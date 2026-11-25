"""`python -m app.services.agent` — run the shopping agent once, from a terminal.

Used by `make agent` and by the demo script. Writes to the real ledger, so a run from
here is as auditable as one through the API.
"""

from __future__ import annotations

import argparse
import sys

from sqlmodel import Session

from app.api.routes_check import run_check
from app.db import create_db_and_tables, get_engine
from app.models.cart import Cart
from app.models.common import LedgerKind, utc_now
from app.models.store import MandateRow
from app.services.agent.faults import FaultMode
from app.services.agent.shopping_agent import AgentMode, ShoppingAgent
from app.services.engine.aggregator import get_engine as get_conformance_engine
from app.services.ledger import LedgerService
from app.services.mandate_builder import MandateBuilder, get_mandate_signer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent", description="Run one shopping-agent pass")
    parser.add_argument("--instruction", "-i", required=True)
    parser.add_argument("--user-id", default="u_demo")
    parser.add_argument("--mode", default="normal", choices=[m.value for m in AgentMode])
    parser.add_argument("--fault", default=None, choices=[f.value for f in FaultMode])
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)

    create_db_and_tables()

    built = MandateBuilder().build(args.instruction, args.user_id)
    mandate = built.mandate.model_copy(
        update={"signature_jws": get_mandate_signer().sign(built.mandate)}
    )

    with Session(get_engine()) as session:
        ledger = LedgerService(session)
        mandate_seq = ledger.append(LedgerKind.MANDATE_SIGNED, mandate).seq

        row = MandateRow.from_domain(mandate)
        row.signed = True
        session.add(row)
        session.commit()

        agent = ShoppingAgent(
            mode=AgentMode(args.mode),
            fault=FaultMode(args.fault) if args.fault else None,
            seed=args.seed,
        )

        def checker(cart: Cart):  # noqa: ANN202
            mandate_row = session.get(MandateRow, mandate.mandate_id)
            assert mandate_row is not None
            return run_check(session, mandate_row, cart, get_conformance_engine())

        run = agent.run(mandate, checker)

        payment_seq = None
        if run.paid:
            payment_seq = ledger.append(
                LedgerKind.PAYMENT,
                {
                    "cart_id": run.final.cart.cart_id,
                    "mandate_id": mandate.mandate_id,
                    "amount_paise": run.final.cart.computed_total_paise,
                    "merchant_id": run.final.cart.merchant_id,
                    "paid_at": utc_now().isoformat(),
                },
            ).seq

        _report(args, mandate, run, mandate_seq, payment_seq)
    return 0


def _rupees(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


def _report(args, mandate, run, mandate_seq, payment_seq) -> None:  # noqa: ANN001
    line = "─" * 72
    print(f"\n{line}\n  INSTRUCTION  {args.instruction}")
    print(f"  MODE         {args.mode}" + (f"   FAULT  {args.fault}" if args.fault else ""))
    print(f"  MANDATE      {mandate.mandate_id}  (ledger seq {mandate_seq})")
    print(line)

    for record in run.attempts:
        cart = record.cart
        print(f"\n  ── attempt {record.attempt} ──")
        if record.fault:
            print(f"  fault injected: {record.fault} — {record.fault_note}")
        print("  cart:")
        for item in cart.items:
            print(
                f"    {item.qty} x {item.title}  [{item.sku}]  "
                f"{_rupees(item.unit_price_paise)}"
                + ("  (SUBSCRIPTION)" if item.is_subscription else "")
            )
        if cart.shipping_paise:
            print(f"    + delivery {_rupees(cart.shipping_paise)}")
        if cart.tax_paise:
            print(f"    + tax {_rupees(cart.tax_paise)}")
        print(f"    = {_rupees(cart.computed_total_paise)}  at {cart.merchant_id}")

        verdict = record.verdict
        print(f"\n  VERDICT: {verdict.decision}   (ledger seq {record.ledger_seqs})")
        for result in verdict.results:
            if result.status in ("violated", "unclear"):
                print(f"    {result.status.upper():9} L{result.layer}  {result.rationale}")
        if record.step_up:
            print(f"\n  STEP-UP: {record.step_up.question_text}")
        print(f"  ACTION: {record.action}")

    print(f"\n{line}")
    print(f"  FINAL: {run.final_decision}   paid={run.paid}   awaiting_user={run.awaiting_user}")
    if payment_seq:
        print(f"  payment written at ledger seq {payment_seq}")
    print(f"  tool calls: {len(run.tool_calls)}")
    print(f"{line}\n")


if __name__ == "__main__":
    sys.exit(main())

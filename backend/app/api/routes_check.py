"""POST /check — the conformance call an agent platform or PSP makes before paying.

Runs at cart creation, *before* payment authorisation. Not inside the authorisation path,
which has about a second to complete.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.db import get_session
from app.models.cart import Cart, CartItem
from app.models.common import LedgerKind, utc_now
from app.models.store import CartRow, MandateRow, StepUpRow, VerdictRow
from app.models.verdict import StepUp, Verdict
from app.services.catalogue import cart_item_from_product, get_catalogue
from app.services.engine.aggregator import CheckOutcome, ConformanceEngine, get_engine
from app.services.ledger import LedgerService
from app.services.mandate_builder import get_mandate_signer

router = APIRouter(tags=["check"])

SessionDep = Annotated[Session, Depends(get_session)]


class CartItemRef(BaseModel):
    """A line referring to a catalogue SKU, which is how a real agent builds a cart."""

    sku: str
    qty: int = Field(default=1, ge=1)


class CartRequest(BaseModel):
    merchant_id: str | None = None
    items: list[CartItemRef] = Field(default_factory=list)
    raw_items: list[CartItem] = Field(
        default_factory=list,
        description="Lines not drawn from our catalogue, supplied whole.",
    )
    shipping_paise: int = Field(default=0, ge=0)
    tax_paise: int = Field(default=0, ge=0)
    delivery_eta: datetime | None = None
    delivery_days: int | None = Field(default=None, ge=0, le=90)
    created_by_agent_id: str | None = None


class CheckRequest(BaseModel):
    mandate_id: str
    cart: CartRequest


class CheckResponse(BaseModel):
    verdict: Verdict
    step_up: StepUp | None = None
    cart: Cart
    l3_call_rate: float
    l3_calls: int
    l3_failures: int
    ledger_seqs: list[int] = Field(default_factory=list)


def build_cart(request: CartRequest, mandate_id: str) -> Cart:
    """Resolve SKU references against the catalogue and total the cart honestly."""
    catalogue = get_catalogue()
    items: list[CartItem] = list(request.raw_items)

    for ref in request.items:
        product = catalogue.get(ref.sku)
        if product is None:
            raise HTTPException(status_code=422, detail=f"unknown sku {ref.sku}")
        items.append(cart_item_from_product(product, qty=ref.qty))

    if not items:
        raise HTTPException(status_code=422, detail="the cart is empty")

    merchant_id = request.merchant_id
    if merchant_id is None:
        product = catalogue.get(items[0].sku)
        merchant_id = product.merchant_id if product else "m_unknown"

    eta = request.delivery_eta
    if eta is None and request.delivery_days is not None:
        eta = utc_now() + timedelta(days=request.delivery_days)

    cart = Cart(
        mandate_id=mandate_id,
        merchant_id=merchant_id,
        items=items,
        shipping_paise=request.shipping_paise,
        tax_paise=request.tax_paise,
        delivery_eta=eta,
        created_by_agent_id=request.created_by_agent_id,
    )
    return cart.with_recomputed_total()


def run_check(
    session: Session,
    mandate_row: MandateRow,
    cart: Cart,
    engine: ConformanceEngine,
) -> tuple[CheckOutcome, list[int]]:
    """Check the cart, then write the cart and the verdict to the ledger."""
    mandate = mandate_row.to_domain()
    verified = get_mandate_signer().verify(mandate) if mandate.is_signed else None

    outcome = engine.check(mandate, cart, uses_consumed=mandate_row.uses_consumed,
                           signature_verified=verified)

    ledger = LedgerService(session)
    seqs: list[int] = []
    for kind, payload in (
        (LedgerKind.CART_CREATED, cart),
        (LedgerKind.VERDICT, outcome.verdict),
    ):
        entry = ledger.append(kind, payload)
        if entry.seq is not None:
            seqs.append(entry.seq)

    session.add(CartRow.from_domain(cart))
    session.add(VerdictRow.from_domain(outcome.verdict))
    if outcome.step_up is not None:
        session.add(StepUpRow.from_domain(outcome.step_up))
    session.commit()

    return outcome, seqs


@router.post("/check", response_model=CheckResponse)
def check(body: CheckRequest, session: SessionDep) -> CheckResponse:
    mandate_row = session.get(MandateRow, body.mandate_id)
    if mandate_row is None:
        raise HTTPException(status_code=404, detail=f"no mandate {body.mandate_id}")

    cart = build_cart(body.cart, body.mandate_id)
    outcome, seqs = run_check(session, mandate_row, cart, get_engine())

    return CheckResponse(
        verdict=outcome.verdict,
        step_up=outcome.step_up,
        cart=cart,
        l3_call_rate=outcome.l3_call_rate,
        l3_calls=outcome.l3_calls,
        l3_failures=outcome.l3_failures,
        ledger_seqs=seqs,
    )

import { useEffect, useState } from "react";
import {
  api,
  ApiError,
  titleCase,
  type DisputeResponse,
  type LedgerEntry,
} from "../lib/api";
import {
  Button,
  DecisionBadge,
  Empty,
  ErrorNote,
  Field,
  Panel,
  Spinner,
  inputClass,
} from "../components/ui";

const CLAIMS = [
  "My agent signed me up for a subscription. I only wanted one pair.",
  "I never ordered this — the agent bought the wrong thing.",
  "It never arrived.",
  "I changed my mind, I do not want these.",
];

const ALLOCATION_TINT: Record<string, string> = {
  user: "text-mute",
  agent_operator: "text-violate",
  merchant: "text-ambiguous",
  issuer_fraud: "text-violate",
};

export function Disputes() {
  const [carts, setCarts] = useState<{ cartId: string; label: string }[]>([]);
  const [cartId, setCartId] = useState("");
  const [claim, setClaim] = useState(CLAIMS[0]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<DisputeResponse | null>(null);

  useEffect(() => {
    api
      .ledger()
      .then((entries: LedgerEntry[]) => {
        const seen = new Map<string, string>();
        for (const entry of entries) {
          if (entry.kind !== "cart_created") continue;
          const payload = entry.payload as Record<string, unknown>;
          const id = String(payload.cart_id ?? "");
          const items = (payload.items as { title?: string }[]) ?? [];
          if (id) seen.set(id, items[0]?.title ?? id);
        }
        const options = [...seen].map(([id, label]) => ({ cartId: id, label }));
        setCarts(options.reverse());
        if (options.length > 0) setCartId(options[0].cartId);
      })
      .catch(() => setCarts([]));
  }, []);

  const open = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.openDispute(cartId, claim));
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const pack = result?.pack;

  return (
    <div className="space-y-6">
      <Panel
        title="Open a dispute"
        hint="The claim arrives in the cardholder's words. Everything that answers it is already on the ledger, written before anyone knew there would be a dispute."
      >
        <div className="space-y-5">
          <Field label="Purchase" hint="Carts the ledger has on record.">
            <select
              value={cartId}
              onChange={(e) => setCartId(e.target.value)}
              className={inputClass}
            >
              {carts.length === 0 && <option value="">no carts yet — run a checkout</option>}
              {carts.map((cart) => (
                <option key={cart.cartId} value={cart.cartId}>
                  {cart.label} — {cart.cartId.slice(0, 14)}…
                </option>
              ))}
            </select>
          </Field>

          <Field label="What the cardholder says">
            <textarea
              rows={2}
              value={claim}
              onChange={(e) => setClaim(e.target.value)}
              className={`${inputClass} resize-none`}
            />
          </Field>

          <div className="flex flex-wrap gap-2">
            {CLAIMS.map((text) => (
              <button
                key={text}
                onClick={() => setClaim(text)}
                className="pill border border-edge bg-raised-2 px-3.5 py-1.5 text-xs
                  text-mute transition-colors hover:border-lime/50 hover:text-chalk"
              >
                {text.length > 42 ? `${text.slice(0, 42)}…` : text}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-3">
            <Button onClick={open} disabled={busy || !cartId}>
              {busy ? "Assembling…" : "Build evidence pack"}
            </Button>
            {busy && <Spinner label="reading the ledger" />}
          </div>
        </div>
      </Panel>

      {error && <ErrorNote message={error} />}
      {!pack && !error && <Empty>Pick a purchase and a claim to assemble a pack.</Empty>}

      {pack && (
        <>
          <Panel
            title="Proposed resolution"
            action={
              pack.decision_on_record && (
                <DecisionBadge decision={pack.decision_on_record} />
              )
            }
          >
            <p className="mb-5 text-sm italic leading-relaxed text-mute">
              “{pack.claim_text}”
            </p>

            <div className="grid gap-px overflow-hidden rounded-2xl bg-edge-soft sm:grid-cols-2">
              <div className="bg-ground p-5">
                <p className="text-xs font-semibold uppercase tracking-wider text-faint">
                  Scenario
                </p>
                <p className="mt-1.5 text-lg font-semibold text-chalk">
                  {titleCase(pack.scenario)}
                </p>
                <p className="mt-2 text-sm leading-relaxed text-mute">
                  {pack.scenario_explanation}.
                </p>
              </div>
              <div className="bg-ground p-5">
                <p className="text-xs font-semibold uppercase tracking-wider text-faint">
                  Proposed allocation
                </p>
                <p
                  className={`mt-1.5 text-lg font-semibold ${
                    ALLOCATION_TINT[pack.allocation] ?? "text-chalk"
                  }`}
                >
                  {titleCase(pack.allocation)}
                </p>
                <dl className="mono mt-3 space-y-1 text-[11px] text-faint">
                  <div>
                    Visa {pack.visa_code} — {pack.visa_label}
                  </div>
                  <div>
                    MC {pack.mc_code} — {pack.mc_label}
                  </div>
                </dl>
              </div>
            </div>

            <div className="mt-5 flex flex-wrap items-center gap-3">
              <a
                href={api.pdfUrl(pack.dispute.dispute_id)}
                target="_blank"
                rel="noreferrer"
                className="pill bg-lime px-5 py-2.5 text-sm font-semibold text-ink
                  transition-colors hover:bg-lime-dim"
              >
                Download evidence pack (PDF)
              </a>
              <span
                className={`pill border px-3.5 py-1.5 text-[11px] font-semibold ${
                  pack.chain_ok
                    ? "border-conform/35 bg-conform/10 text-conform"
                    : "border-violate/35 bg-violate/10 text-violate"
                }`}
              >
                {pack.chain_ok
                  ? "chain verified"
                  : `chain broken at #${pack.chain_broken_at}`}
              </span>
            </div>
          </Panel>

          <Panel
            title="What the ledger holds"
            hint="Each line is bound to the one before it by a signed hash chain."
          >
            <ol className="space-y-px overflow-hidden rounded-2xl bg-edge-soft">
              {pack.items.map((item, index) => (
                <li key={index} className="flex gap-4 bg-raised px-4 py-3.5">
                  <span className="mono w-10 shrink-0 pt-0.5 text-xs text-faint">
                    {item.ledger_seq !== null ? `#${item.ledger_seq}` : "—"}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-chalk">{item.headline}</p>
                    {item.detail && (
                      <p className="mt-0.5 text-[13px] leading-relaxed text-mute">
                        {item.detail}
                      </p>
                    )}
                    {item.entry_hash && (
                      <p className="mono mt-1 text-[10px] text-faint">
                        {item.entry_hash.slice(0, 24)}…
                      </p>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          </Panel>

          <Panel title="Stated on the pack itself">
            <p className="text-sm leading-relaxed text-mute">{pack.caveat}</p>
          </Panel>
        </>
      )}
    </div>
  );
}

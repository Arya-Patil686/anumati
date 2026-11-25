import { useCallback, useEffect, useState } from "react";
import {
  api,
  ApiError,
  shortHash,
  titleCase,
  type LedgerEntry,
  type VerifyResult,
} from "../lib/api";
import { Button, Empty, ErrorNote, Panel, Spinner } from "../components/ui";

const KIND_TINT: Record<string, string> = {
  mandate_signed: "text-lime",
  cart_created: "text-mute",
  verdict: "text-chalk",
  step_up: "text-ambiguous",
  payment: "text-conform",
  dispute_opened: "text-violate",
};

function summarise(entry: LedgerEntry): string {
  const payload = entry.payload as Record<string, unknown>;
  switch (entry.kind) {
    case "mandate_signed":
      return `"${payload.natural_language ?? ""}"`;
    case "cart_created": {
      const items = (payload.items as { title?: string; qty?: number }[]) ?? [];
      return items.map((i) => `${i.qty} × ${i.title}`).join(", ");
    }
    case "verdict":
      return String(payload.decision ?? "");
    case "step_up":
      return `answered ${payload.user_answer ?? "—"}`;
    case "payment":
      return `${payload.amount_paise ?? 0} paise to ${payload.merchant_id ?? ""}`;
    case "dispute_opened":
      return String(payload.claim_text ?? "");
    default:
      return "";
  }
}

export function Ledger() {
  const [entries, setEntries] = useState<LedgerEntry[]>([]);
  const [verify, setVerify] = useState<VerifyResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const [rows, result] = await Promise.all([api.ledger(), api.verify()]);
      setEntries(rows);
      setVerify(result);
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="space-y-6">
      <Panel
        title="Decision ledger"
        hint="Every mandate, cart, verdict, answer and payment, in the order it happened."
        action={
          <div className="flex items-center gap-3">
            {busy && <Spinner />}
            <Button variant="ghost" onClick={load}>
              Re-verify
            </Button>
          </div>
        }
      >
        {verify && (
          <div
            className={`mb-5 rounded-2xl border px-5 py-4 ${
              verify.ok
                ? "border-conform/30 bg-conform/8"
                : "border-violate/35 bg-violate/10"
            }`}
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p
                  className={`text-sm font-semibold ${
                    verify.ok ? "text-conform" : "text-violate"
                  }`}
                >
                  {verify.ok
                    ? `Chain verified — ${verify.entries_checked} entries`
                    : `Chain broken at entry #${verify.broken_at}`}
                </p>
                {verify.reason && (
                  <p className="mt-1 text-xs text-violate/90">{verify.reason}</p>
                )}
              </div>
              <div className="mono text-right text-[11px] text-faint">
                <div>head {shortHash(verify.head_hash, 12)}</div>
                <div>key {shortHash(verify.public_key_hex, 12)}</div>
              </div>
            </div>
          </div>
        )}

        {error && <ErrorNote message={error} />}

        {!error && entries.length === 0 && !busy && (
          <Empty>
            The ledger is empty. Run a checkout and every decision will land here.
          </Empty>
        )}

        {entries.length > 0 && (
          <div className="overflow-hidden rounded-2xl border border-edge-soft">
            {entries.map((entry) => {
              const broken = verify && !verify.ok && verify.broken_at === entry.seq;
              return (
                <div
                  key={entry.seq}
                  className={`border-b border-edge-soft last:border-0 ${
                    broken ? "bg-violate/10" : ""
                  }`}
                >
                  <button
                    onClick={() => setOpen(open === entry.seq ? null : entry.seq)}
                    className="flex w-full items-start gap-4 px-4 py-3 text-left transition-colors hover:bg-raised-2"
                  >
                    <span className="mono w-10 shrink-0 pt-0.5 text-xs text-faint">
                      #{entry.seq}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span
                        className={`mono text-xs font-semibold ${
                          KIND_TINT[entry.kind] ?? "text-mute"
                        }`}
                      >
                        {titleCase(entry.kind)}
                      </span>
                      <span className="mt-0.5 block truncate text-sm text-mute">
                        {summarise(entry)}
                      </span>
                    </span>
                    <span className="mono hidden shrink-0 pt-0.5 text-[11px] text-faint sm:block">
                      {shortHash(entry.entry_hash, 10)}
                    </span>
                  </button>

                  {open === entry.seq && (
                    <div className="space-y-3 border-t border-edge-soft bg-ground px-4 py-4">
                      <dl className="mono grid gap-1 text-[11px] text-faint">
                        <div className="flex gap-2">
                          <dt className="w-20 shrink-0">prev</dt>
                          <dd className="truncate">{entry.prev_hash}</dd>
                        </div>
                        <div className="flex gap-2">
                          <dt className="w-20 shrink-0">payload</dt>
                          <dd className="truncate">{entry.payload_hash}</dd>
                        </div>
                        <div className="flex gap-2">
                          <dt className="w-20 shrink-0">entry</dt>
                          <dd className="truncate text-lime">{entry.entry_hash}</dd>
                        </div>
                        <div className="flex gap-2">
                          <dt className="w-20 shrink-0">signature</dt>
                          <dd className="truncate">{entry.signature}</dd>
                        </div>
                      </dl>
                      <pre className="mono max-h-64 overflow-auto rounded-xl border border-edge-soft bg-ink px-3 py-2.5 text-[11px] leading-relaxed text-mute">
                        {JSON.stringify(entry.payload, null, 2)}
                      </pre>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Panel>

      <Panel title="Try to rewrite history" hint="The tamper test, in your own terminal.">
        <p className="text-sm leading-relaxed text-mute">
          Edit a stored payload directly in SQLite, then press{" "}
          <strong className="text-chalk">Re-verify</strong> above. Verification walks the
          chain from entry 1 and stops at the first entry that no longer holds up.
        </p>
        <pre className="mono mt-4 overflow-x-auto rounded-2xl border border-edge-soft bg-ground px-4 py-3.5 text-[11px] leading-relaxed text-mute">
{`sqlite3 backend/anumati.db \\
  "UPDATE ledger_entries SET payload='{\\"tampered\\":true}' WHERE seq=3;"`}
        </pre>
        <p className="mt-4 text-sm leading-relaxed text-mute">
          Editing the payload breaks the hash. Editing the payload{" "}
          <em className="not-italic text-chalk">and</em> recomputing its hash breaks the
          signature instead, because the signing key is not in the database. Both are
          checked, in that order.
        </p>
      </Panel>
    </div>
  );
}

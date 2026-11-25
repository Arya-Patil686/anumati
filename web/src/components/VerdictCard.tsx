/** One verdict, with the reason for every constraint that did not simply pass.
 *
 * A verdict without reasons is not evidence, so the reasons are the body of the card,
 * not a detail behind a toggle.
 */

import type { ConstraintResult, IntentMandate, Verdict } from "../lib/api";
import { shortHash, titleCase } from "../lib/api";
import { DecisionBadge, StatusDot } from "./ui";

const LAYER_LABEL: Record<number, string> = {
  1: "L1 rules",
  2: "L2 attributes",
  3: "L3 judge",
};

function constraintLabel(
  result: ConstraintResult,
  mandate?: IntentMandate | null,
): string {
  if (result.constraint_id.startsWith("mandate.") || result.constraint_id.startsWith("cart.")) {
    return titleCase(result.constraint_id.replace(".", " "));
  }
  const constraint = mandate?.constraints.find((c) => c.id === result.constraint_id);
  return constraint ? titleCase(constraint.type) : result.constraint_id;
}

export function VerdictCard({
  verdict,
  mandate,
  ledgerSeqs,
}: {
  verdict: Verdict;
  mandate?: IntentMandate | null;
  ledgerSeqs?: number[];
}) {
  const notable = verdict.results.filter((r) => r.status !== "satisfied");
  const passed = verdict.results.length - notable.length;

  return (
    <div className="card rise overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-edge-soft px-6 py-4">
        <DecisionBadge decision={verdict.decision} size="lg" />
        <div className="mono text-right text-[11px] leading-relaxed text-faint">
          <div>
            {verdict.latency_ms < 1 ? "<1" : verdict.latency_ms} ms · engine{" "}
            {verdict.engine_version}
          </div>
          <div>prompt {shortHash(verdict.prompt_hash, 8)}</div>
        </div>
      </div>

      <div className="divide-y divide-edge-soft">
        {notable.length === 0 ? (
          <p className="px-6 py-5 text-sm text-mute">
            Every constraint in the signed mandate was satisfied.
          </p>
        ) : (
          notable.map((result) => (
            <div key={result.constraint_id} className="flex gap-3 px-6 py-4">
              <span className="pt-1.5">
                <StatusDot status={result.status} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="text-sm font-semibold text-chalk">
                    {constraintLabel(result, mandate)}
                  </span>
                  <span className="mono text-[11px] uppercase tracking-wider text-faint">
                    {result.status} · {LAYER_LABEL[result.layer]} ·{" "}
                    {(result.confidence * 100).toFixed(0)}%
                  </span>
                  {!result.hard && (
                    <span className="pill bg-raised-2 px-2 py-0.5 text-[10px] text-mute">
                      preference
                    </span>
                  )}
                </div>
                {result.rationale && (
                  <p className="mt-1 text-sm leading-relaxed text-mute">{result.rationale}</p>
                )}
                {result.evidence_fields.length > 0 && (
                  <p className="mono mt-1.5 text-[11px] text-faint">
                    from {result.evidence_fields.join(", ")}
                  </p>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-edge-soft px-6 py-3 text-[11px] text-faint">
        <span>
          {passed} of {verdict.results.length} constraints satisfied
        </span>
        {ledgerSeqs && ledgerSeqs.length > 0 && (
          <span className="mono">ledger #{ledgerSeqs.join(", #")}</span>
        )}
      </div>
    </div>
  );
}

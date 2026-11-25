import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type MetricsResponse, type VerifyResult } from "../lib/api";
import { Panel, Stat } from "../components/ui";

const FLOW = [
  {
    step: "01",
    title: "Intent",
    body: "The user types an instruction. It becomes a typed mandate with a source span on every constraint, read back in plain words, and signed.",
  },
  {
    step: "02",
    title: "Shopping",
    body: "The agent searches the catalogue and builds a cart on the user's behalf. Nobody is watching it do this.",
  },
  {
    step: "03",
    title: "Check",
    body: "Before checkout, ANUMATI compares the cart with the signed mandate and returns a verdict with a reason for every constraint.",
  },
  {
    step: "04",
    title: "Act",
    body: "Conforms and it pays. Violates and it is blocked, with reasons it can retry on. Ambiguous and the user gets one short question.",
  },
  {
    step: "05",
    title: "Record",
    body: "Mandate, cart, verdict and any answer go into a hash-chained, signed ledger. Written before anyone knows there will be a dispute.",
  },
  {
    step: "06",
    title: "Dispute",
    body: "If the purchase is challenged, the ledger becomes an evidence pack with a proposed allocation, in today's card reason codes.",
  },
];

export function Console() {
  const [verify, setVerify] = useState<VerifyResult | null>(null);
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);

  useEffect(() => {
    api.verify().then(setVerify).catch(() => setVerify(null));
    api.metrics().then(setMetrics).catch(() => setMetrics(null));
  }, []);

  const test = metrics?.rows.find(
    (r) => r.system === "ANUMATI" && r.split === "test",
  );
  const pct = (value: unknown) =>
    typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—";

  return (
    <div className="space-y-6">
      <section className="card rise overflow-hidden px-6 py-10 sm:px-10 sm:py-14">
        <p className="mb-5 inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-lime">
          <span>✳</span> Conformance for AI-agent payments
        </p>

        <h1 className="display max-w-3xl text-3xl font-bold leading-[1.12] sm:text-5xl">
          Checking that what an agent bought is what you
          <span className="marker mx-2 inline-block">actually</span>
          permitted
        </h1>

        <p className="mt-6 max-w-2xl text-[15px] leading-relaxed text-mute">
          AP2, Visa Intelligent Commerce and Mastercard Agent Pay prove{" "}
          <em className="not-italic text-chalk">what the user said</em>. In unattended
          purchases the agent builds the cart itself, and today's checks only verify
          signatures, expiry and price caps. Nothing checks whether the cart actually{" "}
          <em className="not-italic text-chalk">means</em> what was asked for — whether a
          treadmill satisfies "running shoes", or a monthly subscription satisfies "buy
          once".
        </p>

        <div className="mt-8 flex flex-wrap gap-3">
          <Link
            to="/checkout"
            className="pill bg-lime px-6 py-3 text-sm font-semibold text-ink transition-colors hover:bg-lime-dim"
          >
            Run a live check
          </Link>
          <Link
            to="/metrics"
            className="pill border border-edge bg-raised-2 px-6 py-3 text-sm font-semibold text-chalk transition-colors hover:border-lime/50"
          >
            See the benchmark
          </Link>
        </div>
      </section>

      <section className="card grid grid-cols-2 divide-edge-soft px-2 py-5 sm:grid-cols-4 sm:divide-x">
        <Stat value={pct(test?.violation_recall)} label="Violation recall" accent />
        <Stat value={pct(test?.false_block_rate)} label="False-block rate" />
        <Stat value={pct(test?.step_up_rate)} label="Asked the user" />
        <Stat value={pct(test?.macro_f1)} label="Macro-F1" accent />
      </section>

      <Panel
        title="Turning instructions into evidence"
        hint="Six steps. The engine sits at step three, before payment authorisation — not inside it, where there is about a second to work with."
      >
        <div className="grid gap-px overflow-hidden rounded-2xl bg-edge-soft sm:grid-cols-2 lg:grid-cols-3">
          {FLOW.map((item) => (
            <div key={item.step} className="bg-raised p-5">
              <div className="mono mb-2 text-xs font-semibold text-lime">{item.step}</div>
              <h3 className="text-sm font-semibold text-chalk">{item.title}</h3>
              <p className="mt-1.5 text-[13px] leading-relaxed text-mute">{item.body}</p>
            </div>
          ))}
        </div>
      </Panel>

      <div className="grid gap-6 lg:grid-cols-2">
        <Panel
          title="Three verdicts, not two"
          hint="The third one is the point."
        >
          <dl className="space-y-4 text-sm">
            <div className="flex gap-3">
              <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-conform" />
              <div>
                <dt className="font-semibold text-chalk">CONFORMS</dt>
                <dd className="text-mute">
                  Every hard constraint satisfied above the threshold. The agent pays.
                </dd>
              </div>
            </div>
            <div className="flex gap-3">
              <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-violate" />
              <div>
                <dt className="font-semibold text-chalk">VIOLATES</dt>
                <dd className="text-mute">
                  At least one hard constraint clearly broken. Blocked, with the reasons
                  returned to the agent.
                </dd>
              </div>
            </div>
            <div className="flex gap-3">
              <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-ambiguous" />
              <div>
                <dt className="font-semibold text-chalk">AMBIGUOUS</dt>
                <dd className="text-mute">
                  No clear breach, but something a reasonable shopper would hesitate over
                  — off-white against white, 4.9 kg against 5 kg. One tap settles it, and
                  the answer is logged.
                </dd>
              </div>
            </div>
          </dl>
        </Panel>

        <Panel
          title="The decision ledger"
          hint="Append-only, SHA-256 chained, Ed25519 signed."
          action={
            verify && (
              <span
                className={`pill border px-3 py-1 text-[11px] font-semibold ${
                  verify.ok
                    ? "border-conform/35 bg-conform/10 text-conform"
                    : "border-violate/35 bg-violate/10 text-violate"
                }`}
              >
                {verify.ok ? "chain verified" : `broken at #${verify.broken_at}`}
              </span>
            )
          }
        >
          <p className="text-sm leading-relaxed text-mute">
            The hash chain shows no earlier entry was changed after the fact. The
            signature shows the entries came from this service and not from whoever got
            hold of the database file. A dispute pack is worth something only if both
            hold.
          </p>
          <div className="mono mt-4 rounded-2xl border border-edge-soft bg-ground px-4 py-3 text-[11px] leading-relaxed text-faint">
            entry_hash = SHA256(prev_hash ‖ canonical_json(payload))
          </div>
          {verify && (
            <p className="mt-3 text-xs text-faint">
              {verify.entries_checked} entries checked.{" "}
              <Link to="/ledger" className="text-lime hover:underline">
                Try tampering with one →
              </Link>
            </p>
          )}
        </Panel>
      </div>

      <Panel title="Stated plainly" hint="What this does not claim.">
        <ul className="space-y-2 text-sm leading-relaxed text-mute">
          <li>
            — We have no real agent-dispute outcomes, so there is no claim about dispute
            win rates. Production calibration needs a partner's transaction data.
          </li>
          <li>
            — The liability allocation is a proposal. Current card-scheme rules still
            govern who pays.
          </li>
          <li>
            — UPI agent-payment dispute categories are not published yet. Until NPCI
            publishes Unified Agent Protocol rules, packs map to the bank's existing
            complaint flow.
          </li>
          <li>
            — The ledger detects edits by anyone without the signing key. It does not
            defend against someone holding that key, and it does not prove{" "}
            <em className="not-italic text-chalk">when</em> an entry was written.
          </li>
        </ul>
      </Panel>
    </div>
  );
}

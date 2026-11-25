import { useState } from "react";
import {
  api,
  ApiError,
  rupees,
  titleCase,
  type AgentRunResponse,
  type StepUp,
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
import { VerdictCard } from "../components/VerdictCard";
import { PhoneStepUp } from "../components/PhoneStepUp";

const PRESETS = [
  {
    label: "Clean run",
    instruction: "white running shoes under 5000, delivered by Friday",
    mode: "normal",
    fault: "",
    note: "The agent finds a matching pair and pays.",
  },
  {
    label: "Subscription trap",
    instruction: "white running shoes under 5000",
    mode: "faulty",
    fault: "recurrence_flip",
    note: "A recurring plan against a one-off authorisation. Visa 13.2.",
  },
  {
    label: "Wrong category",
    instruction: "white running shoes under 30000",
    mode: "faulty",
    fault: "wrong_category_far",
    note: "The treadmill-for-running-shoes case, inside the budget.",
  },
  {
    label: "Hidden fees",
    instruction: "white running shoes under 5000",
    mode: "faulty",
    fault: "hidden_fees",
    note: "The item fits the cap; the total does not.",
  },
  {
    label: "Hinglish",
    instruction: "5000 se kam ke white running shoes, Friday tak chahiye",
    mode: "normal",
    fault: "",
    note: "Same machinery, code-mixed instruction.",
  },
];

const FAULTS = [
  "price_over",
  "hidden_fees",
  "wrong_category_near",
  "wrong_category_far",
  "attribute_swap",
  "quantity_off",
  "recurrence_flip",
  "merchant_off_list",
  "delivery_late",
  "substitution_unapproved",
  "dietary_violation",
];

export function Checkout() {
  const [instruction, setInstruction] = useState(PRESETS[0].instruction);
  const [mode, setMode] = useState("normal");
  const [fault, setFault] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AgentRunResponse | null>(null);
  const [stepUp, setStepUp] = useState<StepUp | null>(null);
  const [stepUpNote, setStepUpNote] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    setStepUp(null);
    setStepUpNote(null);
    try {
      const response = await api.runAgent(instruction, mode, mode === "faulty" ? fault : null);
      setResult(response);
      const last = response.run.attempts.at(-1);
      if (last?.step_up) setStepUp(last.step_up);
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const answer = async (choice: "APPROVE" | "REJECT") => {
    if (!stepUp) return;
    try {
      const response = await api.answerStepUp(stepUp.verdict_id, choice);
      setStepUpNote(
        choice === "APPROVE"
          ? `Approved and paid. Logged at ledger #${response.ledger_seq}` +
              (response.payment_ledger_seq
                ? `, payment at #${response.payment_ledger_seq}.`
                : ".")
          : `Cancelled. The refusal is logged at ledger #${response.ledger_seq}; no payment was made.`,
      );
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : String(exc));
    }
  };

  const applyPreset = (preset: (typeof PRESETS)[number]) => {
    setInstruction(preset.instruction);
    setMode(preset.mode);
    setFault(preset.fault);
  };

  return (
    <div className="space-y-6">
      <Panel
        title="Run the agent"
        hint="An instruction becomes a signed mandate, the agent builds a cart, and ANUMATI checks it before any money moves."
      >
        <div className="space-y-5">
          <Field label="Instruction" hint="English or Hinglish — both parse the same way.">
            <textarea
              rows={2}
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              className={`${inputClass} resize-none`}
              placeholder="white running shoes under 5000, delivered by Friday"
            />
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Agent behaviour">
              <div className="flex gap-2">
                {["normal", "faulty"].map((option) => (
                  <button
                    key={option}
                    onClick={() => setMode(option)}
                    className={`pill flex-1 px-4 py-2.5 text-sm font-semibold transition-colors ${
                      mode === option
                        ? "bg-lime text-ink"
                        : "border border-edge bg-raised-2 text-mute hover:text-chalk"
                    }`}
                  >
                    {titleCase(option)}
                  </button>
                ))}
              </div>
            </Field>

            <Field
              label="Fault to inject"
              hint={mode === "faulty" ? "Mirrors a benchmark perturbation operator." : "Only used in faulty mode."}
            >
              <select
                value={fault}
                onChange={(e) => setFault(e.target.value)}
                disabled={mode !== "faulty"}
                className={`${inputClass} disabled:opacity-40`}
              >
                <option value="">random</option>
                {FAULTS.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={run} disabled={busy || !instruction.trim()}>
              {busy ? "Running…" : "Run checkout"}
            </Button>
            {busy && <Spinner label="building the cart" />}
          </div>

          <div className="border-t border-edge-soft pt-4">
            <p className="mb-2.5 text-xs font-semibold uppercase tracking-wider text-mute">
              Try one of these
            </p>
            <div className="flex flex-wrap gap-2">
              {PRESETS.map((preset) => (
                <button
                  key={preset.label}
                  onClick={() => applyPreset(preset)}
                  title={preset.note}
                  className="pill border border-edge bg-raised-2 px-3.5 py-1.5 text-xs
                    font-medium text-mute transition-colors hover:border-lime/50 hover:text-chalk"
                >
                  {preset.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </Panel>

      {error && <ErrorNote message={error} />}

      {!result && !error && (
        <Empty>Run a checkout to see the cart, the verdict and the reasons.</Empty>
      )}

      {result && (
        <>
          <Panel title="What the user signed">
            <pre className="mono overflow-x-auto whitespace-pre-wrap rounded-2xl border border-edge-soft bg-ground px-4 py-3.5 text-[12px] leading-relaxed text-mute">
              {result.read_back}
            </pre>
          </Panel>

          {result.run.attempts.map((attempt) => (
            <Panel
              key={attempt.attempt}
              title={`Attempt ${attempt.attempt}`}
              hint={attempt.action}
              action={<DecisionBadge decision={attempt.verdict.decision} />}
            >
              {attempt.fault && (
                <div className="mb-4 rounded-2xl border border-ambiguous/30 bg-ambiguous/10 px-4 py-3 text-sm text-ambiguous">
                  <strong className="font-semibold">Fault injected — {attempt.fault}:</strong>{" "}
                  {attempt.fault_note}
                </div>
              )}

              <div className="mb-5 overflow-hidden rounded-2xl border border-edge-soft">
                {attempt.cart.items.map((item) => (
                  <div
                    key={item.sku}
                    className="flex flex-wrap items-center justify-between gap-2 border-b border-edge-soft px-4 py-3 last:border-0"
                  >
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium text-chalk">
                        {item.qty} × {item.title}
                        {item.is_subscription && (
                          <span className="pill ml-2 bg-ambiguous/15 px-2 py-0.5 text-[10px] font-semibold uppercase text-ambiguous">
                            subscription
                          </span>
                        )}
                      </div>
                      <div className="mono mt-0.5 text-[11px] text-faint">
                        {item.sku} · {item.category_path}
                        {Object.entries(item.attributes).length > 0 &&
                          ` · ${Object.entries(item.attributes)
                            .map(([k, v]) => `${k}: ${v}`)
                            .join(", ")}`}
                      </div>
                    </div>
                    <span className="mono text-sm text-chalk">
                      {rupees(item.line_total_paise)}
                    </span>
                  </div>
                ))}
                <div className="flex items-center justify-between bg-raised-2 px-4 py-3">
                  <span className="text-xs text-mute">
                    including {rupees(attempt.cart.shipping_paise)} delivery and{" "}
                    {rupees(attempt.cart.tax_paise)} tax · {attempt.cart.merchant_id}
                  </span>
                  <span className="mono text-base font-semibold text-chalk">
                    {rupees(attempt.cart.total_paise)}
                  </span>
                </div>
              </div>

              <VerdictCard
                verdict={attempt.verdict}
                ledgerSeqs={attempt.ledger_seqs}
              />
            </Panel>
          ))}

          {stepUp && (
            <Panel
              title="The user is asked"
              hint="Only when the call is genuinely unclear. The answer goes on the ledger."
            >
              <PhoneStepUp stepUp={stepUp} onAnswer={answer} />
              {stepUpNote && (
                <p className="mt-5 text-center text-sm text-lime">{stepUpNote}</p>
              )}
            </Panel>
          )}

          <Panel title="What the agent actually did" hint="Every tool call, in order.">
            <ol className="space-y-2">
              {result.run.tool_calls.map((call, index) => (
                <li
                  key={index}
                  className="flex flex-wrap items-baseline gap-x-3 rounded-xl bg-ground px-4 py-2.5"
                >
                  <span className="mono text-[11px] text-faint">{index + 1}</span>
                  <span className="mono text-xs font-semibold text-lime">{call.tool}</span>
                  <span className="text-xs text-mute">{call.result}</span>
                </li>
              ))}
            </ol>
          </Panel>
        </>
      )}
    </div>
  );
}

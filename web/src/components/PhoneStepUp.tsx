/** The one-tap confirmation, drawn as the phone screen the user would actually see.
 *
 * Deliberately short. A step-up that recites the whole mandate teaches people to tap
 * "approve" without reading, which defeats the point of asking.
 */

import { useState } from "react";
import type { StepUp } from "../lib/api";

export function PhoneStepUp({
  stepUp,
  onAnswer,
  busy,
}: {
  stepUp: StepUp;
  onAnswer?: (answer: "APPROVE" | "REJECT") => void;
  busy?: boolean;
}) {
  const [answered, setAnswered] = useState<"APPROVE" | "REJECT" | null>(
    stepUp.user_answer,
  );

  const respond = (answer: "APPROVE" | "REJECT") => {
    setAnswered(answer);
    onAnswer?.(answer);
  };

  return (
    <div className="mx-auto w-full max-w-[320px]">
      <div className="rounded-[2.5rem] border border-edge bg-ground p-2.5 shadow-2xl shadow-black/60">
        <div className="relative overflow-hidden rounded-[2rem] bg-raised px-5 pb-6 pt-7">
          <div className="absolute left-1/2 top-2.5 h-1.5 w-20 -translate-x-1/2 rounded-full bg-edge" />

          <div className="mb-5 flex items-center gap-2">
            <span className="text-lime">✳</span>
            <span className="display text-sm font-bold tracking-tight">ANUMATI</span>
            <span className="ml-auto text-[10px] text-faint">now</span>
          </div>

          <p className="text-[11px] font-semibold uppercase tracking-wider text-lime">
            One quick check
          </p>
          <p className="mt-2 text-[15px] leading-relaxed text-chalk">
            {stepUp.question_text}
          </p>

          {answered ? (
            <div
              className={`pill mt-6 w-full py-3 text-center text-sm font-semibold ${
                answered === "APPROVE"
                  ? "bg-conform/15 text-conform"
                  : "bg-violate/15 text-violate"
              }`}
            >
              You tapped {answered === "APPROVE" ? "Yes, buy it" : "No, cancel"}
            </div>
          ) : (
            <div className="mt-6 space-y-2.5">
              <button
                disabled={busy}
                onClick={() => respond("APPROVE")}
                className="pill pulse-ring w-full bg-lime py-3 text-sm font-semibold
                  text-ink transition-colors hover:bg-lime-dim disabled:opacity-50"
              >
                Yes, buy it
              </button>
              <button
                disabled={busy}
                onClick={() => respond("REJECT")}
                className="pill w-full border border-edge bg-raised-2 py-3 text-sm
                  font-semibold text-chalk transition-colors hover:border-violate/50
                  disabled:opacity-50"
              >
                No, cancel
              </button>
            </div>
          )}

          <p className="mt-4 text-center text-[10px] leading-relaxed text-faint">
            Your answer is written to the decision ledger. It is what defends you if this
            purchase is ever disputed.
          </p>
        </div>
      </div>
    </div>
  );
}

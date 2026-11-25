/** Shared primitives. Kept small so every screen looks like the same product. */

import type { ReactNode } from "react";
import type { Decision } from "../lib/api";

export function Panel({
  title,
  hint,
  action,
  children,
  className = "",
}: {
  title?: string;
  hint?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card p-6 sm:p-7 ${className}`}>
      {(title || action) && (
        <header className="mb-5 flex items-start justify-between gap-4">
          <div>
            {title && <h2 className="text-lg font-semibold text-chalk">{title}</h2>}
            {hint && <p className="mt-1 max-w-prose text-sm text-mute">{hint}</p>}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function Button({
  children,
  onClick,
  variant = "primary",
  disabled,
  type = "button",
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "ghost" | "danger";
  disabled?: boolean;
  type?: "button" | "submit";
  className?: string;
}) {
  const styles = {
    primary: "bg-lime text-ink hover:bg-lime-dim",
    ghost: "bg-raised-2 text-chalk border border-edge hover:border-lime/50",
    danger: "bg-violate/15 text-violate border border-violate/35 hover:bg-violate/25",
  }[variant];

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`pill inline-flex items-center justify-center gap-2 px-5 py-2.5 text-sm
        font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40
        ${styles} ${className}`}
    >
      {children}
    </button>
  );
}

const DECISION_STYLES: Record<Decision, string> = {
  CONFORMS: "bg-conform/12 text-conform border-conform/35",
  VIOLATES: "bg-violate/12 text-violate border-violate/35",
  AMBIGUOUS: "bg-ambiguous/12 text-ambiguous border-ambiguous/35",
};

export function DecisionBadge({
  decision,
  size = "md",
}: {
  decision: Decision;
  size?: "sm" | "md" | "lg";
}) {
  const scale = {
    sm: "px-2.5 py-0.5 text-[11px]",
    md: "px-3.5 py-1 text-xs",
    lg: "px-5 py-2 text-sm",
  }[size];

  return (
    <span
      className={`pill inline-flex items-center gap-2 border font-mono font-semibold
        uppercase tracking-wider ${DECISION_STYLES[decision]} ${scale}`}
    >
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-current" />
      {decision}
    </span>
  );
}

export function StatusDot({ status }: { status: string }) {
  const colour =
    status === "satisfied"
      ? "bg-conform"
      : status === "violated"
        ? "bg-violate"
        : status === "unclear"
          ? "bg-ambiguous"
          : "bg-faint";
  return <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${colour}`} />;
}

export function Stat({
  value,
  label,
  accent = false,
}: {
  value: string;
  label: string;
  accent?: boolean;
}) {
  return (
    <div className="px-4 py-1 text-center sm:text-left">
      <div className="display text-2xl font-bold sm:text-3xl">
        <span className={accent ? "text-lime" : "text-chalk"}>{value}</span>
      </div>
      <div className="mt-0.5 text-xs text-mute">{label}</div>
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-mute">
        {label}
      </span>
      {children}
      {hint && <span className="mt-1.5 block text-xs text-faint">{hint}</span>}
    </label>
  );
}

export const inputClass =
  "w-full rounded-2xl border border-edge bg-ground px-4 py-3 text-sm text-chalk " +
  "placeholder:text-faint outline-none transition-colors focus:border-lime/60";

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-2xl border border-dashed border-edge px-6 py-10 text-center text-sm text-faint">
      {children}
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div className="rounded-2xl border border-violate/35 bg-violate/10 px-4 py-3 text-sm text-violate">
      {message}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-mute">
      <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-edge border-t-lime" />
      {label}
    </span>
  );
}

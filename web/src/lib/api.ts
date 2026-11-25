/**
 * The one place the console talks to the API.
 *
 * Every call returns parsed JSON or throws an ApiError carrying the server's own
 * message, so screens can show what actually went wrong instead of "something failed".
 */

import { DEMO, demoPackUrl, demoRequest } from "./demo";

const BASE = import.meta.env.VITE_API_BASE ?? "/api";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // The hosted build has no API behind it: it replays a recorded session instead.
  if (DEMO) {
    try {
      return (await demoRequest(path, init)) as T;
    } catch (exc) {
      const status = (exc as { status?: number }).status ?? 0;
      throw new ApiError(exc instanceof Error ? exc.message : String(exc), status);
    }
  }

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError("Cannot reach the ANUMATI API. Is `make dev` running?", 0);
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : detail;
    } catch {
      /* the body was not JSON; the status line is all we have */
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

const post = <T,>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

// --- types mirroring the Pydantic models -------------------------------------

export type Decision = "CONFORMS" | "VIOLATES" | "AMBIGUOUS";
export type ConstraintStatus = "satisfied" | "violated" | "unclear" | "not_decidable_here";

export interface Constraint {
  id: string;
  type: string;
  operator: string;
  value: unknown;
  hard: boolean;
  source_span: string | null;
}

export interface IntentMandate {
  mandate_id: string;
  user_id: string;
  natural_language: string;
  language: string;
  constraints: Constraint[];
  budget_total_paise: number;
  currency: string;
  expires_at: string;
  recurrence: string;
  max_uses: number;
  signature_jws: string | null;
}

export interface CartItem {
  sku: string;
  title: string;
  description: string;
  category_path: string;
  brand: string;
  attributes: Record<string, unknown>;
  unit_price_paise: number;
  qty: number;
  is_subscription: boolean;
  line_total_paise: number;
}

export interface Cart {
  cart_id: string;
  mandate_id: string;
  merchant_id: string;
  items: CartItem[];
  shipping_paise: number;
  tax_paise: number;
  total_paise: number;
  delivery_eta: string | null;
}

export interface ConstraintResult {
  constraint_id: string;
  layer: 1 | 2 | 3;
  status: ConstraintStatus;
  confidence: number;
  evidence_fields: string[];
  rationale: string;
  hard: boolean;
}

export interface Verdict {
  verdict_id: string;
  mandate_id: string;
  cart_id: string;
  decision: Decision;
  results: ConstraintResult[];
  engine_version: string;
  model_id: string | null;
  prompt_hash: string | null;
  latency_ms: number;
  created_at: string;
}

export interface StepUp {
  verdict_id: string;
  question_text: string;
  user_answer: "APPROVE" | "REJECT" | null;
  answered_at: string | null;
}

export interface MandateResponse {
  mandate: IntentMandate;
  read_back: string;
  unparsed_tail: string;
  signed: boolean;
  ledger_seq: number | null;
}

export interface CheckResponse {
  verdict: Verdict;
  step_up: StepUp | null;
  cart: Cart;
  l3_call_rate: number;
  l3_calls: number;
  l3_failures: number;
  ledger_seqs: number[];
}

export interface AttemptRecord {
  attempt: number;
  cart: Cart;
  verdict: Verdict;
  step_up: StepUp | null;
  fault: string | null;
  fault_note: string;
  action: string;
  ledger_seqs: number[];
}

export interface AgentRun {
  mandate_id: string;
  mode: string;
  attempts: AttemptRecord[];
  final_decision: Decision;
  paid: boolean;
  awaiting_user: boolean;
  tool_calls: { tool: string; arguments: Record<string, unknown>; result: string }[];
  narrative: string[];
}

export interface AgentRunResponse {
  run: AgentRun;
  mandate: IntentMandate;
  read_back: string;
  mandate_ledger_seq: number | null;
}

export interface LedgerEntry {
  seq: number;
  ts: string;
  kind: string;
  payload: Record<string, unknown>;
  payload_hash: string;
  prev_hash: string;
  entry_hash: string;
  signature: string;
}

export interface VerifyResult {
  ok: boolean;
  broken_at: number | null;
  reason: string | null;
  entries_checked: number;
  head_hash: string | null;
  public_key_hex: string | null;
}

export interface EvidenceItem {
  ledger_seq: number | null;
  entry_hash: string | null;
  kind: string;
  headline: string;
  detail: string;
}

export interface EvidencePack {
  dispute: {
    dispute_id: string;
    cart_id: string;
    claim_text: string;
    evidence_refs: number[];
    proposed_allocation: string | null;
    visa_code: string | null;
    mc_code: string | null;
  };
  claim_text: string;
  scenario: string;
  scenario_explanation: string;
  allocation: string;
  visa_code: string;
  visa_label: string;
  mc_code: string;
  mc_label: string;
  decision_on_record: Decision | null;
  chain_ok: boolean;
  chain_broken_at: number | null;
  items: EvidenceItem[];
  mandate_read_back: string;
  caveat: string;
}

export interface DisputeResponse {
  pack: EvidencePack;
  pdf_path: string;
  pdf_url: string;
  ledger_seq: number | null;
}

export interface MetricsResponse {
  available: boolean;
  message: string;
  generated_at: string | null;
  dataset: Record<string, unknown>;
  split: Record<string, unknown>;
  notes: string[];
  rows: Record<string, string | number>[];
  per_operator: Record<string, string>[];
}

export interface Health {
  status: string;
  engine_version: string;
  llm_mode: string;
  ledger_public_key: string;
}

// --- calls ---------------------------------------------------------------------

export const api = {
  health: () => request<Health>("/health"),

  createMandate: (instruction: string, user_id = "u_console") =>
    post<MandateResponse>("/mandates", { instruction, user_id }),

  signMandate: (id: string) => post<MandateResponse>(`/mandates/${id}/sign`, {}),

  check: (mandate_id: string, cart: Record<string, unknown>) =>
    post<CheckResponse>("/check", { mandate_id, cart }),

  runAgent: (instruction: string, mode: string, fault?: string | null) =>
    post<AgentRunResponse>("/agent/run", {
      instruction,
      mode,
      fault: fault || null,
    }),

  answerStepUp: (verdictId: string, answer: "APPROVE" | "REJECT") =>
    post<{ step_up: StepUp; ledger_seq: number; payment_ledger_seq: number | null }>(
      `/stepup/${verdictId}`,
      { answer },
    ),

  ledger: (limit = 200) => request<LedgerEntry[]>(`/ledger?limit=${limit}`),

  verify: () => request<VerifyResult>("/ledger/verify"),

  openDispute: (cart_id: string, claim_text: string) =>
    post<DisputeResponse>("/disputes", { cart_id, claim_text }),

  metrics: () => request<MetricsResponse>("/metrics"),

  pdfUrl: (disputeId: string) =>
    (DEMO ? demoPackUrl(disputeId) : null) ?? `${BASE}/disputes/${disputeId}/pack.pdf`,
};

// --- formatting -------------------------------------------------------------------

export function rupees(paise: number): string {
  const value = paise / 100;
  return `₹${value.toLocaleString("en-IN", {
    minimumFractionDigits: value % 1 === 0 ? 0 : 2,
    maximumFractionDigits: 2,
  })}`;
}

export function shortHash(hash: string | null | undefined, length = 10): string {
  return hash ? `${hash.slice(0, length)}…` : "—";
}

export function titleCase(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

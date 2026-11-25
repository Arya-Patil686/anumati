/**
 * The static-demo transport.
 *
 * GitHub Pages serves files, not Python, so the hosted console has no API to call.
 * Rather than mock one, `backend/tools/record_demo.py` drives the real app against a
 * throwaway database and writes down every response; this module replays them. Nothing
 * here invents a verdict, a hash or a number — a request that was not recorded fails
 * loudly instead of being answered.
 */

const BUNDLE_URL = `${import.meta.env.BASE_URL}demo/fixtures.json`;

export const DEMO = import.meta.env.VITE_DEMO === "static";

interface Entry {
  method: string;
  path: string;
  body: unknown;
  status: number;
  response: unknown;
}

interface Bundle {
  recorded_at: string;
  engine_version: string;
  chain_ok: boolean;
  entries_checked: number;
  packs: Record<string, string>;
  entries: Entry[];
}

export class DemoMiss extends Error {
  constructor(readonly method: string, readonly path: string) {
    super(
      "This is a recorded demo, so only the cases on record can answer. " +
        "Pick one of the presets, or run the full stack locally with `docker compose up`.",
    );
    this.name = "DemoMiss";
  }
}

/** Stable key for a request: method, path, and the body with its keys in order. */
function keyOf(method: string, path: string, body: unknown): string {
  return `${method} ${path} ${stable(body)}`;
}

function stable(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([k, v]) => `${JSON.stringify(k)}:${stable(v)}`);
    return `{${entries.join(",")}}`;
  }
  return JSON.stringify(value);
}

let bundlePromise: Promise<Bundle> | null = null;
let index: Map<string, Entry> | null = null;
let loaded: Bundle | null = null;

function load(): Promise<Bundle> {
  if (!bundlePromise) {
    bundlePromise = fetch(BUNDLE_URL)
      .then((response) => {
        if (!response.ok) throw new Error(`fixtures.json: ${response.status}`);
        return response.json() as Promise<Bundle>;
      })
      .then((bundle) => {
        index = new Map();
        for (const entry of bundle.entries) {
          const key = keyOf(entry.method, entry.path, entry.body);
          // First write wins: the recording order is the order a viewer would meet them.
          if (!index.has(key)) index.set(key, entry);
        }
        loaded = bundle;
        return bundle;
      })
      .catch((error) => {
        bundlePromise = null;
        throw error;
      });
  }
  return bundlePromise;
}

/** What the footer and the banner say about this build. */
export function recording(): Bundle | null {
  return loaded;
}

/** Warm the bundle so the banner can show when the session was recorded. */
export function preload(): Promise<unknown> {
  return load().catch(() => null);
}

export async function demoRequest(path: string, init?: RequestInit): Promise<unknown> {
  await load();
  const method = (init?.method ?? "GET").toUpperCase();
  const body = init?.body ? JSON.parse(String(init.body)) : null;

  const entry = index?.get(keyOf(method, path, body));
  if (!entry) throw new DemoMiss(method, path);
  if (entry.status >= 400) {
    const detail = (entry.response as { detail?: string })?.detail;
    throw Object.assign(new Error(detail ?? `${entry.status}`), { status: entry.status });
  }
  return entry.response;
}

/** The evidence PDF the recorder saved for this dispute, if it saved one. */
export function demoPackUrl(disputeId: string): string | null {
  const name = loaded?.packs?.[disputeId];
  return name ? `${import.meta.env.BASE_URL}demo/packs/${name}` : null;
}

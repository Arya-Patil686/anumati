import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, type Health } from "./lib/api";
import { DEMO, preload, recording } from "./lib/demo";
import { Console } from "./pages/Console";
import { Checkout } from "./pages/Checkout";
import { Ledger } from "./pages/Ledger";
import { Disputes } from "./pages/Disputes";
import { Metrics } from "./pages/Metrics";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/checkout", label: "Checkout" },
  { to: "/ledger", label: "Ledger" },
  { to: "/disputes", label: "Disputes" },
  { to: "/metrics", label: "Benchmark" },
];

function Nav() {
  return (
    <nav className="pill mx-auto flex w-full max-w-5xl flex-wrap items-center gap-1 border border-edge-soft bg-raised p-1.5">
      <span className="display flex items-center gap-2 px-4 py-1.5 text-sm font-bold">
        <span className="text-lime">✳</span> ANUMATI
      </span>
      <span className="mx-1 hidden h-5 w-px bg-edge sm:block" />
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          className={({ isActive }) =>
            `pill px-4 py-1.5 text-sm font-medium transition-colors ${
              isActive
                ? "bg-lime text-ink"
                : "text-mute hover:bg-raised-2 hover:text-chalk"
            }`
          }
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}

function DemoBanner() {
  const [when, setWhen] = useState<string | null>(null);

  useEffect(() => {
    preload().then(() => {
      const bundle = recording();
      if (!bundle) return;
      setWhen(new Date(bundle.recorded_at).toLocaleDateString("en-IN", {
        day: "numeric",
        month: "short",
        year: "numeric",
      }));
    });
  }, []);

  return (
    <div className="mx-auto mt-3 w-full max-w-5xl">
      <div className="pill flex flex-wrap items-center justify-center gap-x-3 gap-y-1 border border-lime/25 bg-lime-deep px-4 py-2 text-center text-[12px] leading-relaxed">
        <span className="font-semibold text-lime">Recorded demo</span>
        <span className="text-mute">
          Every verdict, hash and figure here is a real response from this engine
          {when ? `, recorded on ${when}` : ""} — replayed, because a static host has no
          API behind it. Only the cases on record can answer.
        </span>
        <a
          className="font-semibold text-lime underline decoration-lime/40 hover:decoration-lime"
          href="https://github.com/Arya-Patil686/anumati#run-it"
          target="_blank"
          rel="noreferrer"
        >
          Run it live
        </a>
      </div>
    </div>
  );
}

function StatusBar({ health }: { health: Health | null }) {
  return (
    <div className="mono flex flex-wrap items-center justify-center gap-x-5 gap-y-1 text-[11px] text-faint">
      <span className="inline-flex items-center gap-1.5">
        <span
          className={`inline-block h-1.5 w-1.5 rounded-full ${
            health ? "bg-conform" : "bg-violate"
          }`}
        />
        {health
          ? DEMO
            ? "replaying a recorded session"
            : "api connected"
          : DEMO
            ? "recording not loaded"
            : "api unreachable — run `make dev`"}
      </span>
      {health && (
        <>
          <span>engine {health.engine_version}</span>
          <span>llm_mode {health.llm_mode}</span>
          <span>ledger key {health.ledger_public_key.slice(0, 12)}…</span>
        </>
      )}
    </div>
  );
}

export function App() {
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = () =>
      api
        .health()
        .then((h) => alive && setHealth(h))
        .catch(() => alive && setHealth(null));
    poll();
    const timer = setInterval(poll, 15000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="min-h-screen bloom">
      <header className="px-4 pt-5 sm:px-6">
        <Nav />
        {DEMO && <DemoBanner />}
      </header>

      <main className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 sm:py-10">
        <Routes>
          <Route path="/" element={<Console />} />
          <Route path="/checkout" element={<Checkout />} />
          <Route path="/ledger" element={<Ledger />} />
          <Route path="/disputes" element={<Disputes />} />
          <Route path="/metrics" element={<Metrics />} />
        </Routes>
      </main>

      <footer className="mx-auto w-full max-w-5xl space-y-3 px-4 pb-10 sm:px-6">
        <div className="h-px bg-edge-soft" />
        <StatusBar health={health} />
        <p className="text-center text-[11px] leading-relaxed text-faint">
          Liability allocation is a proposal; card-scheme rules govern. No real
          agent-dispute outcomes were used, so no win-rate claim is made.
        </p>
      </footer>
    </div>
  );
}

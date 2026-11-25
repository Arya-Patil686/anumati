import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, type Health } from "./lib/api";
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

function StatusBar({ health }: { health: Health | null }) {
  return (
    <div className="mono flex flex-wrap items-center justify-center gap-x-5 gap-y-1 text-[11px] text-faint">
      <span className="inline-flex items-center gap-1.5">
        <span
          className={`inline-block h-1.5 w-1.5 rounded-full ${
            health ? "bg-conform" : "bg-violate"
          }`}
        />
        {health ? "api connected" : "api unreachable — run `make dev`"}
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

import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type MetricsResponse } from "../lib/api";
import { Empty, Panel, Spinner } from "../components/ui";

const METRICS = [
  { key: "violation_recall", label: "Violation recall", good: "high" },
  { key: "false_block_rate", label: "False-block rate", good: "low" },
  { key: "step_up_rate", label: "Asked the user", good: "neutral" },
  { key: "macro_f1", label: "Macro-F1", good: "high" },
  { key: "hinglish_gap", label: "Hinglish gap", good: "low" },
  { key: "injection_asr", label: "Injection ASR", good: "low" },
  { key: "ece", label: "Calibration error", good: "low" },
] as const;

const LIME = "#b4ff39";
const FAINT = "#3b423b";

function pct(value: unknown): string {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—";
}

export function Metrics() {
  const [data, setData] = useState<MetricsResponse | null>(null);
  const [split, setSplit] = useState<"test" | "dev">("test");
  const [metric, setMetric] = useState<string>("macro_f1");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .metrics()
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <Panel title="Benchmark">
        <Spinner label="loading results" />
      </Panel>
    );
  }

  if (!data?.available) {
    return (
      <Panel title="Benchmark" hint="MCB-IN — Mandate Conformance Benchmark, India">
        <Empty>
          {data?.message ?? "No results yet."}
          <div className="mono mt-3 text-xs text-mute">make bench</div>
        </Empty>
      </Panel>
    );
  }

  const rows = data.rows.filter((r) => r.split === split);
  const chartData = rows.map((r) => ({
    system: String(r.system).replace(/^B\d /, ""),
    full: String(r.system),
    value: typeof r[metric] === "number" ? (r[metric] as number) * 100 : 0,
  }));

  const dataset = data.dataset as Record<string, unknown>;
  const splitInfo = data.split as Record<string, unknown>;

  return (
    <div className="space-y-6">
      <Panel
        title="MCB-IN"
        hint="Mandate Conformance Benchmark, India. Gold labels come from how each case was built — never from any model's output."
        action={
          <div className="flex gap-1.5">
            {(["test", "dev"] as const).map((option) => (
              <button
                key={option}
                onClick={() => setSplit(option)}
                className={`pill px-3.5 py-1.5 text-xs font-semibold transition-colors ${
                  split === option
                    ? "bg-lime text-ink"
                    : "border border-edge bg-raised-2 text-mute hover:text-chalk"
                }`}
              >
                {option}
              </button>
            ))}
          </div>
        }
      >
        <div className="mb-5 grid gap-px overflow-hidden rounded-2xl bg-edge-soft sm:grid-cols-4">
          {[
            ["Cases", String(dataset.cases ?? "—")],
            ["Instructions", String(dataset.instructions ?? "—")],
            [
              "Hinglish",
              typeof dataset.hinglish_share === "number"
                ? `${(dataset.hinglish_share * 100).toFixed(0)}%`
                : "—",
            ],
            ["Held-out operators", String((splitInfo.held_out_operators as string[])?.length ?? 0)],
          ].map(([label, value]) => (
            <div key={label} className="bg-ground px-4 py-3.5">
              <div className="display text-xl font-bold text-chalk">{value}</div>
              <div className="mt-0.5 text-[11px] text-mute">{label}</div>
            </div>
          ))}
        </div>

        <div className="mb-4 flex flex-wrap gap-1.5">
          {METRICS.map((m) => (
            <button
              key={m.key}
              onClick={() => setMetric(m.key)}
              className={`pill px-3 py-1.5 text-xs font-medium transition-colors ${
                metric === m.key
                  ? "bg-raised-2 text-lime ring-1 ring-lime/40"
                  : "text-mute hover:text-chalk"
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>

        <div className="h-72 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 8, right: 8, bottom: 8, left: -12 }}>
              <CartesianGrid strokeDasharray="2 4" stroke="#1b201b" vertical={false} />
              <XAxis
                dataKey="system"
                tick={{ fill: "#9aa69a", fontSize: 12 }}
                axisLine={{ stroke: "#232823" }}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: "#67736a", fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                domain={[0, 100]}
                unit="%"
              />
              <Tooltip
                cursor={{ fill: "rgba(180,255,57,0.06)" }}
                contentStyle={{
                  background: "#121512",
                  border: "1px solid #232823",
                  borderRadius: 12,
                  fontSize: 12,
                }}
                labelStyle={{ color: "#f3f6f2" }}
                formatter={(value: number) => [`${value.toFixed(1)}%`, ""]}
              />
              <Legend wrapperStyle={{ fontSize: 11, color: "#67736a" }} />
              <Bar
                dataKey="value"
                name={METRICS.find((m) => m.key === metric)?.label}
                radius={[8, 8, 0, 0]}
              >
                {chartData.map((entry) => (
                  <Cell
                    key={entry.full}
                    fill={entry.full === "ANUMATI" ? LIME : FAINT}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Panel>

      <Panel title={`Full results — ${split} split`}>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] border-collapse text-sm">
            <thead>
              <tr className="border-b border-edge text-left">
                <th className="py-2.5 pr-4 text-xs font-semibold uppercase tracking-wider text-mute">
                  System
                </th>
                {METRICS.map((m) => (
                  <th
                    key={m.key}
                    className="py-2.5 pr-4 text-xs font-semibold uppercase tracking-wider text-mute"
                  >
                    {m.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const isOurs = row.system === "ANUMATI";
                return (
                  <tr
                    key={String(row.system)}
                    className={`border-b border-edge-soft last:border-0 ${
                      isOurs ? "bg-lime-deep/40" : ""
                    }`}
                  >
                    <td
                      className={`py-3 pr-4 font-medium ${
                        isOurs ? "text-lime" : "text-chalk"
                      }`}
                    >
                      {String(row.system)}
                    </td>
                    {METRICS.map((m) => (
                      <td
                        key={m.key}
                        className={`mono py-3 pr-4 ${isOurs ? "text-chalk" : "text-mute"}`}
                      >
                        {pct(row[m.key])}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="mt-4 text-xs leading-relaxed text-faint">
          Read the shape, not just the headline. B0 is what AP2-style checks do today: it
          rarely blocks a good cart and misses roughly half the violations, because it
          cannot see what was bought. B1 and B2 reach high recall by being suspicious of
          everything — B2 blocks most perfectly good carts, which no payment flow would
          tolerate. Only ANUMATI can say it is unsure instead of guessing.
        </p>
      </Panel>

      {data.notes.length > 0 && (
        <Panel title="Notes on these numbers">
          <ul className="space-y-2 text-sm leading-relaxed text-mute">
            {data.notes.map((note, index) => (
              <li key={index}>— {note.replace(/\*\*/g, "")}</li>
            ))}
          </ul>
          {data.generated_at && (
            <p className="mono mt-4 text-[11px] text-faint">
              generated {data.generated_at} · split{" "}
              {String((splitInfo.held_out_operators as string[])?.join(", "))}
            </p>
          )}
        </Panel>
      )}
    </div>
  );
}

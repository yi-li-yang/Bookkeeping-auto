import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  Cell, PieChart, Pie, Legend, LineChart, Line,
} from "recharts";
import {
  fetchInvestmentSummary, fetchInvestmentHoldings, fetchInvestmentPerformance,
  fetchInvestmentAllocation, fetchInvestmentHistory,
  InvestmentSummary, InvestmentHolding, InvestmentPerformanceRow,
  InvestmentAllocation, InvestmentHistoryRow,
} from "../api/client";

const fmt = (v: number) => `£${v.toLocaleString("en-GB", { maximumFractionDigits: 0 })}`;
const fmtDec = (v: number) => `£${v.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtPct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;

const COLORS = ["#4f86c6", "#60b98a", "#f0b429", "#e8735a", "#9b6dff", "#38bdf8", "#fb7185", "#34d399"];

export default function InvestmentDashboard() {
  const [summary, setSummary] = useState<InvestmentSummary | null>(null);
  const [holdings, setHoldings] = useState<InvestmentHolding[]>([]);
  const [performance, setPerformance] = useState<InvestmentPerformanceRow[]>([]);
  const [allocation, setAllocation] = useState<InvestmentAllocation | null>(null);
  const [history, setHistory] = useState<InvestmentHistoryRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeView, setActiveView] = useState<"overview" | "holdings" | "performance" | "allocation">("overview");

  useEffect(() => {
    setLoading(true);
    Promise.all([
      fetchInvestmentSummary(),
      fetchInvestmentHoldings(),
      fetchInvestmentPerformance(),
      fetchInvestmentAllocation(),
      fetchInvestmentHistory(),
    ]).then(([s, h, p, a, hist]) => {
      setSummary(s);
      setHoldings(h);
      setPerformance(p);
      setAllocation(a);
      setHistory(hist);
    }).finally(() => setLoading(false));
  }, []);

  const hasData = summary && summary.report_date;

  return (
    <div>
      {loading ? (
        <div style={card}><div style={hint}>Loading investment data…</div></div>
      ) : !hasData ? (
        <div style={card}>
          <div style={hint}>
            No investment data found. Ingest a Trading 212 Activity Statement PDF to see your portfolio.
          </div>
        </div>
      ) : (
        <>
          {/* Summary strip */}
          <SummaryStrip summary={summary!} />

          {/* Sub-tab nav */}
          <div style={subTabBar}>
            {(["overview", "holdings", "performance", "allocation"] as const).map((v) => (
              <button key={v} style={subTab(activeView === v)} onClick={() => setActiveView(v)}>
                {v.charAt(0).toUpperCase() + v.slice(1)}
              </button>
            ))}
          </div>

          {activeView === "overview" && (
            <OverviewTab summary={summary!} history={history} />
          )}
          {activeView === "holdings" && (
            <HoldingsTab holdings={holdings} />
          )}
          {activeView === "performance" && (
            <PerformanceTab performance={performance} />
          )}
          {activeView === "allocation" && (
            <AllocationTab allocation={allocation!} />
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Summary strip
// ---------------------------------------------------------------------------

function SummaryStrip({ summary }: { summary: InvestmentSummary }) {
  const returnColor = summary.total_return_gbp >= 0 ? "#60b98a" : "#e8735a";
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 16, marginBottom: 24 }}>
      <StatCard label="Portfolio Value" value={fmt(summary.total_value_gbp)} color="#4f86c6" />
      <StatCard label="Total Invested" value={fmt(summary.total_cost_gbp)} color="#64748b" />
      <StatCard label="Unrealised Return" value={fmt(summary.total_return_gbp)} color={returnColor}
        sub={fmtPct(summary.return_pct)} subColor={returnColor} />
      {summary.accounts.map((acc) => (
        <StatCard key={acc.account_name} label={acc.account_name}
          value={fmt(acc.value_gbp)} color="#9b6dff"
          sub={fmtPct(acc.return_pct)}
          subColor={acc.return_gbp >= 0 ? "#60b98a" : "#e8735a"} />
      ))}
    </div>
  );
}

function StatCard({ label, value, color, sub, subColor }: {
  label: string; value: string; color: string; sub?: string; subColor?: string;
}) {
  return (
    <div style={{ background: "#fff", borderRadius: 12, padding: "16px 20px", boxShadow: "0 2px 8px rgba(0,0,0,0.08)" }}>
      <div style={{ fontSize: 11, color: "#888", marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, color }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: subColor ?? "#888", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Overview tab — history chart + account breakdown
// ---------------------------------------------------------------------------

function OverviewTab({ summary, history }: { summary: InvestmentSummary; history: InvestmentHistoryRow[] }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {history.length > 1 && (
        <div style={card}>
          <h3 style={sectionTitle}>Portfolio Value Over Time</h3>
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={history} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt} width={80} />
              <Tooltip formatter={(v: number) => fmt(v)} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line type="monotone" dataKey="total_value" name="Portfolio Value" stroke="#4f86c6" strokeWidth={2} dot={{ r: 4 }} />
              <Line type="monotone" dataKey="total_cost" name="Total Invested" stroke="#94a3b8" strokeWidth={2} strokeDasharray="4 2" dot={{ r: 4 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {history.length === 1 && (
        <div style={card}>
          <h3 style={sectionTitle}>Portfolio Snapshot — {summary.report_date}</h3>
          <p style={{ color: "#888", fontSize: 13 }}>
            Ingest more statements over time to see your portfolio history chart here.
          </p>
          <div style={{ display: "flex", gap: 16, marginTop: 16, flexWrap: "wrap" }}>
            {summary.accounts.map((acc) => (
              <div key={acc.account_name} style={{ background: "#f8fafc", borderRadius: 8, padding: "12px 16px", flex: 1, minWidth: 140 }}>
                <div style={{ fontSize: 12, color: "#888", marginBottom: 4 }}>{acc.account_name}</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: "#4f86c6" }}>{fmt(acc.value_gbp)}</div>
                <div style={{ fontSize: 12, color: acc.return_gbp >= 0 ? "#60b98a" : "#e8735a", marginTop: 2 }}>
                  {fmt(acc.return_gbp)} ({fmtPct(acc.return_pct)})
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Holdings tab — full position table
// ---------------------------------------------------------------------------

function HoldingsTab({ holdings }: { holdings: InvestmentHolding[] }) {
  return (
    <div style={card}>
      <h3 style={sectionTitle}>All Positions</h3>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: "2px solid #e2e8f0" }}>
              {["Account", "Ticker", "Currency", "Qty", "Avg Cost", "Current", "Value (£)", "Return (£)", "Return %"].map((h) => (
                <th key={h} style={thStyle}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {holdings.map((h) => {
              const pos = (h.return_gbp ?? 0) >= 0;
              return (
                <tr key={h.id} style={{ borderBottom: "1px solid #f0f0f0" }}>
                  <td style={tdStyle}><span style={badge}>{h.account_name}</span></td>
                  <td style={{ ...tdStyle, fontWeight: 700, color: "#1a1a2e" }}>{h.instrument}</td>
                  <td style={tdStyle}>{h.currency ?? "—"}</td>
                  <td style={tdStyle}>{h.quantity?.toFixed(4) ?? "—"}</td>
                  <td style={tdStyle}>{h.avg_price != null ? h.avg_price.toFixed(2) : "—"}</td>
                  <td style={tdStyle}>{h.current_price != null ? h.current_price.toFixed(2) : "—"}</td>
                  <td style={{ ...tdStyle, fontWeight: 600 }}>{fmtDec(h.value_gbp)}</td>
                  <td style={{ ...tdStyle, color: pos ? "#60b98a" : "#e8735a", fontWeight: 600 }}>
                    {h.return_gbp != null ? fmtDec(h.return_gbp) : "—"}
                  </td>
                  <td style={{ ...tdStyle, color: pos ? "#60b98a" : "#e8735a", fontWeight: 600 }}>
                    {h.return_pct != null ? fmtPct(h.return_pct) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Performance tab — gain/loss bar chart by instrument
// ---------------------------------------------------------------------------

function PerformanceTab({ performance }: { performance: InvestmentPerformanceRow[] }) {
  return (
    <div style={card}>
      <h3 style={sectionTitle}>Unrealised Gain / Loss by Position</h3>
      <ResponsiveContainer width="100%" height={Math.max(300, performance.length * 36)}>
        <BarChart
          data={performance}
          layout="vertical"
          margin={{ top: 8, right: 80, bottom: 0, left: 0 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" horizontal={false} />
          <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={fmt} />
          <YAxis type="category" dataKey="instrument" tick={{ fontSize: 11 }} width={60} />
          <Tooltip
            formatter={(v: number, name: string) => [name === "return_gbp" ? fmtDec(v) : fmtPct(v), name === "return_gbp" ? "Return (£)" : "Return (%)"]}
          />
          <Bar dataKey="return_gbp" name="Return (£)" radius={[0, 4, 4, 0]}>
            {performance.map((entry, i) => (
              <Cell key={i} fill={entry.return_gbp >= 0 ? "#60b98a" : "#e8735a"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>

      {/* Return % table */}
      <div style={{ marginTop: 24 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: "#555", marginBottom: 8 }}>Return % by position</div>
        {performance.map((p) => (
          <div key={p.instrument + p.account_name} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0", borderBottom: "1px solid #f7f7f7" }}>
            <div>
              <span style={{ fontWeight: 700, color: "#1a1a2e" }}>{p.instrument}</span>
              <span style={{ fontSize: 11, color: "#888", marginLeft: 8 }}>{p.account_name}</span>
            </div>
            <div style={{ textAlign: "right" }}>
              <span style={{ fontWeight: 600, color: p.return_pct >= 0 ? "#60b98a" : "#e8735a" }}>{fmtPct(p.return_pct)}</span>
              <span style={{ color: "#888", fontSize: 12, marginLeft: 8 }}>({fmtDec(p.return_gbp)})</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Allocation tab — pie chart + treemap-style list
// ---------------------------------------------------------------------------

function AllocationTab({ allocation }: { allocation: InvestmentAllocation }) {
  const pieData = allocation.by_instrument.map((item, i) => ({
    name: item.instrument,
    value: item.value_gbp,
    color: COLORS[i % COLORS.length],
  }));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Account split */}
      <div style={card}>
        <h3 style={sectionTitle}>By Account</h3>
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          {allocation.by_account.map((acc, i) => (
            <div key={acc.account_name} style={{
              flex: 1, minWidth: 140, background: "#f8fafc", borderRadius: 8,
              padding: "14px 16px", borderLeft: `4px solid ${COLORS[i % COLORS.length]}`,
            }}>
              <div style={{ fontSize: 12, color: "#888", marginBottom: 4 }}>{acc.account_name}</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: "#1a1a2e" }}>{fmt(acc.value_gbp)}</div>
              <div style={{ fontSize: 12, color: "#888", marginTop: 2 }}>{acc.weight_pct}% of portfolio</div>
            </div>
          ))}
        </div>
      </div>

      {/* Pie chart */}
      <div style={card}>
        <h3 style={sectionTitle}>Position Allocation</h3>
        <div style={{ display: "flex", gap: 24, flexWrap: "wrap", alignItems: "flex-start" }}>
          <ResponsiveContainer width={280} height={280}>
            <PieChart>
              <Pie data={pieData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={110} label={({ name, weight_pct }: any) => `${name}`}>
                {pieData.map((entry, i) => (
                  <Cell key={i} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip formatter={(v: number) => fmt(v)} />
            </PieChart>
          </ResponsiveContainer>

          {/* Legend list */}
          <div style={{ flex: 1, minWidth: 200 }}>
            {allocation.by_instrument.map((item, i) => (
              <div key={item.instrument + item.account_name} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "5px 0", borderBottom: "1px solid #f7f7f7" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <div style={{ width: 10, height: 10, borderRadius: 2, background: COLORS[i % COLORS.length] }} />
                  <span style={{ fontWeight: 600, fontSize: 13 }}>{item.instrument}</span>
                  <span style={{ fontSize: 11, color: "#888" }}>{item.account_name}</span>
                </div>
                <div style={{ textAlign: "right" }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: "#1a1a2e" }}>{fmt(item.value_gbp)}</span>
                  <span style={{ fontSize: 11, color: "#888", marginLeft: 6 }}>{item.weight_pct}%</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const card: React.CSSProperties = {
  background: "#fff", borderRadius: 12, padding: "20px 24px",
  boxShadow: "0 2px 8px rgba(0,0,0,0.08)", marginBottom: 24,
};
const hint: React.CSSProperties = { color: "#aaa", textAlign: "center", padding: 60, fontSize: 14 };
const sectionTitle: React.CSSProperties = { fontSize: 15, fontWeight: 600, color: "#1a1a2e", marginBottom: 16, marginTop: 0 };
const subTabBar: React.CSSProperties = {
  display: "flex", gap: 0, background: "#fff", borderRadius: 10,
  boxShadow: "0 2px 8px rgba(0,0,0,0.08)", marginBottom: 24, overflow: "hidden",
};
const subTab = (active: boolean): React.CSSProperties => ({
  background: active ? "#4f86c6" : "transparent",
  color: active ? "#fff" : "#64748b",
  border: "none", padding: "10px 20px", fontSize: 13, fontWeight: active ? 600 : 400,
  cursor: "pointer", flex: 1, transition: "background 0.15s",
});
const thStyle: React.CSSProperties = {
  padding: "8px 12px", textAlign: "right", fontSize: 11,
  color: "#888", fontWeight: 600, whiteSpace: "nowrap",
};
const tdStyle: React.CSSProperties = {
  padding: "9px 12px", textAlign: "right", fontSize: 13, color: "#374151",
};
const badge: React.CSSProperties = {
  background: "#eff6ff", color: "#3b82f6", borderRadius: 4,
  padding: "2px 6px", fontSize: 11, fontWeight: 600,
};

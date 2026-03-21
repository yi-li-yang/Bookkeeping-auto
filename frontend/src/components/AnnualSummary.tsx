import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import { fetchAnnualSummary, AnnualSummaryData } from "../api/client";

const fmt = (v: number) => `£${v.toLocaleString("en-GB", { maximumFractionDigits: 0 })}`;

export default function AnnualSummary() {
  const currentYear = new Date().getFullYear();
  const [year, setYear] = useState(currentYear);
  const [data, setData] = useState<AnnualSummaryData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchAnnualSummary(year).then(setData).finally(() => setLoading(false));
  }, [year]);

  const years = Array.from({ length: 5 }, (_, i) => currentYear - i);

  return (
    <div style={card}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <h2 style={title}>Year in Review</h2>
        <select style={select} value={year} onChange={(e) => setYear(Number(e.target.value))}>
          {years.map((y) => <option key={y} value={y}>{y}</option>)}
        </select>
      </div>

      {loading ? (
        <div style={hint}>Loading…</div>
      ) : !data || data.months_of_data === 0 ? (
        <div style={hint}>No data for {year}.</div>
      ) : (
        <>
          {/* Key stats */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 12, marginBottom: 20 }}>
            <Stat label="Total Income" value={fmt(data.total_income)} color="#60b98a" />
            <Stat label="Total Expenses" value={fmt(data.total_expenses)} color="#e8735a" />
            <Stat label="Net Savings" value={fmt(data.savings)} color={data.savings >= 0 ? "#60b98a" : "#e8735a"} />
            <Stat label="Savings Rate" value={`${data.savings_rate}%`} color={data.savings_rate > 20 ? "#60b98a" : data.savings_rate < 5 ? "#e8735a" : "#f0b429"} />
            <Stat label="Months of Data" value={String(data.months_of_data)} color="#4f86c6" />
          </div>

          {/* Highlights */}
          {(data.highest_spending_month || data.best_savings_month) && (
            <div style={{ display: "flex", gap: 10, marginBottom: 20, flexWrap: "wrap" }}>
              {data.highest_spending_month && (
                <div style={highlight("#fff7f7", "#fca5a5")}>
                  Highest spend: <strong>{data.highest_spending_month}</strong>
                </div>
              )}
              {data.best_savings_month && (
                <div style={highlight("#f0fdf4", "#86efac")}>
                  Best savings: <strong>{data.best_savings_month}</strong>
                </div>
              )}
            </div>
          )}

          {/* Top categories chart */}
          {data.top_categories.length > 0 && (
            <>
              <div style={{ fontSize: 13, fontWeight: 600, color: "#555", marginBottom: 8 }}>Top Spending Categories</div>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart
                  data={data.top_categories}
                  layout="vertical"
                  margin={{ top: 0, right: 16, bottom: 0, left: 0 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" horizontal={false} />
                  <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={fmt} />
                  <YAxis type="category" dataKey="category" tick={{ fontSize: 11 }} width={100} />
                  <Tooltip formatter={(v: number) => fmt(v)} />
                  <Bar dataKey="total" fill="#e8735a" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </>
          )}
        </>
      )}
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ background: "#f8fafc", borderRadius: 8, padding: "12px 14px" }}>
      <div style={{ fontSize: 11, color: "#888", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color }}>{value}</div>
    </div>
  );
}

const highlight = (bg: string, border: string): React.CSSProperties => ({
  background: bg, border: `1px solid ${border}`, borderRadius: 6, padding: "8px 12px", fontSize: 13, color: "#374151",
});

const card: React.CSSProperties = { background: "#fff", borderRadius: 12, padding: "20px 24px", boxShadow: "0 2px 8px rgba(0,0,0,0.08)" };
const title: React.CSSProperties = { fontSize: 16, fontWeight: 600, color: "#1a1a2e" };
const hint: React.CSSProperties = { color: "#aaa", textAlign: "center", padding: 40, fontSize: 14 };
const select: React.CSSProperties = { border: "1px solid #e2e8f0", borderRadius: 6, padding: "5px 8px", fontSize: 13, outline: "none" };

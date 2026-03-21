import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { fetchMonthComparison, MonthComparisonRow } from "../api/client";

const fmt = (v: number) => `£${v.toLocaleString("en-GB", { maximumFractionDigits: 0 })}`;

export default function MonthComparison() {
  const now = new Date();
  const toMonthStr = (y: number, m: number) => `${y}-${String(m).padStart(2, "0")}`;
  const prevMonth = now.getMonth() === 0 ? toMonthStr(now.getFullYear() - 1, 12) : toMonthStr(now.getFullYear(), now.getMonth());
  const currMonth = toMonthStr(now.getFullYear(), now.getMonth() + 1);

  const [month1, setMonth1] = useState(prevMonth);
  const [month2, setMonth2] = useState(currMonth);
  const [data, setData] = useState<MonthComparisonRow[]>([]);
  const [loading, setLoading] = useState(false);

  const load = () => {
    if (!month1 || !month2) return;
    setLoading(true);
    fetchMonthComparison(month1, month2).then(setData).finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const totalM1 = data.reduce((s, r) => s + r.month1_total, 0);
  const totalM2 = data.reduce((s, r) => s + r.month2_total, 0);
  const totalChange = totalM2 - totalM1;

  return (
    <div style={card}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12, marginBottom: 16 }}>
        <h2 style={title}>Month-over-Month Comparison</h2>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <input type="month" style={monthInput} value={month1} onChange={(e) => setMonth1(e.target.value)} />
          <span style={{ color: "#888", fontSize: 13 }}>vs</span>
          <input type="month" style={monthInput} value={month2} onChange={(e) => setMonth2(e.target.value)} />
          <button style={compareBtn} onClick={load}>Compare</button>
        </div>
      </div>

      {loading ? (
        <div style={hint}>Loading…</div>
      ) : !data.length ? (
        <div style={hint}>Select two months and click Compare.</div>
      ) : (
        <>
          {/* Total change banner */}
          <div style={{ display: "flex", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
            <div style={summaryBadge}>
              <span style={{ fontSize: 11, color: "#888" }}>{month1} total</span>
              <span style={{ fontSize: 16, fontWeight: 700, color: "#e8735a" }}>{fmt(totalM1)}</span>
            </div>
            <div style={summaryBadge}>
              <span style={{ fontSize: 11, color: "#888" }}>{month2} total</span>
              <span style={{ fontSize: 16, fontWeight: 700, color: "#e8735a" }}>{fmt(totalM2)}</span>
            </div>
            <div style={summaryBadge}>
              <span style={{ fontSize: 11, color: "#888" }}>Change</span>
              <span style={{ fontSize: 16, fontWeight: 700, color: totalChange <= 0 ? "#60b98a" : "#e8735a" }}>
                {totalChange >= 0 ? "+" : ""}{fmt(totalChange)}
              </span>
            </div>
          </div>

          {/* Bar chart */}
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="category" tick={{ fontSize: 10 }} interval={0} angle={-30} textAnchor="end" height={60} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt} width={72} />
              <Tooltip formatter={(v: number) => fmt(v)} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="month1_total" name={month1} fill="#4f86c6" radius={[4, 4, 0, 0]} />
              <Bar dataKey="month2_total" name={month2} fill="#e8735a" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>

          {/* Table of biggest changes */}
          <div style={{ marginTop: 16, fontSize: 13 }}>
            <div style={{ fontWeight: 600, color: "#555", marginBottom: 8 }}>Biggest changes</div>
            {[...data]
              .filter((r) => r.change_pct !== null)
              .sort((a, b) => Math.abs(b.change_abs) - Math.abs(a.change_abs))
              .slice(0, 5)
              .map((r) => (
                <div key={r.category} style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid #f7f7f7" }}>
                  <span style={{ color: "#374151" }}>{r.category}</span>
                  <span style={{ color: r.change_abs <= 0 ? "#60b98a" : "#e8735a", fontWeight: 600 }}>
                    {r.change_abs >= 0 ? "+" : ""}{fmt(r.change_abs)}
                    {r.change_pct !== null && (
                      <span style={{ fontWeight: 400, color: "#888", marginLeft: 6 }}>({r.change_pct > 0 ? "+" : ""}{r.change_pct}%)</span>
                    )}
                  </span>
                </div>
              ))}
          </div>
        </>
      )}
    </div>
  );
}

const card: React.CSSProperties = { background: "#fff", borderRadius: 12, padding: "20px 24px", boxShadow: "0 2px 8px rgba(0,0,0,0.08)" };
const title: React.CSSProperties = { fontSize: 16, fontWeight: 600, color: "#1a1a2e" };
const hint: React.CSSProperties = { color: "#aaa", textAlign: "center", padding: 40, fontSize: 14 };
const monthInput: React.CSSProperties = { border: "1px solid #e2e8f0", borderRadius: 6, padding: "5px 8px", fontSize: 13, outline: "none" };
const compareBtn: React.CSSProperties = { background: "#4f86c6", color: "#fff", border: "none", borderRadius: 6, padding: "6px 14px", fontSize: 13, fontWeight: 600, cursor: "pointer" };
const summaryBadge: React.CSSProperties = { background: "#f8fafc", borderRadius: 8, padding: "10px 14px", display: "flex", flexDirection: "column", gap: 3 };

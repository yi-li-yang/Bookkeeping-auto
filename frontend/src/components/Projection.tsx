import { useEffect, useState } from "react";
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { fetchProjection, ProjectionRow } from "../api/client";

const fmt = (v: number) => `£${v.toLocaleString("en-GB", { maximumFractionDigits: 0 })}`;

export default function Projection() {
  const [horizon, setHorizon] = useState(6);
  const [data, setData] = useState<ProjectionRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchProjection(horizon).then(setData).finally(() => setLoading(false));
  }, [horizon]);

  const lastRow = data[data.length - 1];

  return (
    <div style={card}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <h2 style={title}>Cash Flow Projection</h2>
          <p style={subtitle}>Based on last 3 months average income & expenses</p>
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          {[3, 6, 12].map((h) => (
            <button
              key={h}
              style={horizonBtn(horizon === h)}
              onClick={() => setHorizon(h)}
            >
              {h}m
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div style={hint}>Loading…</div>
      ) : !data.length ? (
        <div style={hint}>Not enough data to project (need at least 1 month).</div>
      ) : (
        <>
          <ResponsiveContainer width="100%" height={260}>
            <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="month" tick={{ fontSize: 11 }} />
              <YAxis yAxisId="left" tick={{ fontSize: 11 }} tickFormatter={fmt} width={80} />
              <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} tickFormatter={fmt} width={80} />
              <Tooltip formatter={(v: number) => fmt(v)} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar yAxisId="left" dataKey="projected_income" name="Proj. Income" fill="#60b98a" radius={[4, 4, 0, 0]} opacity={0.8} />
              <Bar yAxisId="left" dataKey="projected_expenses" name="Proj. Expenses" fill="#e8735a" radius={[4, 4, 0, 0]} opacity={0.8} />
              <Line yAxisId="right" type="monotone" dataKey="projected_balance" name="Proj. Balance" stroke="#4f86c6" strokeWidth={2} dot={{ r: 3 }} />
            </ComposedChart>
          </ResponsiveContainer>

          {lastRow && (
            <div style={{ display: "flex", gap: 12, marginTop: 16, flexWrap: "wrap" }}>
              <StatCard label={`Balance in ${horizon}m`} value={fmt(lastRow.projected_balance)} color={lastRow.projected_balance >= 0 ? "#60b98a" : "#e8735a"} />
              <StatCard label="Monthly savings" value={fmt(lastRow.projected_savings)} color={lastRow.projected_savings >= 0 ? "#60b98a" : "#e8735a"} />
              <StatCard label="Avg income/mo" value={fmt(lastRow.projected_income)} color="#4f86c6" />
              <StatCard label="Avg expenses/mo" value={fmt(lastRow.projected_expenses)} color="#e8735a" />
            </div>
          )}
        </>
      )}
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ background: "#f8fafc", borderRadius: 8, padding: "10px 14px", flex: 1, minWidth: 110 }}>
      <div style={{ fontSize: 11, color: "#888", marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 700, color }}>{value}</div>
    </div>
  );
}

const card: React.CSSProperties = { background: "#fff", borderRadius: 12, padding: "20px 24px", boxShadow: "0 2px 8px rgba(0,0,0,0.08)" };
const title: React.CSSProperties = { fontSize: 16, fontWeight: 600, color: "#1a1a2e", marginBottom: 2 };
const subtitle: React.CSSProperties = { fontSize: 12, color: "#888", margin: 0 };
const hint: React.CSSProperties = { color: "#aaa", textAlign: "center", padding: 40, fontSize: 14 };
const horizonBtn = (active: boolean): React.CSSProperties => ({
  background: active ? "#4f86c6" : "none",
  color: active ? "#fff" : "#64748b",
  border: "1px solid #e2e8f0",
  borderRadius: 6,
  padding: "4px 10px",
  fontSize: 12,
  cursor: "pointer",
  fontWeight: active ? 600 : 400,
});

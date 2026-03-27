import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { fetchDiningComparison, DiningComparisonRow } from "../api/client";

interface Props { start?: string; end?: string; }

const fmt = (v: number) => `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;

export default function DiningComparison({ start, end }: Props) {
  const [data, setData] = useState<DiningComparisonRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchDiningComparison(start, end)
      .then(setData)
      .finally(() => setLoading(false));
  }, [start, end]);

  const totalDining = data.reduce((s, r) => s + r.dining_out, 0);
  const totalHome = data.reduce((s, r) => s + r.home_food, 0);
  const avgRatio = totalHome > 0 ? (totalDining / totalHome).toFixed(2) : null;

  return (
    <div style={card}>
      <div style={{ marginBottom: 16 }}>
        <h2 style={title}>Dine Out vs Eat at Home</h2>
        <p style={subtitle}>
          Monthly spend comparison · Dine out / Home food ratio:{" "}
          <strong style={{ color: avgRatio && parseFloat(avgRatio) > 1 ? "#e8735a" : "#60b98a" }}>
            {avgRatio ?? "—"}×
          </strong>
        </p>
      </div>

      {loading ? (
        <div style={hint}>Loading…</div>
      ) : !data.length ? (
        <div style={hint}>
          No data found. Make sure your categories include keywords like "Dining", "Restaurant", "Groceries", or "Supermarket".
        </div>
      ) : (
        <>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="month" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt} width={72} />
              <Tooltip formatter={(v: number) => fmt(v)} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="dining_out" name="Dine Out" fill="#e8735a" radius={[3, 3, 0, 0]} />
              <Bar dataKey="home_food" name="Eat at Home" fill="#60b98a" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>

          <div style={statsRow}>
            <Stat label="Total Dine Out" value={fmt(totalDining)} color="#e8735a" />
            <Stat label="Total Home Food" value={fmt(totalHome)} color="#60b98a" />
            <Stat label="Dine/Home Ratio" value={avgRatio ? `${avgRatio}×` : "—"} color="#4f86c6" />
          </div>
        </>
      )}
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ textAlign: "center", flex: 1 }}>
      <div style={{ fontSize: 11, color: "#888", marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color }}>{value}</div>
    </div>
  );
}

const card: React.CSSProperties = { background: "#fff", borderRadius: 12, padding: "20px 24px", boxShadow: "0 2px 8px rgba(0,0,0,0.08)" };
const title: React.CSSProperties = { fontSize: 16, fontWeight: 600, color: "#1a1a2e", marginBottom: 2 };
const subtitle: React.CSSProperties = { fontSize: 12, color: "#888", margin: 0 };
const hint: React.CSSProperties = { color: "#aaa", textAlign: "center", padding: 40, fontSize: 14 };
const statsRow: React.CSSProperties = { display: "flex", gap: 16, marginTop: 16, paddingTop: 16, borderTop: "1px solid #f0f0f0" };

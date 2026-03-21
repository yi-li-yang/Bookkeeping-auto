import { useEffect, useState } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { fetchSpendingVelocity, VelocityDay } from "../api/client";

const fmt = (v: number) => `£${v.toLocaleString("en-GB", { maximumFractionDigits: 0 })}`;

export default function SpendingVelocity() {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [data, setData] = useState<VelocityDay[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchSpendingVelocity(year, month).then(setData).finally(() => setLoading(false));
  }, [year, month]);

  const monthStr = `${year}-${String(month).padStart(2, "0")}`;
  const priorLabel = month === 1
    ? `${year - 1}-12`
    : `${year}-${String(month - 1).padStart(2, "0")}`;

  const handleMonthChange = (val: string) => {
    const [y, m] = val.split("-").map(Number);
    setYear(y);
    setMonth(m);
  };

  return (
    <div style={card}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <h2 style={title}>Spending Velocity</h2>
          <p style={subtitle}>Cumulative daily spend — how fast you burn through the month</p>
        </div>
        <input
          type="month"
          style={monthInput}
          value={monthStr}
          onChange={(e) => handleMonthChange(e.target.value)}
        />
      </div>
      {loading ? (
        <div style={hint}>Loading…</div>
      ) : !data.length ? (
        <div style={hint}>No data for this month.</div>
      ) : (
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis dataKey="day" tick={{ fontSize: 11 }} label={{ value: "Day of month", position: "insideBottom", offset: -2, fontSize: 11, fill: "#888" }} />
            <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt} width={72} />
            <Tooltip
              formatter={(v: number, name: string) => [fmt(v), name]}
              labelFormatter={(l) => `Day ${l}`}
            />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Line
              type="monotone"
              dataKey="cumulative"
              name={monthStr}
              stroke="#4f86c6"
              strokeWidth={2}
              dot={false}
            />
            <Line
              type="monotone"
              dataKey="prior_cumulative"
              name={priorLabel}
              stroke="#e2e8f0"
              strokeWidth={2}
              strokeDasharray="5 3"
              dot={false}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

const card: React.CSSProperties = { background: "#fff", borderRadius: 12, padding: "20px 24px", boxShadow: "0 2px 8px rgba(0,0,0,0.08)" };
const title: React.CSSProperties = { fontSize: 16, fontWeight: 600, color: "#1a1a2e", marginBottom: 2 };
const subtitle: React.CSSProperties = { fontSize: 12, color: "#888", margin: 0 };
const hint: React.CSSProperties = { color: "#aaa", textAlign: "center", padding: 40, fontSize: 14 };
const monthInput: React.CSSProperties = { border: "1px solid #e2e8f0", borderRadius: 6, padding: "5px 8px", fontSize: 13, outline: "none" };

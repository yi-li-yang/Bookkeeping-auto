import { useEffect, useState } from "react";
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { fetchIncomeVsExpenses, IncomeExpenseRow } from "../api/client";

interface Props {
  start?: string;
  end?: string;
}

const fmt = (v: number) => `£${v.toLocaleString("en-GB", { maximumFractionDigits: 0 })}`;

export default function IncomeVsExpenses({ start, end }: Props) {
  const [data, setData] = useState<IncomeExpenseRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    const params: Record<string, string> = {};
    if (start) params.start = start;
    if (end) params.end = end;
    fetchIncomeVsExpenses(params)
      .then(setData)
      .finally(() => setLoading(false));
  }, [start, end]);

  if (loading) return <div style={card}><p style={hint}>Loading…</p></div>;
  if (!data.length) return <div style={card}><p style={hint}>No income/expense data yet.</p></div>;

  return (
    <div style={card}>
      <h2 style={title}>Income vs Expenses</h2>
      <ResponsiveContainer width="100%" height={300}>
        <ComposedChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis dataKey="month" tick={{ fontSize: 11 }} />
          <YAxis yAxisId="left" tickFormatter={(v) => `£${(v / 1000).toFixed(0)}k`} tick={{ fontSize: 11 }} />
          <YAxis yAxisId="right" orientation="right" unit="%" tick={{ fontSize: 11 }} domain={[0, 100]} />
          <Tooltip
            formatter={(value: number, name: string) => {
              if (name === "Savings Rate") return [`${value}%`, name];
              return [fmt(value), name];
            }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar yAxisId="left" dataKey="income" name="Income" fill="#60b98a" radius={[4, 4, 0, 0]} />
          <Bar yAxisId="left" dataKey="expenses" name="Expenses" fill="#e8735a" radius={[4, 4, 0, 0]} />
          <Line yAxisId="right" type="monotone" dataKey="savings_rate" name="Savings Rate" stroke="#4f86c6" strokeWidth={2} dot={{ r: 3 }} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

const card: React.CSSProperties = {
  background: "#fff",
  borderRadius: 12,
  padding: "20px 24px",
  boxShadow: "0 2px 8px rgba(0,0,0,0.08)",
};

const title: React.CSSProperties = { fontSize: 16, fontWeight: 600, marginBottom: 16, color: "#1a1a2e" };
const hint: React.CSSProperties = { color: "#888", fontSize: 14 };

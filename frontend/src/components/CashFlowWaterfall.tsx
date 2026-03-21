import { useEffect, useMemo, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Cell, ResponsiveContainer, ReferenceLine,
} from "recharts";
import { fetchCashflowWaterfall, WaterfallRow } from "../api/client";

interface Props {
  start?: string;
  end?: string;
}

interface WaterfallBar {
  month: string;
  base: number;   // transparent base for waterfall effect
  income: number;
  expense: number;
  net: number;
  end_balance: number;
}

export default function CashFlowWaterfall({ start, end }: Props) {
  const [rows, setRows] = useState<WaterfallRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    const params: Record<string, string> = {};
    if (start) params.start = start;
    if (end) params.end = end;
    fetchCashflowWaterfall(params)
      .then(setRows)
      .finally(() => setLoading(false));
  }, [start, end]);

  const chartData: WaterfallBar[] = useMemo(() => {
    return rows.map((r) => ({
      month: r.month,
      base: Math.min(r.start_balance, r.end_balance),
      income: r.income,
      expense: r.expenses.reduce((s, e) => s + e.amount, 0),
      net: r.net,
      end_balance: r.end_balance,
    }));
  }, [rows]);

  if (loading) return <div style={card}><p style={hint}>Loading…</p></div>;
  if (!chartData.length) return <div style={card}><p style={hint}>No cashflow data yet.</p></div>;

  const fmt = (v: number) => `£${v.toLocaleString("en-GB", { maximumFractionDigits: 0 })}`;

  return (
    <div style={card}>
      <h2 style={title}>Cash Flow</h2>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={chartData} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis dataKey="month" tick={{ fontSize: 11 }} />
          <YAxis tickFormatter={(v) => `£${(v / 1000).toFixed(0)}k`} tick={{ fontSize: 11 }} />
          <Tooltip
            formatter={(value: number, name: string) => [fmt(Math.abs(value)), name]}
            labelFormatter={(l) => `Month: ${l}`}
          />
          <ReferenceLine y={0} stroke="#999" />
          <Bar dataKey="income" name="Income" stackId="flow" fill="#60b98a" radius={[4, 4, 0, 0]} />
          <Bar dataKey="expense" name="Expenses" stackId="flow2">
            {chartData.map((_, i) => (
              <Cell key={i} fill="#e8735a" />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>

      {/* Summary table */}
      <div style={{ marginTop: 16, overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ borderBottom: "2px solid #f0f0f0" }}>
              {["Month", "Income", "Expenses", "Net", "Balance"].map((h) => (
                <th key={h} style={{ textAlign: "right", padding: "4px 8px", fontWeight: 600, color: "#555" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.month} style={{ borderBottom: "1px solid #f7f7f7" }}>
                <td style={{ padding: "4px 8px", textAlign: "right" }}>{r.month}</td>
                <td style={{ padding: "4px 8px", textAlign: "right", color: "#60b98a" }}>{fmt(r.income)}</td>
                <td style={{ padding: "4px 8px", textAlign: "right", color: "#e8735a" }}>{fmt(r.expenses.reduce((s, e) => s + e.amount, 0))}</td>
                <td style={{ padding: "4px 8px", textAlign: "right", color: r.net >= 0 ? "#60b98a" : "#e8735a", fontWeight: 600 }}>{fmt(r.net)}</td>
                <td style={{ padding: "4px 8px", textAlign: "right" }}>{fmt(r.end_balance)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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

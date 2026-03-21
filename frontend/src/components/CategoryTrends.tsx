import { useEffect, useState } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { fetchCategoryTrends, CategoryTrend } from "../api/client";

interface Props { start?: string; end?: string; }

const COLORS = ["#4f86c6", "#e8735a", "#60b98a", "#f0b429", "#9b6dff", "#3caea3", "#ed553b", "#20639b"];

const fmt = (v: number) => `£${v.toLocaleString("en-GB", { maximumFractionDigits: 0 })}`;

export default function CategoryTrends({ start, end }: Props) {
  const [data, setData] = useState<CategoryTrend[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    setLoading(true);
    fetchCategoryTrends(start, end)
      .then(setData)
      .finally(() => setLoading(false));
  }, [start, end]);

  const displayed = showAll ? data : data.slice(0, 6);

  // Build flat chart rows keyed by month
  const allMonths = Array.from(new Set(data.flatMap((c) => c.months.map((m) => m.month)))).sort();
  const chartData = allMonths.map((m) => {
    const row: Record<string, unknown> = { month: m };
    displayed.forEach((cat) => {
      const entry = cat.months.find((x) => x.month === m);
      if (entry) {
        row[cat.category] = entry.total;
        row[`${cat.category}__anomaly`] = entry.is_anomaly;
      }
    });
    return row;
  });

  const renderDot = (catKey: string, color: string) => (props: any) => {
    const { cx, cy, payload } = props;
    if (payload[`${catKey}__anomaly`]) {
      return <circle key={`${catKey}-${payload.month}`} cx={cx} cy={cy} r={6} fill="#e8735a" stroke="#fff" strokeWidth={2} />;
    }
    return <circle key={`${catKey}-${payload.month}`} cx={cx} cy={cy} r={2} fill={color} />;
  };

  return (
    <div style={card}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <h2 style={title}>Category Spend Trends</h2>
          <p style={subtitle}>Monthly totals with 3-month rolling average · <span style={{ color: "#e8735a" }}>● anomaly spike</span></p>
        </div>
        {data.length > 6 && (
          <button style={toggleBtn} onClick={() => setShowAll(!showAll)}>
            {showAll ? "Top 6" : `All ${data.length}`}
          </button>
        )}
      </div>
      {loading ? (
        <div style={hint}>Loading…</div>
      ) : !chartData.length ? (
        <div style={hint}>No data in selected range.</div>
      ) : (
        <ResponsiveContainer width="100%" height={320}>
          <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis dataKey="month" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt} width={72} />
            <Tooltip formatter={(v: number) => fmt(v)} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            {displayed.map((cat, i) => (
              <Line
                key={cat.category}
                type="monotone"
                dataKey={cat.category}
                stroke={COLORS[i % COLORS.length]}
                strokeWidth={2}
                dot={renderDot(cat.category, COLORS[i % COLORS.length])}
                activeDot={{ r: 5 }}
                connectNulls
              />
            ))}
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
const toggleBtn: React.CSSProperties = { background: "none", border: "1px solid #e2e8f0", borderRadius: 6, padding: "4px 12px", fontSize: 12, cursor: "pointer", color: "#64748b" };

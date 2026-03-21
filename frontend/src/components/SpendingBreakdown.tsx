import { useEffect, useMemo, useState } from "react";
import { Treemap, ResponsiveContainer, Tooltip } from "recharts";
import { fetchSpendingByCategory, SpendingRow } from "../api/client";

const COLORS = [
  "#4f86c6", "#e8735a", "#60b98a", "#f0b429", "#9b6dff",
  "#f6c90e", "#3caea3", "#ed553b", "#20639b", "#f76c6c",
];

interface Props {
  start?: string;
  end?: string;
}

export default function SpendingBreakdown({ start, end }: Props) {
  const [rows, setRows] = useState<SpendingRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    const params: Record<string, string> = {};
    if (start) params.start = start;
    if (end) params.end = end;
    fetchSpendingByCategory(params)
      .then(setRows)
      .finally(() => setLoading(false));
  }, [start, end]);

  const treemapData = useMemo(() => {
    const totals: Record<string, number> = {};
    for (const r of rows) {
      totals[r.category] = (totals[r.category] ?? 0) + r.total;
    }
    return Object.entries(totals)
      .map(([name, value]) => ({ name, value: Math.round(value * 100) / 100 }))
      .sort((a, b) => b.value - a.value);
  }, [rows]);

  if (loading) return <div style={card}><p style={hint}>Loading…</p></div>;
  if (!treemapData.length) return <div style={card}><p style={hint}>No spending data yet.</p></div>;

  return (
    <div style={card}>
      <h2 style={title}>Spending by Category</h2>
      <ResponsiveContainer width="100%" height={300}>
        <Treemap
          data={treemapData}
          dataKey="value"
          aspectRatio={4 / 3}
          content={<CustomCell colors={COLORS} />}
        >
          <Tooltip formatter={(v: number) => `£${v.toFixed(2)}`} />
        </Treemap>
      </ResponsiveContainer>
    </div>
  );
}

function CustomCell({ x, y, width, height, name, value, index, colors }: any) {
  const color = colors[index % colors.length];
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill={color} stroke="#fff" strokeWidth={2} rx={4} />
      {width > 60 && height > 30 && (
        <>
          <text x={x + width / 2} y={y + height / 2 - 6} textAnchor="middle" fill="#fff" fontSize={11} fontWeight={600}>
            {name}
          </text>
          <text x={x + width / 2} y={y + height / 2 + 10} textAnchor="middle" fill="#ffffffcc" fontSize={10}>
            £{Number(value).toFixed(0)}
          </text>
        </>
      )}
    </g>
  );
}

const card: React.CSSProperties = {
  background: "#fff",
  borderRadius: 12,
  padding: "20px 24px",
  boxShadow: "0 2px 8px rgba(0,0,0,0.08)",
};

const title: React.CSSProperties = {
  fontSize: 16,
  fontWeight: 600,
  marginBottom: 16,
  color: "#1a1a2e",
};

const hint: React.CSSProperties = { color: "#888", fontSize: 14 };

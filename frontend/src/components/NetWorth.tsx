import { useEffect, useState } from "react";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { fetchNetWorth, NetWorthRow } from "../api/client";

interface Props { start?: string; end?: string; }

const fmt = (v: number) => `£${v.toLocaleString("en-GB", { maximumFractionDigits: 0 })}`;

export default function NetWorth({ start, end }: Props) {
  const [data, setData] = useState<NetWorthRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchNetWorth(start, end).then(setData).finally(() => setLoading(false));
  }, [start, end]);

  const latest = data[data.length - 1];

  return (
    <div style={card}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <h2 style={title}>Net Worth Over Time</h2>
          <p style={subtitle}>Cumulative running balance by account type</p>
        </div>
        {latest && (
          <div style={{ textAlign: "right" }}>
            <div style={{ fontSize: 22, fontWeight: 700, color: latest.total >= 0 ? "#60b98a" : "#e8735a" }}>
              {fmt(latest.total)}
            </div>
            <div style={{ fontSize: 11, color: "#888" }}>current total · {latest.month}</div>
          </div>
        )}
      </div>

      {loading ? (
        <div style={hint}>Loading…</div>
      ) : !data.length ? (
        <div style={hint}>No net worth data in selected range.</div>
      ) : (
        <>
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="bankGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#4f86c6" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#4f86c6" stopOpacity={0.05} />
                </linearGradient>
                <linearGradient id="investGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#60b98a" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#60b98a" stopOpacity={0.05} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="month" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={fmt} width={80} />
              <Tooltip formatter={(v: number) => fmt(v)} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Area type="monotone" dataKey="bank" name="Bank" stackId="1" stroke="#4f86c6" fill="url(#bankGrad)" strokeWidth={2} />
              <Area type="monotone" dataKey="investment" name="Investment" stackId="1" stroke="#60b98a" fill="url(#investGrad)" strokeWidth={2} />
              <Area type="monotone" dataKey="total" name="Total" stackId="0" stroke="#1a1a2e" fill="none" strokeWidth={2} strokeDasharray="4 2" />
            </AreaChart>
          </ResponsiveContainer>

          {/* Breakdown table */}
          {latest && (
            <div style={{ display: "flex", gap: 16, marginTop: 20, flexWrap: "wrap" }}>
              {[
                { label: "Bank", value: latest.bank, color: "#4f86c6" },
                { label: "Investment", value: latest.investment, color: "#60b98a" },
                { label: "Credit Card", value: latest.credit_card, color: "#e8735a" },
              ].map(({ label, value, color }) => (
                <div key={label} style={{ background: "#f8fafc", borderRadius: 8, padding: "10px 16px", flex: 1, minWidth: 120 }}>
                  <div style={{ fontSize: 11, color: "#888", marginBottom: 4 }}>{label}</div>
                  <div style={{ fontSize: 16, fontWeight: 700, color }}>{fmt(value)}</div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

const card: React.CSSProperties = { background: "#fff", borderRadius: 12, padding: "20px 24px", boxShadow: "0 2px 8px rgba(0,0,0,0.08)" };
const title: React.CSSProperties = { fontSize: 16, fontWeight: 600, color: "#1a1a2e", marginBottom: 2 };
const subtitle: React.CSSProperties = { fontSize: 12, color: "#888", margin: 0 };
const hint: React.CSSProperties = { color: "#aaa", textAlign: "center", padding: 40, fontSize: 14 };

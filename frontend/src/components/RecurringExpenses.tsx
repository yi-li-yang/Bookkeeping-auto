import { useEffect, useState } from "react";
import { fetchRecurringExpenses, RecurringExpense } from "../api/client";

export default function RecurringExpenses() {
  const [data, setData] = useState<RecurringExpense[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchRecurringExpenses().then(setData).finally(() => setLoading(false));
  }, []);

  const totalMonthly = data.reduce((s, r) => s + r.monthly_cost, 0);

  return (
    <div style={card}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 16 }}>
        <h2 style={title}>Recurring Expenses</h2>
        {data.length > 0 && (
          <span style={{ fontSize: 13, color: "#64748b" }}>
            £{totalMonthly.toFixed(0)}/mo · £{(totalMonthly * 12).toFixed(0)}/yr
          </span>
        )}
      </div>
      {loading ? (
        <div style={hint}>Loading…</div>
      ) : !data.length ? (
        <div style={hint}>No recurring expenses detected yet.</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: "2px solid #f0f0f0", background: "#fafbfc" }}>
                {["Description", "Category", "Monthly", "Annual", "Seen", "Trend"].map((h) => (
                  <th key={h} style={th}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.map((r, i) => (
                <tr key={i} style={{ borderBottom: "1px solid #f7f7f7" }}>
                  <td style={{ ...td, maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {r.description}
                  </td>
                  <td style={td}>
                    <span style={badge}>{r.category}</span>
                  </td>
                  <td style={{ ...td, textAlign: "right", fontWeight: 600, color: "#e8735a" }}>
                    £{r.monthly_cost.toFixed(2)}
                  </td>
                  <td style={{ ...td, textAlign: "right", color: "#888" }}>
                    £{r.annual_cost.toFixed(0)}
                  </td>
                  <td style={{ ...td, color: "#888" }}>
                    {r.occurrences}× · {r.last_seen}
                  </td>
                  <td style={{ ...td, textAlign: "center" }}>
                    {r.is_increasing ? (
                      <span title="Cost is increasing" style={{ color: "#e8735a" }}>↑</span>
                    ) : (
                      <span style={{ color: "#60b98a" }}>—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const card: React.CSSProperties = { background: "#fff", borderRadius: 12, padding: "20px 24px", boxShadow: "0 2px 8px rgba(0,0,0,0.08)" };
const title: React.CSSProperties = { fontSize: 16, fontWeight: 600, color: "#1a1a2e" };
const hint: React.CSSProperties = { color: "#aaa", textAlign: "center", padding: 40, fontSize: 14 };
const th: React.CSSProperties = { textAlign: "left", padding: "8px 12px", fontWeight: 600, color: "#555", fontSize: 12 };
const td: React.CSSProperties = { padding: "8px 12px", verticalAlign: "middle" };
const badge: React.CSSProperties = { background: "#f7f7f7", border: "1px solid #e2e8f0", borderRadius: 4, padding: "2px 8px", fontSize: 11 };

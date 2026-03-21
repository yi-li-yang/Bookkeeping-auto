import { useState } from "react";
import { fetchMonthlySummary, fetchAnomalies, Anomaly } from "../api/client";

export default function MonthlySummary() {
  const now = new Date();
  const defaultMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const [month, setMonth] = useState(defaultMonth);
  const [summary, setSummary] = useState<string | null>(null);
  const [anomalies, setAnomalies] = useState<Anomaly[]>([]);
  const [loading, setLoading] = useState(false);

  const generate = async () => {
    setLoading(true);
    setSummary(null);
    setAnomalies([]);
    try {
      const [s, a] = await Promise.all([
        fetchMonthlySummary(month),
        fetchAnomalies(`${month}-01`, `${month}-31`),
      ]);
      setSummary(s.summary);
      setAnomalies(a);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={card}>
      <h2 style={title}>Monthly Summary</h2>
      <p style={subtitle}>AI-generated plain-English summary of your month</p>

      <div style={{ display: "flex", gap: 8, marginTop: 16, marginBottom: 20 }}>
        <input
          type="month"
          style={monthInput}
          value={month}
          onChange={(e) => setMonth(e.target.value)}
        />
        <button style={genBtn} onClick={generate} disabled={loading}>
          {loading ? "Generating…" : "Generate Summary"}
        </button>
      </div>

      {summary && (
        <div style={summaryBox}>
          <p style={{ margin: 0, fontSize: 14, lineHeight: 1.7, color: "#1a1a2e" }}>{summary}</p>
        </div>
      )}

      {anomalies.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: "#555", marginBottom: 8 }}>
            Unusual transactions this month
          </div>
          {anomalies.slice(0, 5).map((a) => (
            <div key={a.id} style={anomalyRow(a.severity)}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
                <span style={{ fontWeight: 600, fontSize: 13 }}>{a.description.slice(0, 50)}</span>
                <span style={{ fontWeight: 700, color: "#e8735a", fontSize: 13 }}>
                  £{Math.abs(a.amount).toFixed(2)}
                </span>
              </div>
              <div style={{ fontSize: 11, color: "#888" }}>{a.reason}</div>
            </div>
          ))}
        </div>
      )}

      {!loading && summary === null && (
        <div style={{ color: "#bbb", fontSize: 13, textAlign: "center", paddingTop: 8 }}>
          Select a month and click Generate Summary
        </div>
      )}
    </div>
  );
}

const card: React.CSSProperties = { background: "#fff", borderRadius: 12, padding: "20px 24px", boxShadow: "0 2px 8px rgba(0,0,0,0.08)" };
const title: React.CSSProperties = { fontSize: 16, fontWeight: 600, color: "#1a1a2e", marginBottom: 2 };
const subtitle: React.CSSProperties = { fontSize: 12, color: "#888", margin: 0 };
const monthInput: React.CSSProperties = { border: "1px solid #e2e8f0", borderRadius: 6, padding: "7px 10px", fontSize: 13, outline: "none" };
const genBtn: React.CSSProperties = { background: "#4f86c6", color: "#fff", border: "none", borderRadius: 6, padding: "7px 16px", fontSize: 13, fontWeight: 600, cursor: "pointer" };
const summaryBox: React.CSSProperties = { background: "#f0f6ff", border: "1px solid #bfdbfe", borderRadius: 8, padding: "14px 16px" };
const anomalyRow = (severity: string): React.CSSProperties => ({
  background: severity === "high" ? "#fff7f7" : "#fffbf0",
  border: `1px solid ${severity === "high" ? "#fecaca" : "#fde68a"}`,
  borderRadius: 6,
  padding: "10px 12px",
  marginBottom: 8,
});

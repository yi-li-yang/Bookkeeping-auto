import { useEffect, useState } from "react";
import { fetchDataQuality, DataQualityData } from "../api/client";

export default function DataQuality() {
  const [data, setData] = useState<DataQualityData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchDataQuality().then(setData).finally(() => setLoading(false));
  }, []);

  return (
    <div style={card}>
      <h2 style={title}>Data Quality</h2>
      <p style={subtitle}>Categorisation confidence and coverage across all ingested transactions</p>

      {loading ? (
        <div style={hint}>Loading…</div>
      ) : !data || data.total === 0 ? (
        <div style={hint}>No transactions ingested yet.</div>
      ) : (
        <>
          {/* Categorised progress bar */}
          <div style={{ marginTop: 20, marginBottom: 24 }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
              <span style={{ fontSize: 13, fontWeight: 600, color: "#374151" }}>Categorised</span>
              <span style={{ fontSize: 13, fontWeight: 700, color: data.categorised_pct >= 90 ? "#60b98a" : data.categorised_pct >= 70 ? "#f0b429" : "#e8735a" }}>
                {data.categorised_pct}%
              </span>
            </div>
            <div style={progressTrack}>
              <div style={progressFill(data.categorised_pct, data.categorised_pct >= 90 ? "#60b98a" : data.categorised_pct >= 70 ? "#f0b429" : "#e8735a")} />
            </div>
            {data.avg_confidence !== null && (
              <div style={{ marginTop: 6, fontSize: 12, color: "#888" }}>
                Avg confidence: {(data.avg_confidence * 100).toFixed(0)}%
              </div>
            )}
          </div>

          {/* Stats grid */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            <MetricRow
              label="Total transactions"
              value={data.total}
              color="#4f86c6"
            />
            <MetricRow
              label="Uncategorised"
              value={data.uncategorised}
              color={data.uncategorised > 0 ? "#e8735a" : "#60b98a"}
              suffix={data.total ? ` (${((data.uncategorised / data.total) * 100).toFixed(0)}%)` : ""}
            />
            <MetricRow
              label="Low confidence (<70%)"
              value={data.low_confidence}
              color={data.low_confidence > 5 ? "#f0b429" : "#60b98a"}
            />
            <MetricRow
              label="User-corrected"
              value={data.user_edited}
              color="#9b6dff"
              suffix=" corrections"
            />
          </div>

          {/* Advice */}
          <div style={advice}>
            {data.uncategorised > 0
              ? `${data.uncategorised} transaction(s) have no category — click them in the Transactions table to assign one.`
              : data.low_confidence > 5
              ? `${data.low_confidence} transaction(s) were classified with low confidence — review and correct them to improve future accuracy.`
              : "Categorisation looks healthy. Keep correcting any misclassifications to improve future accuracy."}
          </div>
        </>
      )}
    </div>
  );
}

function MetricRow({ label, value, color, suffix = "" }: { label: string; value: number; color: string; suffix?: string }) {
  return (
    <div style={{ background: "#f8fafc", borderRadius: 8, padding: "12px 14px" }}>
      <div style={{ fontSize: 11, color: "#888", marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, color }}>
        {value}<span style={{ fontSize: 12, fontWeight: 400, color: "#888" }}>{suffix}</span>
      </div>
    </div>
  );
}

const progressTrack: React.CSSProperties = { background: "#f0f0f0", borderRadius: 99, height: 10, overflow: "hidden" };
const progressFill = (pct: number, color: string): React.CSSProperties => ({
  background: color, height: "100%", width: `${pct}%`, borderRadius: 99, transition: "width 0.5s",
});

const card: React.CSSProperties = { background: "#fff", borderRadius: 12, padding: "20px 24px", boxShadow: "0 2px 8px rgba(0,0,0,0.08)" };
const title: React.CSSProperties = { fontSize: 16, fontWeight: 600, color: "#1a1a2e", marginBottom: 2 };
const subtitle: React.CSSProperties = { fontSize: 12, color: "#888", margin: 0 };
const hint: React.CSSProperties = { color: "#aaa", textAlign: "center", padding: 40, fontSize: 14 };
const advice: React.CSSProperties = { marginTop: 16, background: "#f0f6ff", border: "1px solid #bfdbfe", borderRadius: 6, padding: "10px 12px", fontSize: 12, color: "#374151", lineHeight: 1.6 };

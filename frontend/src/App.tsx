import { useState } from "react";
import SpendingBreakdown from "./components/SpendingBreakdown";
import IncomeVsExpenses from "./components/IncomeVsExpenses";
import CashFlowWaterfall from "./components/CashFlowWaterfall";
import TransactionTable from "./components/TransactionTable";
import CategoryTrends from "./components/CategoryTrends";
import RecurringExpenses from "./components/RecurringExpenses";
import SpendingVelocity from "./components/SpendingVelocity";
import InvestmentDashboard from "./components/InvestmentDashboard";
import MonthlySummary from "./components/MonthlySummary";
import Projection from "./components/Projection";
import WhatIf from "./components/WhatIf";
import AnnualSummary from "./components/AnnualSummary";
import DataQuality from "./components/DataQuality";
import MonthComparison from "./components/MonthComparison";
import { triggerIngest, IngestResponse } from "./api/client";

type Tab = "overview" | "trends" | "investments" | "insights" | "summary";

const TABS: { key: Tab; label: string; desc: string }[] = [
  { key: "overview",     label: "Overview",     desc: "Spending, income & transactions" },
  { key: "trends",       label: "Trends",       desc: "Category patterns & recurring charges" },
  { key: "investments",  label: "Investments",  desc: "Net worth & portfolio" },
  { key: "insights",     label: "Insights",     desc: "AI summary, projections & what-if" },
  { key: "summary",      label: "Summary",      desc: "Annual review, data quality & comparison" },
];

export default function App() {
  const now = new Date();
  const defaultStart = `${now.getFullYear() - 1}-01-01`;
  const defaultEnd = `${now.getFullYear()}-12-31`;

  const [start, setStart] = useState(defaultStart);
  const [end, setEnd] = useState(defaultEnd);
  const [tab, setTab] = useState<Tab>("overview");
  const [ingesting, setIngesting] = useState(false);
  const [ingestResult, setIngestResult] = useState<IngestResponse | null>(null);
  const [ingestError, setIngestError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const handleIngest = async () => {
    setIngesting(true);
    setIngestResult(null);
    setIngestError(null);
    try {
      const result = await triggerIngest();
      setIngestResult(result);
      setRefreshKey((k) => k + 1);
    } catch (err: any) {
      setIngestError(err?.response?.data?.detail ?? err.message ?? "Unknown error");
    } finally {
      setIngesting(false);
    }
  };

  return (
    <div style={{ minHeight: "100vh", background: "#f5f7fa" }}>
      {/* Navbar */}
      <nav style={navbar}>
        <span style={logo}>📊 Bookkeeping Auto</span>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <label style={navLabel}>From</label>
          <input type="date" style={dateInput} value={start} onChange={(e) => setStart(e.target.value)} />
          <label style={navLabel}>To</label>
          <input type="date" style={dateInput} value={end} onChange={(e) => setEnd(e.target.value)} />
          <button style={ingestBtn} onClick={handleIngest} disabled={ingesting}>
            {ingesting ? "Processing…" : "⬆ Ingest Files"}
          </button>
        </div>
      </nav>

      {/* Tab bar */}
      <div style={tabBar}>
        {TABS.map((t) => (
          <button
            key={t.key}
            style={tabBtn(tab === t.key)}
            onClick={() => setTab(t.key)}
            title={t.desc}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Ingest banners */}
      {ingestResult && (
        <div style={banner("#d1fae5", "#065f46")}>
          Ingested {ingestResult.processed} file(s) — {ingestResult.files.reduce((s, f) => s + f.row_count, 0)} transactions.
          {ingestResult.errors > 0 && ` ${ingestResult.errors} error(s).`}
          {ingestResult.skipped > 0 && ` ${ingestResult.skipped} skipped.`}
          <button style={closeBtn} onClick={() => setIngestResult(null)}>✗</button>
        </div>
      )}
      {ingestError && (
        <div style={banner("#fee2e2", "#991b1b")}>
          Ingest error: {ingestError}
          <button style={closeBtn} onClick={() => setIngestError(null)}>✗</button>
        </div>
      )}

      {/* Main content */}
      <main style={mainStyle} key={refreshKey}>

        {/* Tier 1 — Overview */}
        {tab === "overview" && (
          <>
            <div style={grid2}>
              <SpendingBreakdown start={start} end={end} />
              <IncomeVsExpenses start={start} end={end} />
            </div>
            <div style={row}>
              <CashFlowWaterfall start={start} end={end} />
            </div>
            <div style={row}>
              <TransactionTable start={start} end={end} />
            </div>
          </>
        )}

        {/* Tier 2 — Trends */}
        {tab === "trends" && (
          <>
            <div style={row}>
              <CategoryTrends start={start} end={end} />
            </div>
            <div style={grid2}>
              <RecurringExpenses />
              <SpendingVelocity />
            </div>
          </>
        )}

        {/* Tier 3 — Investments */}
        {tab === "investments" && (
          <div>
            <InvestmentDashboard />
          </div>
        )}

        {/* Tier 4 — Insights */}
        {tab === "insights" && (
          <>
            <div style={grid2}>
              <MonthlySummary />
              <Projection />
            </div>
            <div style={row}>
              <WhatIf start={start} end={end} />
            </div>
          </>
        )}

        {/* Tier 5 — Summary */}
        {tab === "summary" && (
          <>
            <div style={grid2}>
              <AnnualSummary />
              <DataQuality />
            </div>
            <div style={row}>
              <MonthComparison />
            </div>
          </>
        )}
      </main>
    </div>
  );
}

// Styles
const navbar: React.CSSProperties = {
  background: "#1a1a2e",
  color: "#fff",
  padding: "12px 32px",
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  boxShadow: "0 2px 8px rgba(0,0,0,0.2)",
  flexWrap: "wrap",
  gap: 12,
};
const logo: React.CSSProperties = { fontSize: 18, fontWeight: 700, letterSpacing: "-0.3px" };
const navLabel: React.CSSProperties = { fontSize: 13, color: "#94a3b8" };
const dateInput: React.CSSProperties = {
  border: "1px solid #334155",
  background: "#0f172a",
  color: "#fff",
  borderRadius: 6,
  padding: "5px 8px",
  fontSize: 13,
  outline: "none",
};
const ingestBtn: React.CSSProperties = {
  background: "#4f86c6",
  color: "#fff",
  border: "none",
  borderRadius: 6,
  padding: "7px 14px",
  fontSize: 13,
  fontWeight: 600,
  cursor: "pointer",
};
const tabBar: React.CSSProperties = {
  background: "#fff",
  borderBottom: "1px solid #e2e8f0",
  padding: "0 32px",
  display: "flex",
  gap: 0,
};
const tabBtn = (active: boolean): React.CSSProperties => ({
  background: "none",
  border: "none",
  borderBottom: active ? "2px solid #4f86c6" : "2px solid transparent",
  color: active ? "#4f86c6" : "#64748b",
  padding: "13px 18px",
  fontSize: 14,
  fontWeight: active ? 600 : 400,
  cursor: "pointer",
  marginBottom: -1,
  transition: "color 0.15s",
});
const mainStyle: React.CSSProperties = { maxWidth: 1300, margin: "0 auto", padding: "28px 24px" };
const grid2: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(480px, 1fr))",
  gap: 24,
  marginBottom: 24,
};
const row: React.CSSProperties = { marginBottom: 24 };
const banner = (bg: string, color: string): React.CSSProperties => ({
  background: bg,
  color,
  padding: "10px 32px",
  display: "flex",
  alignItems: "center",
  gap: 8,
  fontSize: 13,
  fontWeight: 500,
});
const closeBtn: React.CSSProperties = {
  marginLeft: "auto",
  background: "transparent",
  border: "none",
  cursor: "pointer",
  fontSize: 14,
  color: "inherit",
};

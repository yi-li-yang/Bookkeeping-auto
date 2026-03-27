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

type Tab = "overview" | "trends" | "investments" | "insights" | "summary";

const TABS: { key: Tab; label: string; desc: string }[] = [
  { key: "overview",     label: "Overview",     desc: "Spending, income & transactions" },
  { key: "trends",       label: "Trends",       desc: "Category patterns & recurring charges" },
  { key: "investments",  label: "Investments",  desc: "Net worth & portfolio" },
  { key: "insights",     label: "Insights",     desc: "AI summary, projections & what-if" },
  { key: "summary",      label: "Summary",      desc: "Annual review, data quality & comparison" },
];

const START_ALL = "2000-01-01";
const END_ALL = "2099-12-31";

export default function App() {
  const [tab, setTab] = useState<Tab>("overview");

  return (
    <div style={{ minHeight: "100vh", background: "#f5f7fa" }}>
      {/* Navbar */}
      <nav style={navbar}>
        <span style={logo}>📊 Bookkeeping Auto</span>
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

      {/* Main content */}
      <main style={mainStyle}>

        {/* Tier 1 — Overview */}
        {tab === "overview" && (
          <>
            <div style={grid2}>
              <SpendingBreakdown start={START_ALL} end={END_ALL} />
              <IncomeVsExpenses start={START_ALL} end={END_ALL} />
            </div>
            <div style={row}>
              <CashFlowWaterfall start={START_ALL} end={END_ALL} />
            </div>
            <div style={row}>
              <TransactionTable start={START_ALL} end={END_ALL} />
            </div>
          </>
        )}

        {/* Tier 2 — Trends */}
        {tab === "trends" && (
          <>
            <div style={row}>
              <CategoryTrends start={START_ALL} end={END_ALL} />
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
              <WhatIf start={START_ALL} end={END_ALL} />
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

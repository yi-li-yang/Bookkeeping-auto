import { useEffect, useState } from "react";
import { fetchSpendingByCategory, fetchIncomeVsExpenses, SpendingRow, IncomeExpenseRow } from "../api/client";

interface Props { start?: string; end?: string; }

export default function WhatIf({ start, end }: Props) {
  const [spending, setSpending] = useState<SpendingRow[]>([]);
  const [income, setIncome] = useState<IncomeExpenseRow[]>([]);
  const [adjustments, setAdjustments] = useState<Record<string, number>>({});

  useEffect(() => {
    const params: Record<string, string> = {};
    if (start) params.start = start;
    if (end) params.end = end;
    fetchSpendingByCategory(params).then(setSpending);
    fetchIncomeVsExpenses(params).then(setIncome);
    setAdjustments({});
  }, [start, end]);

  // Average monthly spend per category
  const months = Array.from(new Set(spending.map((r) => r.month)));
  const numMonths = months.length || 1;
  const catTotals: Record<string, number> = {};
  spending.forEach((r) => { catTotals[r.category] = (catTotals[r.category] || 0) + r.total; });
  const catMonthly: Record<string, number> = {};
  Object.entries(catTotals).forEach(([cat, total]) => { catMonthly[cat] = total / numMonths; });

  const avgIncome = income.reduce((s, r) => s + r.income, 0) / (income.length || 1);
  const currentExpenses = Object.values(catMonthly).reduce((s, v) => s + v, 0);
  const newExpenses = Object.entries(catMonthly).reduce((s, [cat, amt]) => {
    return s + amt * (1 + (adjustments[cat] ?? 0) / 100);
  }, 0);
  const currentSavings = avgIncome - currentExpenses;
  const newSavings = avgIncome - newExpenses;
  const impact = newSavings - currentSavings;
  const newSavingsRate = avgIncome > 0 ? (newSavings / avgIncome) * 100 : 0;

  const categories = Object.keys(catMonthly).sort((a, b) => catMonthly[b] - catMonthly[a]);
  const hasAdjustments = Object.values(adjustments).some((v) => v !== 0);

  const reset = () => setAdjustments({});

  return (
    <div style={card}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 4 }}>
        <div>
          <h2 style={title}>What-If Scenarios</h2>
          <p style={subtitle}>Adjust spending categories and see the impact on your monthly savings rate</p>
        </div>
        {hasAdjustments && (
          <button style={resetBtn} onClick={reset}>Reset all</button>
        )}
      </div>

      {/* Summary strip */}
      <div style={{ display: "flex", gap: 12, margin: "16px 0", flexWrap: "wrap" }}>
        <Stat label="Avg monthly income" value={`£${avgIncome.toFixed(0)}`} color="#60b98a" />
        <Stat label="Current expenses" value={`£${currentExpenses.toFixed(0)}`} color="#e8735a" />
        <Stat label="New expenses" value={`£${newExpenses.toFixed(0)}`} color={newExpenses < currentExpenses ? "#60b98a" : "#e8735a"} />
        <Stat
          label="Monthly impact"
          value={`${impact >= 0 ? "+" : ""}£${impact.toFixed(0)}`}
          color={impact >= 0 ? "#60b98a" : "#e8735a"}
        />
        <Stat
          label="New savings rate"
          value={`${newSavingsRate.toFixed(1)}%`}
          color={newSavingsRate > 20 ? "#60b98a" : newSavingsRate < 5 ? "#e8735a" : "#f0b429"}
        />
      </div>

      {categories.length === 0 ? (
        <div style={{ color: "#aaa", textAlign: "center", padding: 32, fontSize: 14 }}>
          No spending data in selected range.
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 10 }}>
          {categories.map((cat) => {
            const adj = adjustments[cat] ?? 0;
            const monthly = catMonthly[cat];
            const newAmt = monthly * (1 + adj / 100);
            return (
              <div key={cat} style={sliderRow}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                  <span style={{ fontSize: 13, fontWeight: 500, color: "#1a1a2e" }}>{cat}</span>
                  <span style={{ fontSize: 12, color: "#888" }}>
                    £{monthly.toFixed(0)} →{" "}
                    <span style={{ fontWeight: 700, color: newAmt < monthly ? "#60b98a" : newAmt > monthly ? "#e8735a" : "#888" }}>
                      £{newAmt.toFixed(0)}
                    </span>
                  </span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <input
                    type="range"
                    min={-100}
                    max={100}
                    step={5}
                    value={adj}
                    onChange={(e) => setAdjustments((p) => ({ ...p, [cat]: Number(e.target.value) }))}
                    style={{ flex: 1 }}
                  />
                  <span style={{ fontSize: 12, width: 36, textAlign: "right", fontWeight: 600, color: adj < 0 ? "#60b98a" : adj > 0 ? "#e8735a" : "#888" }}>
                    {adj > 0 ? "+" : ""}{adj}%
                  </span>
                  {adj !== 0 && (
                    <button
                      style={{ background: "none", border: "none", cursor: "pointer", color: "#ccc", fontSize: 13, padding: 0 }}
                      onClick={() => setAdjustments((p) => { const n = { ...p }; delete n[cat]; return n; })}
                    >
                      ✕
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ background: "#f8fafc", borderRadius: 8, padding: "10px 14px", flex: 1, minWidth: 120 }}>
      <div style={{ fontSize: 11, color: "#888", marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 700, color }}>{value}</div>
    </div>
  );
}

const card: React.CSSProperties = { background: "#fff", borderRadius: 12, padding: "20px 24px", boxShadow: "0 2px 8px rgba(0,0,0,0.08)" };
const title: React.CSSProperties = { fontSize: 16, fontWeight: 600, color: "#1a1a2e", marginBottom: 2 };
const subtitle: React.CSSProperties = { fontSize: 12, color: "#888", margin: 0 };
const sliderRow: React.CSSProperties = { background: "#f8fafc", borderRadius: 8, padding: "12px 14px" };
const resetBtn: React.CSSProperties = { background: "none", border: "1px solid #e2e8f0", borderRadius: 6, padding: "4px 12px", fontSize: 12, cursor: "pointer", color: "#64748b" };

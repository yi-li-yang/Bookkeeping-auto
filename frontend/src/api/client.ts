import axios from "axios";

const BASE = import.meta.env.VITE_API_BASE ?? "/api";

const api = axios.create({ baseURL: BASE });

export interface Transaction {
  id: number;
  date: string;
  description: string;
  amount: number;
  category: string | null;
  account_name: string | null;
  account_type: string | null;
  source_file_id: number;
  confidence: number | null;
  is_user_edited: boolean;
}

export interface SpendingRow {
  month: string;
  category: string;
  total: number;
  count: number;
}

export interface IncomeExpenseRow {
  month: string;
  income: number;
  expenses: number;
  savings: number;
  savings_rate: number;
}

export interface WaterfallExpense {
  category: string;
  amount: number;
}

export interface WaterfallRow {
  month: string;
  start_balance: number;
  income: number;
  expenses: WaterfallExpense[];
  end_balance: number;
  net: number;
}

export interface IngestResponse {
  processed: number;
  skipped: number;
  errors: number;
  files: { id: number; filename: string; file_type: string; status: string; row_count: number; error_message: string | null }[];
}

export const fetchTransactions = (params?: Record<string, string | number>) =>
  api.get<Transaction[]>("/transactions", { params }).then((r) => r.data);

export const patchCategory = (id: number, category: string) =>
  api.patch<Transaction>(`/transactions/${id}`, { category }).then((r) => r.data);

export const fetchCategories = () =>
  api.get<string[]>("/categories").then((r) => r.data);

export const fetchSpendingByCategory = (params?: Record<string, string>) =>
  api.get<SpendingRow[]>("/analytics/spending-by-category", { params }).then((r) => r.data);

export const fetchIncomeVsExpenses = (params?: Record<string, string>) =>
  api.get<IncomeExpenseRow[]>("/analytics/income-vs-expenses", { params }).then((r) => r.data);

export const fetchCashflowWaterfall = (params?: Record<string, string>) =>
  api.get<WaterfallRow[]>("/analytics/cashflow-waterfall", { params }).then((r) => r.data);

export const triggerIngest = (force = false) =>
  api.post<IngestResponse>("/ingest", null, { params: { force } }).then((r) => r.data);

// ---------------------------------------------------------------------------
// Tier 2 — Trend & Pattern Analysis
// ---------------------------------------------------------------------------

export interface CategoryTrendEntry {
  month: string;
  total: number;
  rolling_avg_3m: number;
  is_anomaly: boolean;
}
export interface CategoryTrend {
  category: string;
  months: CategoryTrendEntry[];
  total_spend: number;
}
export interface RecurringExpense {
  description: string;
  category: string;
  monthly_cost: number;
  annual_cost: number;
  occurrences: number;
  last_seen: string;
  is_increasing: boolean;
}
export interface VelocityDay {
  day: number;
  daily: number;
  cumulative: number;
  prior_cumulative: number;
}

// Tier 3 — Investment & Net Worth
export interface NetWorthRow {
  month: string;
  bank: number;
  investment: number;
  credit_card: number;
  total: number;
}

// Tier 4 — Predictive & Actionable Insights
export interface Anomaly {
  id: number;
  date: string;
  description: string;
  amount: number;
  category: string;
  reason: string;
  severity: "high" | "medium";
}
export interface ProjectionRow {
  month: string;
  projected_income: number;
  projected_expenses: number;
  projected_balance: number;
  projected_savings: number;
}
export interface MonthlySummaryData {
  month: string;
  summary: string;
}

// Tier 5 — UX Details
export interface AnnualSummaryData {
  year: number;
  total_income: number;
  total_expenses: number;
  savings: number;
  savings_rate: number;
  months_of_data: number;
  top_categories: { category: string; total: number; count: number }[];
  highest_spending_month: string | null;
  best_savings_month: string | null;
}
export interface DataQualityData {
  total: number;
  uncategorised: number;
  low_confidence: number;
  user_edited: number;
  avg_confidence: number | null;
  categorised_pct: number;
}
export interface MonthComparisonRow {
  category: string;
  month1_total: number;
  month2_total: number;
  change_pct: number | null;
  change_abs: number;
}

// API functions
export const fetchCategoryTrends = (start?: string, end?: string) =>
  api.get<CategoryTrend[]>("/analytics/category-trends", { params: { start, end } }).then((r) => r.data);

export const fetchRecurringExpenses = () =>
  api.get<RecurringExpense[]>("/analytics/recurring-expenses").then((r) => r.data);

export const fetchSpendingVelocity = (year: number, month: number) =>
  api.get<VelocityDay[]>("/analytics/spending-velocity", { params: { year, month } }).then((r) => r.data);

export const fetchNetWorth = (start?: string, end?: string) =>
  api.get<NetWorthRow[]>("/analytics/net-worth", { params: { start, end } }).then((r) => r.data);

export const fetchAnomalies = (start?: string, end?: string) =>
  api.get<Anomaly[]>("/analytics/anomalies", { params: { start, end } }).then((r) => r.data);

export const fetchProjection = (months: number) =>
  api.get<ProjectionRow[]>("/analytics/projection", { params: { months } }).then((r) => r.data);

export const fetchMonthlySummary = (month: string) =>
  api.get<MonthlySummaryData>("/analytics/monthly-summary", { params: { month } }).then((r) => r.data);

export const fetchAnnualSummary = (year: number) =>
  api.get<AnnualSummaryData>("/analytics/annual-summary", { params: { year } }).then((r) => r.data);

export const fetchDataQuality = () =>
  api.get<DataQualityData>("/analytics/data-quality").then((r) => r.data);

export const fetchMonthComparison = (month1: string, month2: string) =>
  api.get<MonthComparisonRow[]>("/analytics/month-comparison", { params: { month1, month2 } }).then((r) => r.data);

// ---------------------------------------------------------------------------
// Dining comparison
// ---------------------------------------------------------------------------
export interface DiningComparisonRow {
  month: string;
  dining_out: number;
  home_food: number;
  ratio: number | null;
}

export const fetchDiningComparison = (start?: string, end?: string) =>
  api.get<DiningComparisonRow[]>("/analytics/dining-comparison", { params: { start, end } }).then((r) => r.data);

// ---------------------------------------------------------------------------
// Credit cards
// ---------------------------------------------------------------------------
export interface CreditCardData {
  id: number;
  account_name: string;
  card_name: string | null;
  credit_limit: number | null;
  current_balance: number;
  usage_pct: number | null;
  promotion_end_date: string | null;
  promo_days_left: number | null;
  fx_fee_pct: number | null;
  annual_fee: number | null;
  notes: string | null;
}

export interface CreditCardIn {
  account_name: string;
  card_name?: string;
  credit_limit?: number;
  promotion_end_date?: string;
  fx_fee_pct?: number;
  annual_fee?: number;
  notes?: string;
}

export const fetchCreditCards = () =>
  api.get<CreditCardData[]>("/credit-cards").then((r) => r.data);

export const createCreditCard = (payload: CreditCardIn) =>
  api.post<CreditCardData>("/credit-cards", payload).then((r) => r.data);

export const updateCreditCard = (id: number, payload: CreditCardIn) =>
  api.put<CreditCardData>(`/credit-cards/${id}`, payload).then((r) => r.data);

export const deleteCreditCard = (id: number) =>
  api.delete(`/credit-cards/${id}`);

// ---------------------------------------------------------------------------
// Investment portfolio
// ---------------------------------------------------------------------------

export interface InvestmentAccount {
  account_name: string;
  value_gbp: number;
  cost_gbp: number;
  return_gbp: number;
  return_pct: number;
}

export interface InvestmentSummary {
  report_date: string | null;
  total_value_gbp: number;
  total_cost_gbp: number;
  total_return_gbp: number;
  return_pct: number;
  accounts: InvestmentAccount[];
}

export interface InvestmentHolding {
  id: number;
  account_name: string;
  instrument: string;
  isin: string | null;
  currency: string | null;
  quantity: number | null;
  avg_price: number | null;
  current_price: number | null;
  fx_rate: number | null;
  cost_gbp: number | null;
  value_gbp: number;
  return_gbp: number | null;
  return_pct: number | null;
}

export interface InvestmentPerformanceRow {
  instrument: string;
  account_name: string;
  return_gbp: number;
  return_pct: number;
  value_gbp: number;
}

export interface AllocationItem {
  instrument: string;
  account_name: string;
  value_gbp: number;
  weight_pct: number;
}

export interface AccountAllocation {
  account_name: string;
  value_gbp: number;
  weight_pct: number;
}

export interface InvestmentAllocation {
  by_instrument: AllocationItem[];
  by_account: AccountAllocation[];
}

export interface InvestmentHistoryRow {
  date: string;
  total_value: number;
  total_cost: number;
  total_return: number;
}

export const fetchInvestmentSummary = () =>
  api.get<InvestmentSummary>("/investments/summary").then((r) => r.data);

export const fetchInvestmentHoldings = () =>
  api.get<InvestmentHolding[]>("/investments/holdings").then((r) => r.data);

export const fetchInvestmentPerformance = () =>
  api.get<InvestmentPerformanceRow[]>("/investments/performance").then((r) => r.data);

export const fetchInvestmentAllocation = () =>
  api.get<InvestmentAllocation>("/investments/allocation").then((r) => r.data);

export const fetchInvestmentHistory = () =>
  api.get<InvestmentHistoryRow[]>("/investments/history").then((r) => r.data);

export default api;

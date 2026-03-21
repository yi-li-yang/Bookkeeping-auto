import { useEffect, useRef, useState } from "react";
import { fetchCategories, fetchTransactions, patchCategory, Transaction } from "../api/client";

interface Props {
  start?: string;
  end?: string;
}

export default function TransactionTable({ start, end }: Props) {
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [filterCat, setFilterCat] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = (s: string, cat: string) => {
    setLoading(true);
    const params: Record<string, string | number> = {};
    if (start) params.start = start;
    if (end) params.end = end;
    if (s) params.search = s;
    if (cat) params.category = cat;
    fetchTransactions(params)
      .then(setTransactions)
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchCategories().then(setCategories);
  }, []);

  useEffect(() => {
    load(search, filterCat);
  }, [start, end, filterCat]);

  const handleSearchChange = (val: string) => {
    setSearch(val);
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => load(val, filterCat), 400);
  };

  const handleCategoryChange = async (id: number, category: string) => {
    const updated = await patchCategory(id, category);
    setTransactions((prev) => prev.map((t) => (t.id === id ? updated : t)));
    setEditingId(null);
    // Refresh categories list in case a new one was created
    fetchCategories().then(setCategories);
  };

  return (
    <div style={card}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
        <h2 style={title}>Transactions</h2>
        <input
          style={input}
          placeholder="Search description…"
          value={search}
          onChange={(e) => handleSearchChange(e.target.value)}
        />
        <select style={input} value={filterCat} onChange={(e) => setFilterCat(e.target.value)}>
          <option value="">All categories</option>
          {categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <span style={{ marginLeft: "auto", fontSize: 13, color: "#888" }}>
          {loading ? "Loading…" : `${transactions.length} rows`}
        </span>
      </div>

      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: "2px solid #f0f0f0", background: "#fafbfc" }}>
              {["Date", "Description", "Amount", "Category", "Account", "Source"].map((h) => (
                <th key={h} style={th}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {transactions.map((t) => (
              <tr key={t.id} style={{ borderBottom: "1px solid #f7f7f7" }}>
                <td style={td}>{t.date}</td>
                <td style={{ ...td, maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {t.description}
                </td>
                <td style={{ ...td, textAlign: "right", fontWeight: 600, color: t.amount >= 0 ? "#60b98a" : "#e8735a" }}>
                  {t.amount >= 0 ? "+" : ""}£{Math.abs(t.amount).toFixed(2)}
                </td>
                <td style={td}>
                  {editingId === t.id ? (
                    <CategoryEditor
                      current={t.category ?? ""}
                      options={categories}
                      onSave={(cat) => handleCategoryChange(t.id, cat)}
                      onCancel={() => setEditingId(null)}
                    />
                  ) : (
                    <span
                      style={catBadge(t.is_user_edited)}
                      onClick={() => setEditingId(t.id)}
                      title="Click to edit"
                    >
                      {t.category ?? "—"}
                      {t.is_user_edited && <span title="User edited" style={{ marginLeft: 4, fontSize: 10 }}>✏️</span>}
                    </span>
                  )}
                </td>
                <td style={{ ...td, color: "#888" }}>{t.account_name ?? "—"}</td>
                <td style={{ ...td, color: "#888", fontSize: 11 }}>{t.account_type ?? "—"}</td>
              </tr>
            ))}
            {!loading && !transactions.length && (
              <tr>
                <td colSpan={6} style={{ textAlign: "center", padding: 32, color: "#aaa" }}>
                  No transactions found.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CategoryEditor({
  current,
  options,
  onSave,
  onCancel,
}: {
  current: string;
  options: string[];
  onSave: (v: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(current);
  const allOptions = options.includes(current) || !current ? options : [current, ...options];

  return (
    <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
      <input
        list="cat-list"
        style={{ ...input, width: 140, fontSize: 12, padding: "2px 6px" }}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") onSave(value);
          if (e.key === "Escape") onCancel();
        }}
        autoFocus
      />
      <datalist id="cat-list">
        {allOptions.map((o) => <option key={o} value={o} />)}
      </datalist>
      <button style={btn("#4f86c6")} onClick={() => onSave(value)}>✓</button>
      <button style={btn("#aaa")} onClick={onCancel}>✗</button>
    </div>
  );
}

// Styles
const card: React.CSSProperties = {
  background: "#fff",
  borderRadius: 12,
  padding: "20px 24px",
  boxShadow: "0 2px 8px rgba(0,0,0,0.08)",
};
const title: React.CSSProperties = { fontSize: 16, fontWeight: 600, color: "#1a1a2e", whiteSpace: "nowrap" };
const input: React.CSSProperties = {
  border: "1px solid #e2e8f0",
  borderRadius: 6,
  padding: "6px 10px",
  fontSize: 13,
  outline: "none",
};
const th: React.CSSProperties = { textAlign: "left", padding: "8px 12px", fontWeight: 600, color: "#555", fontSize: 12 };
const td: React.CSSProperties = { padding: "8px 12px", verticalAlign: "middle" };
const catBadge = (edited: boolean): React.CSSProperties => ({
  display: "inline-block",
  background: edited ? "#eff6ff" : "#f7f7f7",
  border: `1px solid ${edited ? "#bfdbfe" : "#e2e8f0"}`,
  borderRadius: 4,
  padding: "2px 8px",
  fontSize: 12,
  cursor: "pointer",
  userSelect: "none",
});
const btn = (color: string): React.CSSProperties => ({
  background: color,
  color: "#fff",
  border: "none",
  borderRadius: 4,
  padding: "2px 6px",
  cursor: "pointer",
  fontSize: 12,
});

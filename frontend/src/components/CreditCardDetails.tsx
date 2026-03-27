import { useEffect, useState } from "react";
import {
  fetchCreditCards, createCreditCard, updateCreditCard, deleteCreditCard,
  CreditCardData, CreditCardIn,
} from "../api/client";

const EMPTY_FORM: CreditCardIn = {
  account_name: "",
  card_name: "",
  credit_limit: undefined,
  promotion_end_date: "",
  fx_fee_pct: undefined,
  annual_fee: undefined,
  notes: "",
};

export default function CreditCardDetails() {
  const [cards, setCards] = useState<CreditCardData[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [form, setForm] = useState<CreditCardIn>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    fetchCreditCards().then(setCards).finally(() => setLoading(false));
  };

  useEffect(load, []);

  const openAdd = () => {
    setForm(EMPTY_FORM);
    setEditId(null);
    setError(null);
    setShowForm(true);
  };

  const openEdit = (c: CreditCardData) => {
    setForm({
      account_name: c.account_name,
      card_name: c.card_name ?? "",
      credit_limit: c.credit_limit ?? undefined,
      promotion_end_date: c.promotion_end_date ?? "",
      fx_fee_pct: c.fx_fee_pct ?? undefined,
      annual_fee: c.annual_fee ?? undefined,
      notes: c.notes ?? "",
    });
    setEditId(c.id);
    setError(null);
    setShowForm(true);
  };

  const handleSave = async () => {
    if (!form.account_name.trim()) { setError("Account name is required."); return; }
    setSaving(true);
    setError(null);
    try {
      const payload: CreditCardIn = {
        ...form,
        credit_limit: form.credit_limit || undefined,
        fx_fee_pct: form.fx_fee_pct !== undefined ? form.fx_fee_pct : undefined,
        annual_fee: form.annual_fee || undefined,
        promotion_end_date: form.promotion_end_date || undefined,
        notes: form.notes || undefined,
        card_name: form.card_name || undefined,
      };
      if (editId !== null) {
        await updateCreditCard(editId, payload);
      } else {
        await createCreditCard(payload);
      }
      setShowForm(false);
      load();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? "Save failed.");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Delete this card?")) return;
    await deleteCreditCard(id);
    load();
  };

  return (
    <div style={card}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <div>
          <h2 style={title}>Credit Card Details</h2>
          <p style={subtitle}>Track limits, promotions, FX fees & usage</p>
        </div>
        <button style={addBtn} onClick={openAdd}>+ Add Card</button>
      </div>

      {loading ? (
        <div style={hint}>Loading…</div>
      ) : cards.length === 0 && !showForm ? (
        <div style={hint}>No cards added yet. Click <strong>+ Add Card</strong> to get started.</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 16 }}>
          {cards.map((c) => <CardTile key={c.id} card={c} onEdit={() => openEdit(c)} onDelete={() => handleDelete(c.id)} />)}
        </div>
      )}

      {showForm && (
        <div style={modal}>
          <div style={modalBox}>
            <h3 style={{ margin: "0 0 16px", fontSize: 16 }}>{editId ? "Edit Card" : "Add Card"}</h3>

            <Label text="Account Name (must match statement filename)*">
              <input style={input} value={form.account_name} onChange={(e) => setForm({ ...form, account_name: e.target.value })} placeholder="e.g. Chase_2024" />
            </Label>
            <Label text="Card Display Name">
              <input style={input} value={form.card_name ?? ""} onChange={(e) => setForm({ ...form, card_name: e.target.value })} placeholder="e.g. Chase Sapphire Preferred" />
            </Label>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <Label text="Credit Limit ($)">
                <input style={input} type="number" value={form.credit_limit ?? ""} onChange={(e) => setForm({ ...form, credit_limit: e.target.value ? parseFloat(e.target.value) : undefined })} placeholder="e.g. 10000" />
              </Label>
              <Label text="Annual Fee ($)">
                <input style={input} type="number" value={form.annual_fee ?? ""} onChange={(e) => setForm({ ...form, annual_fee: e.target.value ? parseFloat(e.target.value) : undefined })} placeholder="e.g. 95" />
              </Label>
              <Label text="Promotion End Date">
                <input style={input} type="date" value={form.promotion_end_date ?? ""} onChange={(e) => setForm({ ...form, promotion_end_date: e.target.value })} />
              </Label>
              <Label text="FX Fee (%)">
                <input style={input} type="number" step="0.1" value={form.fx_fee_pct ?? ""} onChange={(e) => setForm({ ...form, fx_fee_pct: e.target.value !== "" ? parseFloat(e.target.value) : undefined })} placeholder="e.g. 3.0 or 0 for none" />
              </Label>
            </div>
            <Label text="Notes">
              <textarea style={{ ...input, height: 60, resize: "vertical" }} value={form.notes ?? ""} onChange={(e) => setForm({ ...form, notes: e.target.value })} placeholder="Optional notes…" />
            </Label>

            {error && <div style={{ color: "#e8735a", fontSize: 13, marginBottom: 8 }}>{error}</div>}

            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 8 }}>
              <button style={cancelBtn} onClick={() => setShowForm(false)}>Cancel</button>
              <button style={saveBtn} onClick={handleSave} disabled={saving}>{saving ? "Saving…" : "Save"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function CardTile({ card, onEdit, onDelete }: { card: CreditCardData; onEdit: () => void; onDelete: () => void }) {
  const usagePct = card.usage_pct ?? 0;
  const usageColor = usagePct > 80 ? "#e8735a" : usagePct > 50 ? "#f0b429" : "#60b98a";
  const promoUrgent = card.promo_days_left !== null && card.promo_days_left <= 30;
  const fxFree = card.fx_fee_pct === 0;

  return (
    <div style={tile}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 15, color: "#1a1a2e" }}>{card.card_name || card.account_name}</div>
          <div style={{ fontSize: 11, color: "#888" }}>{card.account_name}</div>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <button style={iconBtn} onClick={onEdit} title="Edit">✏️</button>
          <button style={iconBtn} onClick={onDelete} title="Delete">🗑️</button>
        </div>
      </div>

      {card.credit_limit !== null && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "#555", marginBottom: 4 }}>
            <span>Usage: ${card.current_balance.toLocaleString()} / ${card.credit_limit!.toLocaleString()}</span>
            <span style={{ color: usageColor, fontWeight: 600 }}>{card.usage_pct?.toFixed(1)}%</span>
          </div>
          <div style={{ background: "#f0f0f0", borderRadius: 4, height: 6, overflow: "hidden" }}>
            <div style={{ width: `${Math.min(usagePct, 100)}%`, background: usageColor, height: "100%", borderRadius: 4, transition: "width 0.3s" }} />
          </div>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <InfoChip
          label="Promo ends"
          value={card.promotion_end_date ?? "—"}
          sub={card.promo_days_left !== null ? `${card.promo_days_left}d left` : undefined}
          color={promoUrgent ? "#e8735a" : "#4f86c6"}
        />
        <InfoChip
          label="FX fee"
          value={card.fx_fee_pct !== null ? `${card.fx_fee_pct}%` : "—"}
          sub={fxFree ? "Great for travel!" : card.fx_fee_pct ? "Avoid abroad" : undefined}
          color={fxFree ? "#60b98a" : "#e8735a"}
        />
        {card.annual_fee !== null && (
          <InfoChip label="Annual fee" value={`$${card.annual_fee}`} color="#888" />
        )}
        {card.notes && (
          <div style={{ gridColumn: "1/-1", fontSize: 11, color: "#888", fontStyle: "italic" }}>{card.notes}</div>
        )}
      </div>
    </div>
  );
}

function InfoChip({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={{ background: "#f8f9fb", borderRadius: 8, padding: "8px 10px" }}>
      <div style={{ fontSize: 10, color: "#aaa", marginBottom: 2 }}>{label}</div>
      <div style={{ fontWeight: 600, fontSize: 13, color: color ?? "#1a1a2e" }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: color ?? "#888" }}>{sub}</div>}
    </div>
  );
}

function Label({ text, children }: { text: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 11, color: "#555", marginBottom: 4 }}>{text}</div>
      {children}
    </div>
  );
}

const card: React.CSSProperties = { background: "#fff", borderRadius: 12, padding: "20px 24px", boxShadow: "0 2px 8px rgba(0,0,0,0.08)" };
const title: React.CSSProperties = { fontSize: 16, fontWeight: 600, color: "#1a1a2e", marginBottom: 2 };
const subtitle: React.CSSProperties = { fontSize: 12, color: "#888", margin: 0 };
const hint: React.CSSProperties = { color: "#aaa", textAlign: "center", padding: 40, fontSize: 14 };
const tile: React.CSSProperties = { background: "#f8f9fb", borderRadius: 10, padding: "14px 16px", border: "1px solid #e2e8f0" };
const addBtn: React.CSSProperties = { background: "#4f86c6", color: "#fff", border: "none", borderRadius: 8, padding: "8px 16px", fontSize: 13, cursor: "pointer", fontWeight: 600 };
const iconBtn: React.CSSProperties = { background: "none", border: "none", cursor: "pointer", fontSize: 14, padding: "2px 4px", opacity: 0.7 };
const input: React.CSSProperties = { width: "100%", border: "1px solid #e2e8f0", borderRadius: 6, padding: "6px 10px", fontSize: 13, outline: "none", boxSizing: "border-box" };
const modal: React.CSSProperties = { position: "fixed", inset: 0, background: "rgba(0,0,0,0.35)", zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center" };
const modalBox: React.CSSProperties = { background: "#fff", borderRadius: 12, padding: "24px", width: "100%", maxWidth: 480, boxShadow: "0 8px 32px rgba(0,0,0,0.18)" };
const saveBtn: React.CSSProperties = { background: "#4f86c6", color: "#fff", border: "none", borderRadius: 8, padding: "8px 20px", fontSize: 13, cursor: "pointer", fontWeight: 600 };
const cancelBtn: React.CSSProperties = { background: "none", border: "1px solid #e2e8f0", borderRadius: 8, padding: "8px 16px", fontSize: 13, cursor: "pointer", color: "#64748b" };

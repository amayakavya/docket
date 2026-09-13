import {
  Building2, CreditCard, FileText, Globe, Home, Landmark, Lock,
  Search, Shield, TrendingUp, User, Wallet, X, Star,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { API_BASE } from "./api";

const API = API_BASE;

async function req(path: string) {
  const tokenRes = await fetch(`${API}/api/v1/auth/dev-token?role=escalation_manager`);
  const { access_token } = await tokenRes.json();
  const res = await fetch(`${API}${path}`, {
    headers: { Authorization: `Bearer ${access_token}` },
  });
  return res.json();
}

// ── Formatters ────────────────────────────────────────────────────────────────
const inr = (n: number | null | undefined) =>
  n != null ? `₹${Number(n).toLocaleString("en-IN")}` : "—";

const fmtCard = (n: string) => n.replace(/(.{4})(?=.)/g, "$1 ");

const fmtMobile = (n: string) =>
  n.length === 10 ? `${n.slice(0, 5)} ${n.slice(5)}` : n;

const fmtDate = (d: string | null | undefined) => {
  if (!d) return "—";
  const [y, m, day] = d.split("-");
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${day} ${months[parseInt(m) - 1]} ${y}`;
};

// ── Style helpers ─────────────────────────────────────────────────────────────
const SEG_STYLE: Record<string, React.CSSProperties> = {
  PRIORITY:       { background: "#ede9fe", color: "#6d28d9" },
  HNI:            { background: "#fef3c7", color: "#92400e" },
  NRI:            { background: "#dbeafe", color: "#1d4ed8" },
  CORPORATE:      { background: "#ffedd5", color: "#c2410c" },
  SENIOR_CITIZEN: { background: "#ccfbf1", color: "#0f766e" },
  AGRI:           { background: "#dcfce7", color: "#15803d" },
  RETAIL:         { background: "var(--surface-3)", color: "var(--muted)" },
  SALARY:         { background: "#e0e7ff", color: "#4338ca" },
};

const verificationStyle = (s: string): React.CSSProperties =>
  s === "COMPLETED" ? { color: "var(--success)" }
  : s === "PENDING"  ? { color: "var(--warning)" }
  : s === "EXPIRED"  ? { color: "var(--danger)" }
  : { color: "var(--muted)" };

const statusStyle = (s: string): React.CSSProperties =>
  s === "ACTIVE"  ? { color: "var(--success)" }
  : s === "DORMANT" ? { color: "var(--warning)" }
  : { color: "var(--danger)" };

const acctTypeStyle = (t: string): React.CSSProperties => {
  const map: Record<string, string> = {
    SAVINGS: "#1d4ed8", CURRENT: "#c2410c", SALARY: "#4338ca",
    OVERDRAFT: "#b45309", CASH_CREDIT: "#b45309",
  };
  return { color: map[t] ?? "var(--ink-mid)", fontWeight: 700 };
};

const networkStyle = (n: string): React.CSSProperties => {
  if (n === "VISA") return { background: "#dbeafe", color: "#1d4ed8", border: "1px solid #bfdbfe" };
  if (n === "MASTERCARD") return { background: "#ffedd5", color: "#c2410c", border: "1px solid #fed7aa" };
  if (n === "RUPAY") return { background: "#dcfce7", color: "#15803d", border: "1px solid #bbf7d0" };
  return { background: "var(--surface-3)", color: "var(--muted)" };
};

// ── Small atoms ───────────────────────────────────────────────────────────────
const s = {
  badge: (extra?: React.CSSProperties): React.CSSProperties => ({
    display: "inline-flex", alignItems: "center",
    fontSize: 10, fontWeight: 700, padding: "2px 8px",
    borderRadius: 20, whiteSpace: "nowrap" as const, ...extra,
  }),
  chip: (): React.CSSProperties => ({
    fontSize: 10, padding: "2px 8px",
    background: "var(--surface-3)", color: "var(--ink-mid)",
    border: "1px solid var(--line)", borderRadius: 6,
    whiteSpace: "nowrap" as const,
  }),
  card: (extra?: React.CSSProperties): React.CSSProperties => ({
    background: "var(--surface)", border: "1px solid var(--line)",
    borderRadius: 10, overflow: "hidden", ...extra,
  }),
  cardHeader: (): React.CSSProperties => ({
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "7px 12px", borderBottom: "1px solid var(--line)",
    background: "var(--surface-2)",
  }),
  cardBody: (): React.CSSProperties => ({
    padding: "8px 12px", display: "flex", flexDirection: "column" as const, gap: 5,
  }),
  meta: (): React.CSSProperties => ({
    display: "flex", flexWrap: "wrap" as const, gap: "2px 12px",
    fontSize: 11, color: "var(--muted)",
  }),
  label: (): React.CSSProperties => ({
    fontSize: 9, fontWeight: 700, textTransform: "uppercase" as const,
    letterSpacing: ".06em", color: "var(--muted)",
    display: "flex", alignItems: "center", gap: 5,
    padding: "14px 0 5px", borderBottom: "1px solid var(--line)", marginBottom: 8,
  }),
  mono: (): React.CSSProperties => ({
    fontFamily: "monospace", letterSpacing: ".03em",
  }),
  kv: (): React.CSSProperties => ({
    fontSize: 11.5, display: "grid",
    gridTemplateColumns: "120px 1fr", gap: 6,
    padding: "4px 0",
  }),
};

// ── Bar util ──────────────────────────────────────────────────────────────────
function Bar({ pct }: { pct: number }) {
  const colour = pct > 80 ? "var(--danger)" : pct > 50 ? "var(--warning)" : "var(--success)";
  return (
    <div style={{ height: 4, background: "var(--surface-3)", borderRadius: 2, overflow: "hidden", margin: "3px 0" }}>
      <div style={{ height: "100%", width: `${pct}%`, background: colour, borderRadius: 2, transition: "width .4s" }} />
    </div>
  );
}

// ── Account card ──────────────────────────────────────────────────────────────
function AccountCard({ a }: { a: any }) {
  const odUsedPct = a.od_limit ? Math.round(((a.od_used ?? 0) / a.od_limit) * 100) : null;
  return (
    <div style={s.card()}>
      <div style={s.cardHeader()}>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={acctTypeStyle(a.type)}>{a.type.replace(/_/g, " ")}</span>
          {a.business_name && <span style={{ fontSize: 11, color: "var(--muted)", fontStyle: "italic" }}>{a.business_name}</span>}
        </div>
        <span style={statusStyle(a.status)}>{a.status}</span>
      </div>
      <div style={s.cardBody()}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ ...s.mono(), fontSize: 12.5, fontWeight: 700, color: "var(--ink)" }}>{a.connection_id}</span>
          <span style={{ ...s.mono(), fontSize: 10.5, color: "var(--faint)" }}>{a.exchange_code}</span>
        </div>
        {a.balance != null && a.od_limit == null && (
          <div style={s.meta()}>
            <span>Balance <strong style={{ color: "var(--brand)" }}>{inr(a.balance)}</strong></span>
            {a.minimum_balance > 0 && <span>Min bal {inr(a.minimum_balance)}</span>}
            {a.interest_rate && <span>Rate {a.interest_rate}% p.a.</span>}
          </div>
        )}
        {a.od_limit != null && (
          <>
            <div style={s.meta()}>
              <span>Limit <strong style={{ color: "var(--ink)" }}>{inr(a.od_limit)}</strong></span>
              <span>Used <strong style={{ color: "var(--danger)" }}>{inr(a.od_used ?? 0)}</strong></span>
              <span>Available <strong style={{ color: "var(--success)" }}>{inr(a.od_limit - (a.od_used ?? 0))}</strong></span>
            </div>
            {odUsedPct != null && <><Bar pct={odUsedPct} /><span style={{ fontSize: 10, color: "var(--faint)" }}>{odUsedPct}% utilised</span></>}
            {a.security && <span style={{ fontSize: 11, color: "var(--muted)" }}>Security: {a.security}</span>}
          </>
        )}
        <div style={s.meta()}>
          {a.opening_date && <span>Opened {fmtDate(a.opening_date)}</span>}
          {a.mode_of_operation && <span>Mode: {a.mode_of_operation}</span>}
          {a.employer && <span>Employer: {a.employer}</span>}
          {a.salary_credit_date && <span>Salary credit: {a.salary_credit_date}th</span>}
          {a.alternate_contact && <span>Nominee: {a.alternate_contact}</span>}
        </div>
      </div>
    </div>
  );
}

// ── Card display ──────────────────────────────────────────────────────────────
function CardDisplay({ cd }: { cd: any }) {
  const isCredit = cd.type === "CREDIT";
  const usedPct = isCredit && cd.credit_limit
    ? Math.round(((cd.credit_limit - (cd.available_limit ?? cd.credit_limit)) / cd.credit_limit) * 100)
    : null;
  return (
    <div style={s.card()}>
      <div style={s.cardHeader()}>
        <div>
          <div style={{ fontSize: 12.5, fontWeight: 700, color: "var(--ink)" }}>{cd.product_name ?? cd.type}</div>
          <div style={{ ...s.mono(), fontSize: 11, color: "var(--muted)", marginTop: 2, letterSpacing: ".08em" }}>{fmtCard(cd.card_number)}</div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
          <span style={{ ...s.badge(networkStyle(cd.network)), fontSize: 9.5 }}>{cd.network}</span>
          <span style={{ ...statusStyle(cd.status), fontSize: 10.5, fontWeight: 600 }}>{cd.status}</span>
        </div>
      </div>
      <div style={s.cardBody()}>
        <div style={s.meta()}>
          <span>Expires <strong>{cd.expiry}</strong></span>
          {cd.variant && <span>{cd.variant}</span>}
          {cd.issued_date && <span>Issued {fmtDate(cd.issued_date)}</span>}
        </div>
        {isCredit ? (
          <>
            <div style={s.meta()}>
              <span>Limit <strong style={{ color: "var(--ink)" }}>{inr(cd.credit_limit)}</strong></span>
              <span>Available <strong style={{ color: "var(--success)" }}>{inr(cd.available_limit)}</strong></span>
              <span>Outstanding <strong style={{ color: "var(--danger)" }}>{inr(cd.outstanding_balance)}</strong></span>
            </div>
            {usedPct != null && <><Bar pct={usedPct} /><span style={{ fontSize: 10, color: "var(--faint)" }}>{usedPct}% utilised</span></>}
            <div style={s.meta()}>
              {cd.minimum_due != null && <span>Min due <strong style={{ color: "var(--warning)" }}>{inr(cd.minimum_due)}</strong></span>}
              {cd.billing_date && <span>Billing: {cd.billing_date}th</span>}
              {cd.due_date && <span>Due: {cd.due_date}th</span>}
              {cd.reward_points != null && (
                <span style={{ display: "flex", alignItems: "center", gap: 3, color: "#b45309" }}>
                  <Star size={9} style={{ flexShrink: 0 }} />
                  <strong>{cd.reward_points.toLocaleString("en-IN")} pts</strong>
                </span>
              )}
            </div>
          </>
        ) : (
          <div style={s.meta()}>
            {cd.daily_kiosk_limit && <span>service kiosk {inr(cd.daily_kiosk_limit)}/day</span>}
            {cd.daily_pos_limit && <span>POS {inr(cd.daily_pos_limit)}/day</span>}
          </div>
        )}
        <div style={{ display: "flex", flexWrap: "wrap" as const, gap: 4, marginTop: 2 }}>
          {cd.international_usage != null && (
            <span style={{ fontSize: 10, padding: "1px 6px", borderRadius: 4, background: cd.international_usage ? "var(--success-bg)" : "var(--surface-3)", color: cd.international_usage ? "var(--success)" : "var(--faint)", fontWeight: 600 }}>
              {cd.international_usage ? "✓" : "✗"} Intl
            </span>
          )}
          {cd.contactless != null && (
            <span style={{ fontSize: 10, padding: "1px 6px", borderRadius: 4, background: cd.contactless ? "var(--success-bg)" : "var(--surface-3)", color: cd.contactless ? "var(--success)" : "var(--faint)", fontWeight: 600 }}>
              {cd.contactless ? "✓" : "✗"} NFC
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Section heading ───────────────────────────────────────────────────────────
function SH({ icon: Icon, title }: { icon: any; title: string }) {
  return (
    <div style={s.label()}>
      <Icon size={11} style={{ flexShrink: 0 }} />
      {title}
    </div>
  );
}

// ── KV row ────────────────────────────────────────────────────────────────────
function KV({ k, v, mono }: { k: string; v: React.ReactNode; mono?: boolean }) {
  if (v == null || v === "" || v === "—") return null;
  return (
    <div style={s.kv()}>
      <span style={{ color: "var(--faint)", paddingTop: 1 }}>{k}</span>
      <span style={{ color: "var(--ink)", fontWeight: 500, ...(mono ? s.mono() : {}) }}>{v ?? "—"}</span>
    </div>
  );
}

// ── Customer detail drawer ─────────────────────────────────────────────────────
function CustomerDrawer({ customer_ref, onClose }: { customer_ref: string; onClose: () => void }) {
  const [customer, setCustomer] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setLoading(true);
    setCustomer(null);
    req(`/api/v1/customers/${customer_ref}`)
      .then(setCustomer)
      .finally(() => setLoading(false));
  }, [customer_ref]);

  const drawer = (
    <div
      style={{ position: "fixed", inset: 0, zIndex: 9000, display: "flex", justifyContent: "flex-end" }}
      onClick={onClose}
    >
      {/* Dim overlay */}
      <div style={{ position: "absolute", inset: 0, background: "rgba(23,25,43,.35)" }} />

      {/* Panel */}
      <div
        ref={scrollRef}
        style={{
          position: "relative", width: "100%", maxWidth: 680,
          background: "var(--surface)", borderLeft: "1px solid var(--line-strong)",
          overflowY: "auto", boxShadow: "-8px 0 32px rgba(23,25,43,.12)",
          display: "flex", flexDirection: "column",
        }}
        onClick={e => e.stopPropagation()}
      >
        {loading ? (
          <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted)", fontSize: 13 }}>
            Loading customer profile…
          </div>
        ) : !customer ? null : (() => {
          const c = customer;
          const addr = c.address || {};
          const contracts = c.contracts || [];
          const deposits = c.addon_services || [];
          const cards = c.device_details || [];
          const accounts = c.connection_details || [];
          const static IP = c.static_ip_blocks || [];
          const premisesKit = c.premises_equipment || [];
          const insurance = c.protection_plans || [];
          const nri = c.roaming_profile;

          return (
            <>
              {/* Sticky header */}
              <div style={{
                position: "sticky", top: 0, zIndex: 10,
                background: "var(--surface)", borderBottom: "1px solid var(--line)",
                padding: "14px 20px 12px",
              }}>
                <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <span style={{ fontSize: 15, fontWeight: 800, color: "var(--ink)" }}>{c.name}</span>
                      <span style={s.badge({ ...SEG_STYLE[c.customer_segment] ?? SEG_STYLE.RETAIL })}>
                        {c.customer_segment.replace("_", " ")}
                      </span>
                      <span style={{ ...statusStyle(c.status), fontSize: 11, fontWeight: 700 }}>● {c.status}</span>
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "2px 16px", fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
                      <span>customer reference <strong style={{ ...s.mono(), color: "var(--ink-mid)" }}>{c.customer_ref}</strong></span>
                      {c.date_of_birth && <span>DOB {fmtDate(c.date_of_birth)}</span>}
                      {c.payment_score && (
                        <span style={{ fontWeight: 700, color: c.payment_score >= 750 ? "var(--success)" : c.payment_score >= 650 ? "var(--warning)" : "var(--danger)" }}>
                          CIBIL {c.payment_score}
                        </span>
                      )}
                    </div>
                  </div>
                  <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--muted)", padding: 4, display: "flex", alignItems: "center" }}>
                    <X size={18} />
                  </button>
                </div>
              </div>

              {/* Body */}
              <div style={{ padding: "0 20px 32px", fontSize: 12 }}>

                {/* Identity */}
                <SH icon={User} title="Identity & Contact" />
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0 24px" }}>
                  <KV k="Mobile" v={fmtMobile(c.mobile_number)} mono />
                  <KV k="Email" v={c.email} />
                  <KV k="PAN" v={c.tax_id} mono />
                  <KV k="Aadhaar" v={c.id_last4} mono />
                  <KV k="identity verification Status" v={<span style={verificationStyle(c.verification_status)}>{c.verification_status}</span>} />
                  <KV k="Occupation" v={c.occupation} />
                  {c.annual_income && <KV k="Annual Income" v={inr(c.annual_income)} />}
                </div>
                {addr.city && (
                  <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 6, display: "flex", gap: 5 }}>
                    <span style={{ flexShrink: 0 }}>📍</span>
                    <span>{[addr.street, addr.city, addr.state, addr.pincode, addr.country].filter(Boolean).join(", ")}</span>
                  </div>
                )}

                {/* Service area */}
                <SH icon={Landmark} title="Service area & Relationship" />
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0 24px" }}>
                  <KV k="Service area" v={c.service_area} />
                  <KV k="exchange code" v={c.area_code} mono />
                  {c.account_manager && <KV k="RM" v={c.account_manager} />}
                  <KV k="Internet Service" v={<span style={{ color: c.self_care_portal === "ACTIVE" ? "var(--success)" : "var(--danger)" }}>{c.self_care_portal}</span>} />
                  <KV k="Mobile Service" v={<span style={{ color: c.mobile_app_access === "ACTIVE" ? "var(--success)" : "var(--danger)" }}>{c.mobile_app_access}</span>} />
                  {c.sms_alerts != null && <KV k="SMS Alerts" v={c.sms_alerts ? "On" : "Off"} />}
                </div>

                {/* Accounts */}
                {accounts.length > 0 && (
                  <>
                    <SH icon={Wallet} title={`Connections (${accounts.length})`} />
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {accounts.map((a: any, i: number) => <AccountCard key={i} a={a} />)}
                    </div>
                  </>
                )}

                {/* Cards */}
                {cards.length > 0 && (
                  <>
                    <SH icon={CreditCard} title={`Cards (${cards.length})`} />
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {cards.map((cd: any, i: number) => <CardDisplay key={i} cd={cd} />)}
                    </div>
                  </>
                )}

                {/* UPI */}
                {c.payment_handles?.length > 0 && (
                  <>
                    <SH icon={Building2} title={`UPI IDs (${c.payment_handles.length})`} />
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                      {c.payment_handles.map((u: string) => <span key={u} style={s.chip()}>{u}</span>)}
                    </div>
                  </>
                )}

                {/* Contracts */}
                {contracts.length > 0 && (
                  <>
                    <SH icon={Home} title={`Contract Accounts (${contracts.length})`} />
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {contracts.map((l: any, i: number) => {
                        const paidPct = l.sanctioned_amount
                          ? Math.round(((l.sanctioned_amount - l.outstanding_amount) / l.sanctioned_amount) * 100)
                          : null;
                        return (
                          <div key={i} style={s.card()}>
                            <div style={s.cardHeader()}>
                              <div>
                                <div style={{ fontSize: 12.5, fontWeight: 700, color: "var(--ink)" }}>
                                  {(l.product_name ?? l.type).replace(/_/g, " ")}
                                </div>
                                <div style={{ ...s.mono(), fontSize: 10.5, color: "var(--faint)", marginTop: 1 }}>{l.contract_number}</div>
                                {l.contract_connection_id && l.contract_connection_id !== l.contract_number && (
                                  <div style={{ ...s.mono(), fontSize: 10, color: "var(--faint)" }}>A/c {l.contract_connection_id}</div>
                                )}
                              </div>
                              <span style={{
                                ...s.badge(),
                                ...(l.status === "ACTIVE" ? { background: "var(--success-bg)", color: "var(--success)" }
                                  : l.status === "NPA" ? { background: "var(--danger-bg)", color: "var(--danger)" }
                                  : { background: "var(--warning-bg)", color: "var(--warning)" })
                              }}>{l.status}</span>
                            </div>
                            <div style={s.cardBody()}>
                              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
                                {[
                                  { label: "Sanctioned", val: inr(l.sanctioned_amount), col: "var(--ink)" },
                                  { label: "Outstanding", val: inr(l.outstanding_amount), col: "var(--warning)" },
                                  { label: "instalment", val: inr(l.instalment_amount), col: "var(--brand)" },
                                ].map(({ label, val, col }) => (
                                  <div key={label} style={{ fontSize: 11, color: "var(--muted)" }}>
                                    {label} <div style={{ fontSize: 13, fontWeight: 800, color: col }}>{val}</div>
                                  </div>
                                ))}
                              </div>
                              {paidPct != null && (
                                <>
                                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--faint)" }}>
                                    <span>{paidPct}% repaid</span>
                                    <span>{l.completed_instalments ?? 0}/{l.tenure_months} instalments</span>
                                  </div>
                                  <Bar pct={paidPct} />
                                </>
                              )}
                              <div style={s.meta()}>
                                <span>Rate <strong style={{ color: "var(--ink)" }}>{l.interest_rate}%</strong>
                                  {l.rate_type && <span style={{ color: "var(--faint)", marginLeft: 3 }}>({l.rate_type})</span>}
                                </span>
                                {l.instalment_due_date && <span>Due: <strong>{l.instalment_due_date}th</strong></span>}
                                {l.next_instalment_date && <span>Next instalment: <strong>{fmtDate(l.next_instalment_date)}</strong></span>}
                                {l.remaining_instalments != null && <span>Remaining: {l.remaining_instalments} instalments</span>}
                              </div>
                              {l.collateral && <div style={{ fontSize: 11, color: "var(--muted)" }}>Collateral: <span style={{ color: "var(--ink-mid)" }}>{l.collateral}</span></div>}
                              {l.insurance_linked && <div style={{ fontSize: 11, color: "var(--muted)" }}>Linked policy: <span style={{ ...s.mono(), color: "var(--ink-mid)" }}>{l.insurance_linked}</span></div>}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </>
                )}

                {/* Deposits */}
                {deposits.length > 0 && (
                  <>
                    <SH icon={FileText} title={`Deposits & Schemes (${deposits.length})`} />
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {deposits.map((d: any, i: number) => (
                        <div key={i} style={s.card()}>
                          <div style={s.cardHeader()}>
                            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                              <span style={{ fontSize: 12, fontWeight: 700, color: "var(--ink)" }}>{d.type.replace(/_/g, " ")}</span>
                              <span style={{ ...s.mono(), fontSize: 10.5, color: "var(--faint)" }}>{d.folio_number}</span>
                            </div>
                            <span style={{
                              ...s.badge(),
                              ...(d.status === "ACTIVE" ? { background: "var(--success-bg)", color: "var(--success)" }
                                : d.status === "MATURED" ? { background: "var(--brand-tint)", color: "var(--brand)" }
                                : { background: "var(--surface-3)", color: "var(--faint)" })
                            }}>{d.status}</span>
                          </div>
                          <div style={s.cardBody()}>
                            <div style={s.meta()}>
                              <span>Principal <strong style={{ color: "var(--brand)" }}>{inr(d.principal_amount)}</strong></span>
                              <span>Rate {d.interest_rate}%</span>
                              {d.tenure && <span>Tenure {d.tenure}</span>}
                              {d.maturity_amount && <span>Maturity <strong style={{ color: "var(--success)" }}>{inr(d.maturity_amount)}</strong></span>}
                            </div>
                            <div style={s.meta()}>
                              {d.opening_date && <span>Opened {fmtDate(d.opening_date)}</span>}
                              {d.maturity_date && <span>Matures {fmtDate(d.maturity_date)}</span>}
                              {d.payout_frequency && <span>Payout: {d.payout_frequency.replace(/_/g, " ")}</span>}
                              {d.auto_renew != null && <span style={{ color: d.auto_renew ? "var(--success)" : "var(--muted)" }}>Auto-renew: {d.auto_renew ? "Yes" : "No"}</span>}
                              {d.tds_applicable && <span style={{ color: "var(--warning)" }}>TDS applicable</span>}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </>
                )}

                {/* Static IP */}
                {static IP.length > 0 && (
                  <>
                    <SH icon={TrendingUp} title={`Static IP Blocks (${static_ip_blocks.length})`} />
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {static IP.map((d: any, i: number) => (
                        <div key={i} style={s.card()}>
                          <div style={{ ...s.cardHeader() }}>
                            <div>
                              <span style={{ ...s.mono(), fontSize: 12.5, fontWeight: 700, color: "var(--ink)" }}>{d.static IP_number}</span>
                              <span style={{ fontSize: 10.5, color: "var(--faint)", marginLeft: 8 }}>DP: {d.dp_id}</span>
                            </div>
                            <span style={statusStyle(d.status)}>{d.status}</span>
                          </div>
                          <div style={s.cardBody()}>
                            <div style={s.meta()}>
                              <span>{d.dp_name}</span>
                              {d.trading_account && <span>Trading A/c <span style={s.mono()}>{d.trading_account}</span></span>}
                              {d.holdings_value_approx && <span>Holdings <strong style={{ color: "var(--success)" }}>{inr(d.holdings_value_approx)}</strong> (approx)</span>}
                              {d.opened_date && <span>Opened {fmtDate(d.opened_date)}</span>}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </>
                )}

                {/* Lockers */}
                {premises_kits.length > 0 && (
                  <>
                    <SH icon={Lock} title={`Premises Kit (${premises_kits.length})`} />
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {premises_kits.map((l: any, i: number) => (
                        <div key={i} style={s.card()}>
                          <div style={s.cardHeader()}>
                            <span style={{ ...s.mono(), fontSize: 12.5, fontWeight: 700, color: "var(--ink)" }}>{l.premises_kit_number}</span>
                            <span style={statusStyle(l.status)}>{l.status}</span>
                          </div>
                          <div style={s.cardBody()}>
                            <div style={s.meta()}>
                              <span>{l.service_area}</span>
                              <span>Size: {l.size}</span>
                              <span>Rent: {inr(l.annual_rent)}/yr</span>
                              {l.key_number && <span>Key: <span style={s.mono()}>{l.key_number}</span></span>}
                              {l.allotted_date && <span>Since {fmtDate(l.allotted_date)}</span>}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </>
                )}

                {/* Insurance */}
                {insurance.length > 0 && (
                  <>
                    <SH icon={Shield} title={`Insurance Policies (${insurance.length})`} />
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      {insurance.map((p: any, i: number) => (
                        <div key={i} style={s.card()}>
                          <div style={s.cardHeader()}>
                            <div>
                              <div style={{ fontSize: 12.5, fontWeight: 700, color: "var(--ink)" }}>{p.product_name ?? `${p.type} Policy`}</div>
                              <div style={{ display: "flex", gap: 8, marginTop: 2 }}>
                                <span style={{ ...s.mono(), fontSize: 10.5, color: "var(--faint)" }}>{p.policy_number}</span>
                                <span style={{ fontSize: 10.5, color: "var(--muted)" }}>{p.insurer.replace(/_/g, " ")}</span>
                              </div>
                            </div>
                            <span style={{ ...s.badge(), ...(p.status === "ACTIVE" ? { background: "var(--success-bg)", color: "var(--success)" } : { background: "var(--surface-3)", color: "var(--faint)" }) }}>{p.status}</span>
                          </div>
                          <div style={s.cardBody()}>
                            <div style={s.meta()}>
                              <span>Sum assured <strong style={{ color: "var(--ink)" }}>{inr(p.sum_assured)}</strong></span>
                              <span>Premium {inr(p.premium_annual)}/{(p.premium_frequency ?? "annual").toLowerCase()}</span>
                              {p.start_date && <span>From {fmtDate(p.start_date)}</span>}
                              {p.maturity_date && <span>To {fmtDate(p.maturity_date)}</span>}
                            </div>
                            {p.alternate_contact && <div style={{ fontSize: 11, color: "var(--muted)" }}>Nominee: <span style={{ color: "var(--ink-mid)" }}>{p.alternate_contact}</span></div>}
                          </div>
                        </div>
                      ))}
                    </div>
                  </>
                )}

                {/* NRI */}
                {nri && (
                  <>
                    <SH icon={Globe} title="NRI Details" />
                    <div style={{ ...s.card(), padding: "10px 14px" }}>
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0 24px" }}>
                        <KV k="Country" v={nri.country_of_residence} />
                        <KV k="Passport" v={nri.passport_number} mono />
                        {nri.passport_expiry && <KV k="Passport expiry" v={fmtDate(nri.passport_expiry)} />}
                        <KV k="Visa" v={nri.visa_type} />
                        {nri.employer_abroad && <KV k="Employer" v={nri.employer_abroad} />}
                        {nri.roaming_partner && <KV k="Roaming partner" v={nri.roaming_partner} />}
                        {nri.fema_declaration_date && <KV k="FEMA date" v={fmtDate(nri.fema_declaration_date)} />}
                      </div>
                    </div>
                  </>
                )}

                {/* Nominee */}
                {c.alternate_contact && (
                  <>
                    <SH icon={User} title="Nominee" />
                    <div style={{ ...s.card(), padding: "10px 14px" }}>
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0 24px" }}>
                        <KV k="Name" v={c.alternate_contact.name} />
                        <KV k="Relationship" v={c.alternate_contact.relationship} />
                        <KV k="DOB" v={fmtDate(c.alternate_contact.dob)} />
                        <KV k="Mobile" v={fmtMobile(c.alternate_contact.mobile)} mono />
                      </div>
                    </div>
                  </>
                )}

                {/* Products */}
                <SH icon={Building2} title="Linked Products" />
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {(c.product_types || []).map((p: string) => (
                    <span key={p} style={s.chip()}>{p.replace(/_/g, " ")}</span>
                  ))}
                </div>

              </div>
            </>
          );
        })()}
      </div>
    </div>
  );

  return createPortal(drawer, document.body);
}

// ── Main component ─────────────────────────────────────────────────────────────
export default function CustomerDatabase() {
  const [customers, setCustomers] = useState<any[]>([]);
  const [filtered, setFiltered] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [segFilter, setSegFilter] = useState("ALL");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [selectedCif, setSelectedCif] = useState<string | null>(null);

  useEffect(() => {
    req("/api/v1/customers?limit=100")
      .then(data => { setCustomers(data); setFiltered(data); })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    let result = customers;
    if (segFilter !== "ALL") result = result.filter(c => c.customer_segment === segFilter);
    if (statusFilter !== "ALL") result = result.filter(c => c.status === statusFilter);
    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter(c =>
        c.name?.toLowerCase().includes(q) ||
        c.customer_ref?.includes(q) ||
        c.mobile_number?.includes(q) ||
        c.email?.toLowerCase().includes(q) ||
        c.tax_id?.toLowerCase().includes(q) ||
        c.service_area?.toLowerCase().includes(q)
      );
    }
    setFiltered(result);
  }, [search, segFilter, statusFilter, customers]);

  const segments = ["ALL", "RESIDENTIAL", "PRIORITY", "BUSINESS", "ENTERPRISE", "STUDENT"];
  const statuses = ["ALL", "ACTIVE", "DORMANT", "BLOCKED"];

  if (loading) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: 200, color: "var(--muted)", fontSize: 13 }}>
      Loading customer database…
    </div>
  );

  return (
    <div style={{ paddingBottom: 24 }}>
      {selectedCif && <CustomerDrawer customer_ref={selectedCif} onClose={() => setSelectedCif(null)} />}

      {/* Stats */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginBottom: 14 }}>
        {[
          { label: "Total Customers", value: customers.length, color: "#1d4ed8" },
          { label: "Active", value: customers.filter(c => c.status === "ACTIVE").length, color: "var(--success)" },
          { label: "With Contracts", value: customers.filter(c => c.contracts?.length).length, color: "var(--warning)" },
          { label: "NRI / Corporate", value: customers.filter(c => ["ENTERPRISE", "BUSINESS"].includes(c.customer_segment)).length, color: "var(--brand)" },
        ].map(({ label, value, color }) => (
          <div key={label} style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 10, padding: "10px 14px", textAlign: "center" }}>
            <div style={{ fontSize: 22, fontWeight: 800, color }}>{value}</div>
            <div style={{ fontSize: 10.5, color: "var(--muted)", marginTop: 2 }}>{label}</div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div style={{ display: "flex", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
        <div style={{ position: "relative", flex: 1, minWidth: 200 }}>
          <Search size={13} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "var(--faint)" }} />
          <input
            style={{
              width: "100%", paddingLeft: 30, paddingRight: 12, paddingTop: 7, paddingBottom: 7,
              fontSize: 12, background: "var(--surface)", border: "1px solid var(--line)",
              borderRadius: 8, color: "var(--ink)", outline: "none", boxSizing: "border-box",
            }}
            placeholder="Search name, customer reference, mobile, email, PAN, service_area…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        {[
          { value: segFilter, set: setSegFilter, options: segments, all: "All Segments", fmt: (s: string) => s.replace(/_/g, " ") },
          { value: statusFilter, set: setStatusFilter, options: statuses, all: "All Statuses", fmt: (s: string) => s },
        ].map(({ value, set, options, all, fmt }, idx) => (
          <select
            key={idx}
            style={{
              fontSize: 11.5, background: "var(--surface)", border: "1px solid var(--line)",
              borderRadius: 8, padding: "7px 12px", color: "var(--ink)", outline: "none",
            }}
            value={value}
            onChange={e => set(e.target.value)}
          >
            {options.map(o => <option key={o} value={o}>{o === "ALL" ? all : fmt(o)}</option>)}
          </select>
        ))}
        <span style={{ fontSize: 11, color: "var(--faint)", alignSelf: "center" }}>{filtered.length} records</span>
      </div>

      {/* Table */}
      <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 10, overflow: "hidden" }}>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--line)", background: "var(--surface-2)" }}>
                {["customer reference", "Name", "Segment", "Mobile", "Area", "Links", "Devices", "Contracts", "Add-ons", "CIBIL", "identity verification", "Status"].map(h => (
                  <th key={h} style={{ textAlign: "left", color: "var(--muted)", fontWeight: 600, padding: "9px 12px", whiteSpace: "nowrap", fontSize: 10.5 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map(c => {
                const contracts = c.contracts?.length || 0;
                const deps  = c.addon_services?.length || 0;
                const accts = c.connection_details?.length || c.connection_ids?.length || 0;
                const cardCount = c.device_details?.length || c.device_serials?.length || 0;
                return (
                  <tr
                    key={c.customer_ref}
                    style={{ borderBottom: "1px solid var(--line)", cursor: "pointer", transition: "background .12s" }}
                    onMouseEnter={e => (e.currentTarget.style.background = "var(--surface-2)")}
                    onMouseLeave={e => (e.currentTarget.style.background = "")}
                    onClick={() => setSelectedCif(c.customer_ref)}
                  >
                    <td style={{ padding: "9px 12px", ...s.mono(), color: "var(--faint)", whiteSpace: "nowrap" }}>{c.customer_ref}</td>
                    <td style={{ padding: "9px 12px", color: "var(--ink)", fontWeight: 600, maxWidth: 180 }}>
                      <span style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.name}</span>
                    </td>
                    <td style={{ padding: "9px 12px" }}>
                      <span style={s.badge({ ...SEG_STYLE[c.customer_segment] ?? SEG_STYLE.RETAIL })}>{c.customer_segment.replace("_", " ")}</span>
                    </td>
                    <td style={{ padding: "9px 12px", ...s.mono(), color: "var(--ink-mid)", whiteSpace: "nowrap" }}>{fmtMobile(c.mobile_number)}</td>
                    <td style={{ padding: "9px 12px", color: "var(--muted)", maxWidth: 150 }}>
                      <span style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.service_area}</span>
                    </td>
                    {[
                      { val: accts, col: "#1d4ed8" },
                      { val: cardCount, col: "#6d28d9" },
                      { val: contracts, col: "var(--warning)" },
                      { val: deps, col: "var(--success)" },
                    ].map(({ val, col }, i) => (
                      <td key={i} style={{ padding: "9px 12px", textAlign: "center" }}>
                        <span style={{ fontWeight: val ? 700 : 400, color: val ? col : "var(--faint)" }}>{val || "—"}</span>
                      </td>
                    ))}
                    <td style={{ padding: "9px 12px", textAlign: "center" }}>
                      {c.payment_score ? (
                        <span style={{ fontWeight: 700, color: c.payment_score >= 750 ? "var(--success)" : c.payment_score >= 650 ? "var(--warning)" : "var(--danger)" }}>
                          {c.payment_score}
                        </span>
                      ) : <span style={{ color: "var(--faint)" }}>—</span>}
                    </td>
                    <td style={{ padding: "9px 12px" }}>
                      <span style={{ fontWeight: 600, ...verificationStyle(c.verification_status) }}>{c.verification_status}</span>
                    </td>
                    <td style={{ padding: "9px 12px" }}>
                      <span style={{ fontWeight: 700, ...statusStyle(c.status) }}>{c.status}</span>
                    </td>
                  </tr>
                );
              })}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={12} style={{ textAlign: "center", color: "var(--faint)", padding: "32px 0", fontSize: 12 }}>
                    No customers match your filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

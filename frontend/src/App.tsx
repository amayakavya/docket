import {
  AlertTriangle,
  Archive,
  ArrowLeft,
  ArrowRight,
  Undo2,
  Redo2,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock,
  FileSearch,
  GitService area,
  GitCompareArrows,
  Inbox,
  Play,
  RefreshCcw,
  Search,
  ShieldAlert,
  Upload,
  Ban,
  Microscope,
  MessageSquare,
  FileText,
  Wrench,
  MonitorCheck,
  ArrowRightLeft,
  Siren,
  Users,
  TrendingUp,
  BookOpen,
  Scale,
  Cpu,
  Building2,
  Lightbulb,
  Send,
  BrainCircuit,
  AlertOctagon,
  Globe,
  CheckCheck,
  XCircle,
  CreditCard,
  Landmark,
  Mail,
  History,
  Info as InfoIcon,
  RotateCcw,
  Navigation,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Group as PanelGroup, Panel, Separator as PanelResizeHandle } from "react-resizable-panels";
import { API_BASE, api } from "./api";
import AnalyticsComponent from "./Analytics";
import CustomerDatabase from "./CustomerDatabase";
import DeptAnalytics from "./DeptAnalytics";
import AdminAssistant from "./AdminAssistant";
import type { AttachmentExtracted, AutoDraft, ClarificationDraft, IntakeFlags, ImpersonationRisk, RepeatDetection, RegulatoryBreach } from "./types";
import type {
  CaseDetail,
  CaseSummary,
  Dashboard,
  Department,
  DepartmentQueue,
  DeptStats,
  PortalRequestType,
  PortalSubmitPayload,
  PortalSubmitResult,
  PortalUrgency,
  SuggestedSolution,
  SystemReadiness,
  TriageLabel,
} from "./types";

// ─── Types ───────────────────────────────────────────────────────────────────
type Portal = null | "central" | "desks" | "request";
type DeskView = "dept_fraud" | "dept_customer" | "dept_compliance" | "dept_technical" | "dept_central" | "dept_contracts" | "dept_nri" | "dept_cards";
type View = "central" | DeskView | "sla" | "incidents" | "history" | "audit" | "triage_queue" | "error_queue" | "analytics" | "customers" | "assistant";
type StatusTone = "neutral" | "success" | "warning" | "error";

// ─── Department config ────────────────────────────────────────────────────────
type DeskAction = { id: string; label: string; Icon: any; variant?: "primary" | "danger" | "warn"; requires?: "case" | "note" | "operator" | "transfer"; confirm?: boolean };
type DeskConfig = {
  slug: string;
  label: string;
  shortLabel: string;
  Icon: any;
  colorClass: string;
  metricKeys: { key: string; label: string; compute: (q: DepartmentQueue) => string | number }[];
  actions: DeskAction[];
  keyFields: string[];
  caseSort: (a: CaseSummary, b: CaseSummary) => number;
};

// Standard 5 actions — same for every desk
const STANDARD_ACTIONS: DeskAction[] = [
  { id: "acknowledge",        label: "Acknowledge",        Icon: CheckCircle2 },
  { id: "start_review",       label: "Start Review",       Icon: Play,          variant: "primary" },
  { id: "send_communication", label: "Send Communication", Icon: MessageSquare, requires: "note" },
  { id: "resolve",            label: "Resolve Case",       Icon: CheckCircle2,  requires: "note" },
  { id: "request_department", label: "Transfer to Desk",   Icon: ArrowRightLeft, requires: "transfer" },
];

const DESKS: Record<string, DeskConfig> = {
  "trust-safety-desk": {
    slug: "trust-safety-desk",
    label: "Trust & Safety Desk",
    shortLabel: "Trust",
    Icon: ShieldAlert,
    colorClass: "desk-trust",
    metricKeys: [
      { key: "total",              label: "Active Cases",     compute: q => q.counts.unresolved ?? 0 },
      { key: "amount_at_risk",     label: "Value in Dispute ₹", compute: q => (q.counts.amount_at_risk ?? 0).toLocaleString("en-IN") },
      { key: "lines_suspended",   label: "Lines Suspended", compute: q => q.counts.lines_suspended ?? 0 },
      { key: "overdue",            label: "SLA Overdue",      compute: q => q.counts.overdue ?? 0 },
    ],
    actions: STANDARD_ACTIONS,
    keyFields: ["customer_name", "amount_involved", "transaction_type", "transaction_reference", "connection_ids_detected"],
    caseSort: (a, b) => (b.risk_score ?? 0) - (a.risk_score ?? 0),
  },

  "customer-resolution-desk": {
    slug: "customer-resolution-desk",
    label: "Customer Resolution Desk",
    shortLabel: "Customer Service",
    Icon: Users,
    colorClass: "desk-resolution",
    metricKeys: [
      { key: "total",    label: "Cases in Queue", compute: q => q.counts.total ?? 0 },
      { key: "resolved", label: "Resolved",       compute: q => q.counts.resolved ?? 0 },
      { key: "overdue",  label: "SLA at Risk",    compute: q => q.counts.overdue ?? 0 },
      { key: "waiting",  label: "Awaiting Action",compute: q => q.counts.waiting ?? 0 },
    ],
    actions: STANDARD_ACTIONS,
    keyFields: ["customer_name", "customer_email", "amount_involved", "requested_action", "service_summary"],
    caseSort: (a, b) => (b.sla_alerts.length - a.sla_alerts.length) || (a.created_at < b.created_at ? -1 : 1),
  },

  "compliance-desk": {
    slug: "compliance-desk",
    label: "Compliance Desk",
    shortLabel: "Compliance",
    Icon: Scale,
    colorClass: "desk-compliance",
    metricKeys: [
      { key: "total",              label: "Regulatory Cases",  compute: q => q.counts.total ?? 0 },
      { key: "regulatory_reports", label: "Reports Filed",     compute: q => q.counts.regulatory_reports ?? 0 },
      { key: "waiting",            label: "Awaiting Response", compute: q => q.counts.waiting ?? 0 },
      { key: "escalated",          label: "Escalated",         compute: q => q.counts.escalated ?? 0 },
    ],
    actions: STANDARD_ACTIONS,
    keyFields: ["customer_name", "amount_involved", "transaction_type", "service_summary", "payment_handles_detected"],
    caseSort: (a, b) => {
      const complianceFirst = ["REGULATORY", "LEGAL_NOTICE", "VERIFICATION_QUERY"];
      const aOrder = complianceFirst.indexOf(a.classification);
      const bOrder = complianceFirst.indexOf(b.classification);
      if (aOrder !== bOrder) return (aOrder === -1 ? 99 : aOrder) - (bOrder === -1 ? 99 : bOrder);
      return a.created_at < b.created_at ? -1 : 1;
    },
  },

  "network-support-desk": {
    slug: "network-support-desk",
    label: "Network Support Desk",
    shortLabel: "Technical Support",
    Icon: Cpu,
    colorClass: "desk-network",
    metricKeys: [
      { key: "total",       label: "Tech Issues",   compute: q => q.counts.total ?? 0 },
      { key: "under_review",label: "Under Review",  compute: q => q.counts.under_review ?? 0 },
      { key: "it_tickets_created", label: "IT Tickets", compute: q => q.counts.it_tickets_created ?? 0 },
      { key: "resolved",    label: "Resolved",      compute: q => q.counts.resolved ?? 0 },
    ],
    actions: STANDARD_ACTIONS,
    keyFields: ["customer_name", "transaction_type", "service_summary", "payment_handles_detected", "connection_ids_detected"],
    caseSort: (a, b) => {
      const techFirst = ["CONNECTION_FAULT", "PAYMENT_FAILURE", "PORTAL_ACCESS"];
      const aOrder = techFirst.indexOf(a.classification);
      const bOrder = techFirst.indexOf(b.classification);
      if (aOrder !== bOrder) return (aOrder === -1 ? 99 : aOrder) - (bOrder === -1 ? 99 : bOrder);
      return (b.risk_score ?? 0) - (a.risk_score ?? 0);
    },
  },

  "central-operations-desk": {
    slug: "central-operations-desk",
    label: "Central Operations Desk",
    shortLabel: "Central Ops",
    Icon: Building2,
    colorClass: "desk-central",
    metricKeys: [
      { key: "escalated", label: "Escalations Active", compute: q => q.counts.escalated ?? 0 },
      { key: "critical",  label: "Critical Cases",     compute: q => q.counts.critical ?? 0 },
      { key: "total",     label: "Total in Queue",     compute: q => q.counts.total ?? 0 },
      { key: "overdue",   label: "SLA Breached",       compute: q => q.counts.overdue ?? 0 },
    ],
    actions: STANDARD_ACTIONS,
    keyFields: ["customer_name", "amount_involved", "service_summary", "requested_action", "transaction_reference"],
    caseSort: (a, b) => {
      const pOrder = { CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3 };
      return (pOrder[a.priority as keyof typeof pOrder] ?? 4) - (pOrder[b.priority as keyof typeof pOrder] ?? 4);
    },
  },

  "billing-contracts-desk": {
    slug: "billing-contracts-desk",
    label: "Billing & Contracts Desk",
    shortLabel: "Billing",
    Icon: Landmark,
    colorClass: "desk-contracts",
    metricKeys: [
      { key: "total",    label: "Contract Cases",     compute: q => q.counts.total ?? 0 },
      { key: "overdue",  label: "SLA Overdue",    compute: q => q.counts.overdue ?? 0 },
      { key: "resolved", label: "Resolved",       compute: q => q.counts.resolved ?? 0 },
      { key: "waiting",  label: "Awaiting Docs",  compute: q => q.counts.waiting ?? 0 },
    ],
    actions: [
      ...STANDARD_ACTIONS,
      { id: "request_evidence", label: "Request Evidence", Icon: FileText, requires: "note" as const },
    ],
    keyFields: ["customer_name", "amount_involved", "transaction_reference", "service_summary", "connection_ids_detected"],
    caseSort: (a, b) => {
      const loadFirst = ["INSTALMENT_ISSUE", "CONTRACT_ACTIVATION", "CONTRACT_CLOSURE", "CONTRACT_QUERY"];
      const aO = loadFirst.indexOf(a.classification); const bO = loadFirst.indexOf(b.classification);
      if (aO !== bO) return (aO === -1 ? 99 : aO) - (bO === -1 ? 99 : bO);
      return (b.risk_score ?? 0) - (a.risk_score ?? 0);
    },
  },

  "roaming-international-desk": {
    slug: "roaming-international-desk",
    label: "Roaming & International Desk",
    shortLabel: "Roaming",
    Icon: Globe,
    colorClass: "desk-roaming",
    metricKeys: [
      { key: "total",   label: "Roaming Cases",        compute: q => q.counts.total ?? 0 },
      { key: "waiting", label: "Awaiting Response", compute: q => q.counts.waiting ?? 0 },
      { key: "overdue", label: "SLA Overdue",       compute: q => q.counts.overdue ?? 0 },
      { key: "resolved",label: "Resolved",          compute: q => q.counts.resolved ?? 0 },
    ],
    actions: [
      ...STANDARD_ACTIONS,
      { id: "file_regulatory_report", label: "Regulatory Filing", Icon: Scale, requires: "note" as const },
    ],
    keyFields: ["customer_name", "amount_involved", "transaction_type", "transaction_reference", "payment_handles_detected"],
    caseSort: (a, b) => {
      const nriFirst = ["INTERNATIONAL_USAGE", "ROAMING_ACCOUNT"];
      const aO = nriFirst.indexOf(a.classification); const bO = nriFirst.indexOf(b.classification);
      if (aO !== bO) return (aO === -1 ? 99 : aO) - (bO === -1 ? 99 : bO);
      return a.created_at < b.created_at ? -1 : 1;
    },
  },

  "equipment-provisioning-desk": {
    slug: "equipment-provisioning-desk",
    label: "Equipment & Provisioning Desk",
    shortLabel: "Equipment",
    Icon: CreditCard,
    colorClass: "desk-equipment",
    metricKeys: [
      { key: "total",    label: "Equipment Cases",      compute: q => q.counts.total ?? 0 },
      { key: "critical", label: "service kiosk/Urgent",       compute: q => q.counts.critical ?? 0 },
      { key: "resolved", label: "Resolved",         compute: q => q.counts.resolved ?? 0 },
      { key: "overdue",  label: "SLA Overdue",      compute: q => q.counts.overdue ?? 0 },
    ],
    actions: [
      ...STANDARD_ACTIONS,
      { id: "suspend_service", label: "Suspend Service", Icon: Ban, variant: "danger" as const, confirm: true },
      { id: "attach_evidence", label: "Attach Evidence", Icon: FileText, requires: "note" as const },
    ],
    keyFields: ["customer_name", "amount_involved", "transaction_reference", "connection_ids_detected", "transaction_type"],
    caseSort: (a, b) => {
      const cardFirst = ["INSTALLATION_DELAY", "EQUIPMENT_FAULT", "EQUIPMENT_RETURN"];
      const aO = cardFirst.indexOf(a.classification); const bO = cardFirst.indexOf(b.classification);
      if (aO !== bO) return (aO === -1 ? 99 : aO) - (bO === -1 ? 99 : bO);
      return (b.risk_score ?? 0) - (a.risk_score ?? 0);
    },
  },
};

// ─── Helpers ──────────────────────────────────────────────────────────────────
const ALL_DEPARTMENTS: Department[] = Object.values(DESKS).map(d => ({ department: d.label, slug: d.slug }));
const SAMPLE_EMAILS = [
  {
    label: "Fraud (Unauthorized Debit)",
    text: `From: customer@example.com\nSubject: Urgent unauthorized transaction from my salary account\n\nHello team,\n\nI noticed an unauthorized transaction of INR 145,000 from my salary account. I did not approve this and I cannot log in to self care portal. Please block any further transactions and investigate this immediately.\n\nRegards,\nRavi Sharma`,
  },
  {
    label: "NRI Remittance Issue",
    text: `From: priya.nri@example.com\nSubject: roaming account remittance not credited\n\nDear Docket,\n\nI am travelling and roaming in the UK. I transferred GBP 3,500 to my roaming account on 01/06/2026 via SWIFT. The amount has been taken from my card but the top up has not been applied to my Docket roaming account yet. UTR: GBXXXXXXX12345. Please investigate urgently.\n\nThanks,\nPriya Mehta`,
  },
  {
    label: "Welfare / Distress",
    text: `From: anonymous123@example.com\nSubject: I am in crisis\n\nI have lost my entire life savings of Rs 4,50,000 due to unauthorised use of my line. I am a senior citizen and I don't know what to do. I feel like I have no reason to continue. Please help me immediately.\n\nK. Raghavan`,
  },
  {
    label: "Contract instalment Dispute",
    text: `From: rahul.verma@example.com\nSubject: Wrong instalment deducted for home contract\n\nHello,\n\nMy home contract connection id is XXXXXXXXX4521. The instalment deducted this month was Rs 18,500 but as per my contract agreement, the instalment should be Rs 15,200. Please reverse the excess deduction of Rs 3,300 and clarify.\n\nRegards,\nRahul Verma`,
  },
];

function SampleEmailButton({ onInsert }: { onInsert: (text: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ position: "relative", display: "inline-block" }}>
      <button
        type="button"
        style={{ fontSize: 11 }}
        onClick={() => setOpen(o => !o)}
        title="Load a sample email for testing"
      >
        <BookOpen size={13} /> Sample
      </button>
      {open && (
        <div className="sample-dropdown" onMouseLeave={() => setOpen(false)}>
          {SAMPLE_EMAILS.map(s => (
            <button
              key={s.label}
              className="sample-dropdown-item"
              onClick={() => { onInsert(s.text); setOpen(false); }}
            >
              {s.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function fmt(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number") return value.toLocaleString("en-IN");
  return String(value);
}

function CopyButton({ text, title = "Copy" }: { text: string; title?: string }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    });
  }
  return (
    <button
      type="button"
      onClick={e => { e.stopPropagation(); copy(); }}
      title={title}
      style={{ padding: "1px 5px", fontSize: 10, border: "1px solid var(--line)", background: copied ? "var(--success-bg)" : "var(--surface-2)", color: copied ? "var(--success)" : "var(--muted)", borderRadius: 4 }}
    >
      {copied ? <CheckCheck size={10} /> : <FileText size={10} />}
    </button>
  );
}

function priorityClass(priority?: string) {
  return `priority ${String(priority || "low").toLowerCase()}`;
}

function stateClass(state?: string) {
  const s = (state || "").toLowerCase().replace(/_/g, "-");
  return `state-badge state-${s}`;
}

function fmtSlaDuration(minutes: number): string {
  if (minutes < 0) return `${Math.abs(Math.round(minutes))}m overdue`;
  if (minutes < 60) return `${Math.round(minutes)}m left`;
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return m > 0 ? `${h}h ${m}m left` : `${h}h left`;
}

function SlaChip({ alerts, slaMeta, state }: { alerts: any[]; slaMeta?: Record<string, any>; state?: string }) {
  const isSettled = state === "RESOLVED" || state === "CLOSED";
  if (isSettled && slaMeta?.resolved_at) {
    const met: boolean | undefined = slaMeta.resolution_sla_met;
    const mins: number | undefined = slaMeta.minutes_to_resolve;
    let timeLabel = "";
    if (mins !== undefined) {
      if (mins < 60) timeLabel = `${mins}m`;
      else { const h = Math.floor(mins / 60); const m = mins % 60; timeLabel = m > 0 ? `${h}h ${m}m` : `${h}h`; }
    }
    const metLabel = met === true ? "SLA met ✓" : met === false ? "SLA breached ✗" : "";
    const title = [timeLabel && `Resolved in ${timeLabel}`, metLabel].filter(Boolean).join(" · ");
    return (
      <span className={`sla-chip ${met === false ? "sla-chip-resolved-late" : "sla-chip-resolved"}`} title={title || "Case closed"}>
        <CheckCircle2 size={9} /> {timeLabel ? `${timeLabel}` : "Resolved"}{metLabel && <span className="sla-met-label"> {met ? "✓" : "✗"}</span>}
      </span>
    );
  }
  if (!alerts?.length) return null;
  const overdue = alerts.find(a => a.severity === "OVERDUE");
  const nearing = alerts.find(a => a.severity === "NEARING_BREACH");
  if (overdue) {
    return (
      <span className="sla-chip sla-chip-overdue" title={overdue.message}>
        <Clock size={9} /> {fmtSlaDuration(overdue.minutes_left ?? -1)}
      </span>
    );
  }
  if (nearing) {
    return (
      <span className="sla-chip sla-chip-nearing" title={nearing.message}>
        <Clock size={9} /> {fmtSlaDuration(nearing.minutes_left ?? 0)}
      </span>
    );
  }
  return null;
}

function UnverifiedBadge() {
  return (
    <span className="unverified-badge" title="Sender not found in Docket customer database">
      ? Unverified sender
    </span>
  );
}

const TONE_META: Record<string, { label: string; color: string }> = {
  WELFARE_RISK:  { label: "Welfare Risk",   color: "#f43f5e" },
  ANGRY:         { label: "Angry",          color: "#ef4444" },
  LEGAL_THREAT:  { label: "Legal Threat",   color: "#f97316" },
  DISTRESSED:    { label: "Distressed",     color: "#fb923c" },
  URGENT:        { label: "Urgent",         color: "#facc15" },
  FRUSTRATED:    { label: "Frustrated",     color: "#a78bfa" },
  NEUTRAL:       { label: "Neutral",        color: "#6b7280" },
  SATISFIED:     { label: "Satisfied",      color: "#22c55e" },
};

function SentimentDisplay({ sentiment, tone, distress, intensity }: {
  sentiment?: string; tone?: string; distress?: number; intensity?: string;
}) {
  const toneKey = tone ?? "NEUTRAL";
  const meta = TONE_META[toneKey] ?? TONE_META["NEUTRAL"];
  const dotColor = sentiment === "POSITIVE" ? "#22c55e" : sentiment === "NEGATIVE" ? "#ef4444" : "#6b7280";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, flexWrap: "wrap" }}>
      <span style={{ width: 7, height: 7, borderRadius: "50%", display: "inline-block", background: dotColor, flexShrink: 0 }} />
      <span>{sentiment ?? "NEUTRAL"}</span>
      {toneKey !== "NEUTRAL" && (
        <span style={{ fontSize: 9, fontWeight: 700, padding: "1px 5px", borderRadius: 6,
          background: meta.color + "28", color: meta.color, border: `1px solid ${meta.color}55` }}>
          {meta.label}
        </span>
      )}
      {typeof distress === "number" && distress > 0 && (
        <span style={{ fontSize: 9, color: "var(--muted)" }}>distress {distress}/10</span>
      )}
      {intensity && <span style={{ fontSize: 10, color: "var(--very-muted)" }}>({intensity})</span>}
    </span>
  );
}

function ExtractedEntitiesPanel({ entities }: { entities: Record<string, any> }) {
  const rows: { label: string; values: string[] }[] = [
    { label: "Persons",       values: entities.persons ?? [] },
    { label: "Organisations", values: entities.organizations ?? [] },
    { label: "Amounts ₹",     values: (entities.amount_values ?? []).map((v: number) => v.toLocaleString("en-IN")) },
    { label: "Accounts",      values: entities.connection_ids ?? [] },
    { label: "UPI IDs",       values: entities.payment_handles ?? [] },
    { label: "Phone",         values: entities.phone_numbers ?? [] },
    { label: "exchange code",          values: entities.exchange_code_codes ?? [] },
    { label: "Cards",         values: entities.device_serials ?? [] },
    { label: "Txn Refs",      values: entities.transaction_refs ?? [] },
    { label: "Dates",         values: entities.dates_mentioned ?? [] },
  ].filter(r => r.values.length > 0);

  if (!rows.length) return null;
  return (
    <div className="entities-panel">
      <div className="entities-panel-title">Extracted from email</div>
      <div className="entities-grid">
        {rows.map(r => (
          <div key={r.label} className="entity-row">
            <span className="entity-label">{r.label}</span>
            <span className="entity-values">{r.values.join(" · ")}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function BankLogo({ compact = false, onClick }: { compact?: boolean; onClick?: () => void }) {
  const inner = compact
    ? <img src="/logo.png?v=2" alt="Docket" className="brand-logo2-img" />
    : <img src="/logo.png?v=2" alt="Docket" className="brand-logo-img" />;
  if (onClick) {
    return (
      <button
        className="brand-logo brand-logo-btn"
        aria-label="Docket — return to main menu"
        title="Main menu"
        onClick={onClick}
      >
        {inner}
      </button>
    );
  }
  return <div className="brand-logo" aria-label="Docket">{inner}</div>;
}

function SystemPills({ readiness, socketOnline, status, tone = "neutral" }: {
  readiness: SystemReadiness | null; socketOnline: boolean; status?: string; tone?: StatusTone;
}) {
  const apiState = readiness ? (readiness.ok ? "Online" : "Degraded") : "Checking";
  return (
    <div className="system-state">
      <span className={`state-dot ${readiness?.ok ? "online" : readiness ? "offline" : "checking"}`} />
      <span className={`health-pill ${readiness?.ok ? "health-ready" : readiness ? "health-error" : ""}`}>API: {apiState}</span>
      <span className={`health-pill health-${readiness?.bert.status ?? "checking"}`}>BERT: {readiness?.bert.status ?? "checking"}</span>
      <span className={`health-pill health-${readiness?.ollama.status ?? "checking"}`}>Ollama: {readiness?.ollama.status ?? "checking"}</span>
      <span className={`health-pill ${socketOnline ? "health-ready" : "health-warning"}`}>Live: {socketOnline ? "on" : "off"}</span>
    </div>
  );
}

// ─── Unified App Header ────────────────────────────────────────────────────────
function AppHeader({
  eyebrow, title, readiness, socketOnline, status, tone, navRight,
  onLogoClick, canUndo, canRedo, onUndo, onRedo,
}: {
  eyebrow: string; title: string;
  readiness: SystemReadiness | null; socketOnline: boolean;
  status?: string; tone?: StatusTone;
  navRight?: ReactNode;
  onLogoClick?: () => void;
  canUndo?: boolean; canRedo?: boolean;
  onUndo?: () => void; onRedo?: () => void;
}) {
  return (
    <header className="topbar topbar-unified">
      <div className="brand-line">
        <BankLogo onClick={onLogoClick} />
        <div>
          <div className="eyebrow">{eyebrow}</div>
          <h1>{title}</h1>
        </div>
        {(onUndo || onRedo) && (
          <div className="undo-redo-btns">
            <button
              className="undo-redo-btn"
              title="Undo (go back)"
              disabled={!canUndo}
              onClick={onUndo}
            >
              <Undo2 size={15} />
            </button>
            <button
              className="undo-redo-btn"
              title="Redo (go forward)"
              disabled={!canRedo}
              onClick={onRedo}
            >
              <Redo2 size={15} />
            </button>
          </div>
        )}
      </div>
      <div className="topbar-controls">
        <SystemPills readiness={readiness} socketOnline={socketOnline} status={status} tone={tone} />
        {navRight}
      </div>
    </header>
  );
}

// ─── URL helpers ──────────────────────────────────────────────────────────────
function portalFromPath(): Portal {
  const p = window.location.pathname.replace(/\/+$/, "");
  if (p === "/centre" || p === "/center") return "central";
  if (p === "/dept")    return "desks";
  if (p === "/request") return "request";
  return null;
}

function pathForPortal(portal: Portal): string {
  if (portal === "central") return "/centre";
  if (portal === "desks")   return "/dept";
  if (portal === "request") return "/request";
  return "/";
}

// ─── Root App ─────────────────────────────────────────────────────────────────
export default function App() {
  const [portal, setPortalState] = useState<Portal>(portalFromPath);
  const [view, setView] = useState<View>("central");
  // null = desk selector screen; set to a DeskView once operator picks their desk
  const [selectedDesk, setSelectedDesk] = useState<DeskView | null>(null);

  // ── URL-synced portal navigation ──────────────────────────────────────────
  function setPortal(next: Portal) {
    const path = pathForPortal(next);
    window.history.pushState({ portal: next, view: "central", idx: 0 }, "", path);
    setPortalState(next);
    setView("central");
    // reset view history counters
    histIdxRef.current = 0;
    maxIdxRef.current  = 0;
    setCanUndo(false);
    setCanRedo(false);
  }

  // ── Browser history-backed undo/redo ──────────────────────────────────────
  const histIdxRef = useRef(0);
  const maxIdxRef  = useRef(0);
  const [canUndo, setCanUndo] = useState(false);
  const [canRedo, setCanRedo] = useState(false);

  useEffect(() => {
    // Seed so the current URL is in the history stack
    window.history.replaceState(
      { portal, view: "central", idx: 0 },
      "",
      pathForPortal(portal),
    );

    function onPop(e: PopStateEvent) {
      const state = e.state as { portal: Portal; view: View; idx: number } | null;
      if (!state) return;
      // Portal change
      if (state.portal !== undefined) {
        setPortalState(state.portal);
        setView(state.view ?? "central");
      } else {
        setView(state.view);
      }
      histIdxRef.current = state.idx ?? 0;
      setCanUndo(state.idx > 0);
      setCanRedo(state.idx < maxIdxRef.current);
    }

    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  function navigateTo(next: View) {
    const newIdx = histIdxRef.current + 1;
    histIdxRef.current = newIdx;
    maxIdxRef.current  = newIdx;
    window.history.pushState(
      { portal, view: next, idx: newIdx },
      "",
      pathForPortal(portal),
    );
    setView(next);
    setCanUndo(true);
    setCanRedo(false);
  }

  function undoView()  { window.history.back(); }
  function redoView()  { window.history.forward(); }
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [departments, setDepartments] = useState<Department[]>(ALL_DEPARTMENTS);
  const [deptQueues, setDeptQueues] = useState<Record<string, DepartmentQueue>>({});
  const [selectedCase, setSelectedCase] = useState<CaseDetail | null>(null);
  const [emailText, setEmailText] = useState("");
  const [status, setStatus] = useState("Ready");
  const [statusTone, setStatusTone] = useState<StatusTone>("neutral");
  const [busy, setBusy] = useState(false);
  const [verifyingModels, setVerifyingModels] = useState(false);
  const [readiness, setReadiness] = useState<SystemReadiness | null>(null);
  const [socketOnline, setSocketOnline] = useState(false);
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyResults, setHistoryResults] = useState<any[]>([]);
  const [auditRows, setAuditRows] = useState<any[]>([]);
  const [incidents, setIncidents] = useState<any[]>([]);
  const [slaAlerts, setSlaAlerts] = useState<any[]>([]);
  const [triageQueue, setTriageQueue] = useState<any[]>([]);
  const [errorQueue, setErrorQueue] = useState<any[]>([]);
  const [progress, setProgress] = useState<{ pct: number; stage: string } | null>(null);

  // Progress bar helper — drives the staged pipeline animation
  function runProgress(onDone: () => void): () => void {
    // Stages: parse → BERT → Gemma4 → save
    // Gemma4 is the slow step; we estimate ~90s total but complete instantly on API response
    const stages: { label: string; targetPct: number; durationMs: number }[] = [
      { label: "Parsing email…",             targetPct: 8,  durationMs: 400  },
      { label: "Running BERT analysis…",     targetPct: 22, durationMs: 2500 },
      { label: "Gemma4 classifying email…",  targetPct: 85, durationMs: 90000 },
      { label: "Saving case to database…",   targetPct: 97, durationMs: 600  },
    ];
    let cancelled = false;
    let current = 0;

    function tick() {
      if (cancelled) return;
      const stage = stages[current];
      if (!stage) return;

      const start = performance.now();
      const startPct = current === 0 ? 0 : stages[current - 1].targetPct;

      function animate() {
        if (cancelled) return;
        const elapsed = performance.now() - start;
        const t = Math.min(elapsed / stage.durationMs, 1);
        // Ease-out: fast at start, slow at end
        const eased = 1 - Math.pow(1 - t, 2);
        const pct = startPct + (stage.targetPct - startPct) * eased;
        setProgress({ pct: Math.round(pct), stage: stage.label });
        if (t < 1) {
          requestAnimationFrame(animate);
        } else {
          current++;
          if (current < stages.length) tick();
        }
      }
      requestAnimationFrame(animate);
    }

    tick();
    return () => { cancelled = true; };
  }

  function notice(message: string, tone: StatusTone = "neutral") {
    setStatus(message);
    setStatusTone(tone);
  }

  async function refreshReadiness() {
    try {
      const next = await api.readiness();
      setReadiness(next);
      return next;
    } catch (error: any) {
      setReadiness(null);
      notice(error.message || "API health check failed", "error");
      return null;
    }
  }

  async function refreshAll() {
    const [dash, deps, sla, incs, tq, eq] = await Promise.all([
      api.dashboard(),
      api.departments().catch(() => ALL_DEPARTMENTS),
      api.slaAlerts().catch(() => [] as any[]),
      api.incidents().catch(() => [] as any[]),
      api.triageQueue().catch(() => [] as any[]),
      api.errorQueue().catch(() => [] as any[]),
    ]);
    setDashboard(dash);
    setDepartments(deps.length ? deps : ALL_DEPARTMENTS);
    setSlaAlerts(sla);
    setIncidents(incs);
    setTriageQueue(tq);
    setErrorQueue(eq);
  }

  async function refreshDashboard() {
    const dash = await api.dashboard();
    setDashboard(dash);
  }

  async function refreshDept(slug: string) {
    const q = await api.departmentQueue(slug);
    setDeptQueues(prev => ({ ...prev, [slug]: q }));
  }

  async function refreshAllDepts() {
    await Promise.all(Object.keys(DESKS).map(s => refreshDept(s)));
  }

  async function loadCase(caseId: string, auditEvent = true) {
    const detail = await api.caseDetail(caseId, auditEvent);
    setSelectedCase(detail);
  }

  useEffect(() => {
    refreshReadiness();
    refreshAll().catch(e => notice(e.message, "error"));
    refreshAllDepts().catch(() => undefined);
  }, []);

  // 60s fallback refresh so SLA ordering stays current even when WebSocket drops
  useEffect(() => {
    const id = setInterval(() => {
      refreshAll().catch(() => undefined);
      refreshAllDepts().catch(() => undefined);
    }, 60_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    let disposed = false;
    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    function connect() {
      if (disposed) return;
      ws = new WebSocket(`${API_BASE.replace("http", "ws")}/ws/operations`);

      ws.onopen = () => {
        if (disposed) return;
        setSocketOnline(true);
        attempt = 0;
      };
      ws.onmessage = (msg) => {
        if (disposed) return;
        let event = "";
        let payload: any = {};
        try {
          const data = JSON.parse(msg.data);
          event = data.event ?? "";
          payload = data.payload ?? {};
        } catch { /* ignore malformed */ }

        if (event === "case_created") {
          // New case: refresh everything — counts, queues, SLA, triage
          refreshAll().catch(() => undefined);
          refreshAllDepts().catch(() => undefined);
        } else if (event === "case_updated") {
          // Case changed: refresh counts + every dept queue (state/priority may shift queues)
          refreshAll().catch(() => undefined);
          refreshAllDepts().catch(() => undefined);
          // Also refresh the open case detail if it's the one that changed
          setSelectedCase(prev => {
            if (!prev) return prev;
            const isDirectMatch = prev.case_id === payload.case_id;
            const isParent      = prev.case_id === payload.parent_case_id;
            const isChild       = prev.parent_case_id === payload.case_id ||
                                  (prev.parent_case_id && prev.parent_case_id === payload.parent_case_id);
            if (isDirectMatch || isParent || isChild) {
              api.caseDetail(prev.case_id).then(d => {
                if (!disposed) setSelectedCase(d);
              }).catch(() => undefined);
            }
            return prev;
          });
        } else if (event === "queue_reranked") {
          // SLA rerank: only queues need refreshing, not full dashboard
          refreshAll().catch(() => undefined);
          refreshAllDepts().catch(() => undefined);
        } else if (event === "error_dismissed") {
          // Error queue changed: refresh dashboard counts + error queue
          refreshAll().catch(() => undefined);
        }
      };
      ws.onerror = () => {
        // error always precedes close; let onclose handle reconnect
        if (!disposed) setSocketOnline(false);
      };
      ws.onclose = () => {
        if (disposed) return;
        setSocketOnline(false);
        // Exponential backoff: 1 s, 2 s, 4 s … capped at 30 s
        const delay = Math.min(1000 * Math.pow(2, attempt), 30000);
        attempt += 1;
        retryTimer = setTimeout(connect, delay);
      };
    }

    connect();
    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
      ws?.close();
    };
  }, []);

  async function verifyModelStack() {
    setVerifyingModels(true);
    notice("Running BERT and Ollama diagnostics. First verification can take a moment.");
    try {
      const result = await api.verifyModels();
      setReadiness(result);
      if (result.status === "ready") {
        notice("BERT and Ollama diagnostics passed.", "success");
      } else {
        notice("One or more model checks failed. Review readiness details.", "error");
      }
    } catch (error: any) {
      notice(error.message || "Model diagnostics failed.", "error");
    } finally {
      setVerifyingModels(false);
    }
  }

  async function processText() {
    if (!emailText.trim()) { notice("Paste an email first.", "warning"); return; }
    setBusy(true);
    const cancel = runProgress(() => {});
    try {
      const created = await api.ingestText(emailText);
      cancel();
      setProgress({ pct: 100, stage: "Done!" });
      setTimeout(() => setProgress(null), 1200);
      setSelectedCase(created);
      notice(`${created.case_id} → ${created.primary_department}`, "success");
      await refreshAll(); await refreshAllDepts();
    } catch (e: any) {
      cancel(); setProgress(null);
      notice(e.message, "error");
    }
    finally { setBusy(false); }
  }

  async function processFile(file: File | null) {
    if (!file) return;
    setBusy(true);
    const cancel = runProgress(() => {});
    try {
      const created = await api.ingestFile(file);
      cancel();
      setProgress({ pct: 100, stage: "Done!" });
      setTimeout(() => setProgress(null), 1200);
      setSelectedCase(created);
      notice(`${created.case_id} → ${created.primary_department}`, "success");
      await refreshAll(); await refreshAllDepts();
    } catch (e: any) {
      cancel(); setProgress(null);
      notice(e.message, "error");
    }
    finally { setBusy(false); }
  }

  async function deskAction(action: string, deskLabel: string, payload: Record<string, any> = {}) {
    if (!selectedCase) { notice("Select a case from the queue before taking action.", "warning"); return false; }
    setBusy(true);
    try {
      const updated = await api.workflow(selectedCase.case_id, {
        action,
        role: "department_operator",
        actor: "department_operator",
        department: deskLabel,
        ...payload,
      });
      const settled = updated.workflow_state === "RESOLVED" || updated.workflow_state === "CLOSED";
      setSelectedCase(settled ? null : updated);
      await Promise.all([refreshAllDepts(), refreshAll()]);
      return action; // return action name so desk console can show a toast
    } catch (e: any) { notice(e.message, "error"); return false; }
    finally { setBusy(false); }
  }

  async function deptQueueReorder(deptSlug: string, caseId: string, newIndex: number) {
    // Optimistic: reinsert locally first
    setDeptQueues(prev => {
      const q = prev[deptSlug];
      if (!q) return prev;
      const items = [...q.items];
      const fromIdx = items.findIndex(c => c.case_id === caseId);
      if (fromIdx === -1) return prev;
      const [item] = items.splice(fromIdx, 1);
      items.splice(newIndex, 0, item);
      const resequenced = items.map((it, i) => ({ ...it, queue_position: i + 1 }));
      return { ...prev, [deptSlug]: { ...q, items: resequenced } };
    });
    try {
      await api.deptQueueReorder(deptSlug, caseId, newIndex);
      await refreshDept(deptSlug);
    } catch (e: any) {
      notice(e.message, "error");
      await refreshDept(deptSlug).catch(() => undefined);
    }
  }

  async function deptQueueMove(deptSlug: string, caseId: string, direction: "up" | "down") {
    // Optimistic update: reorder locally before the server confirms so the UI
    // responds instantly. The server response replaces the optimistic state.
    setDeptQueues(prev => {
      const q = prev[deptSlug];
      if (!q) return prev;
      const items = q.items.map((item, index) => ({
        ...item,
        queue_position: item.queue_position ?? index + 1,
      }));
      const idx = items.findIndex(c => c.case_id === caseId);
      const neighbourIdx = direction === "up" ? idx - 1 : idx + 1;
      if (idx === -1 || neighbourIdx < 0 || neighbourIdx >= items.length) return prev;
      [items[idx], items[neighbourIdx]] = [items[neighbourIdx], items[idx]];
      const resequenced = items.map((item, index) => ({ ...item, queue_position: index + 1 }));
      return { ...prev, [deptSlug]: { ...q, items: resequenced } };
    });
    try {
      await api.deptQueueMove(deptSlug, caseId, direction);
      await refreshDept(deptSlug);
    } catch (e: any) {
      notice(e.message, "error");
      // Rollback optimistic update on failure
      await refreshDept(deptSlug).catch(() => undefined);
    }
  }

  async function centralQueueMove(caseId: string, direction: "up" | "down") {
    setBusy(true);
    try {
      await api.centralQueueMove(caseId, direction);
      await refreshDashboard();
      notice(`Case moved ${direction} in central queue.`, "success");
    } catch (e: any) { notice(e.message, "error"); }
    finally { setBusy(false); }
  }

  async function unpinCase(deptSlug: string, caseId: string) {
    try {
      await api.unpinCase(caseId);
      await refreshDept(deptSlug);
      notice("Case unpinned — will auto-rank by SLA and priority.", "success");
    } catch (e: any) { notice(e.message, "error"); }
  }

  async function openAudit() {
    navigateTo("audit");
    try {
      const rows = await api.audit();
      setAuditRows(rows);
    } catch (error: any) {
      notice(error.message, "error");
    }
  }

  const queue = dashboard?.incoming_queue ?? [];

  const deptNavItems: [View, string, DeskConfig][] = [
    ["dept_fraud",      "trust-safety-desk",            DESKS["trust-safety-desk"]],
    ["dept_customer",   "customer-resolution-desk",      DESKS["customer-resolution-desk"]],
    ["dept_compliance", "compliance-desk",            DESKS["compliance-desk"]],
    ["dept_technical",  "network-support-desk",DESKS["network-support-desk"]],
    ["dept_central",    "central-operations-desk",    DESKS["central-operations-desk"]],
    ["dept_contracts",      "billing-contracts-desk",                   DESKS["billing-contracts-desk"]],
    ["dept_nri",        "roaming-international-desk",                      DESKS["roaming-international-desk"]],
    ["dept_cards",      "equipment-provisioning-desk",                 DESKS["equipment-provisioning-desk"]],
  ];

  // ── Landing portal ──────────────────────────────────────────────────────────
  if (portal === null) {
    return (
      <div className="app-shell">
        <AppHeader
          eyebrow="Docket Internal Service Operations"
          title="AI/ML Email Operations Platform"
          readiness={readiness} socketOnline={socketOnline}
        />
        <div className="portal-landing">
          <p className="portal-subtitle">Select your workspace to continue</p>
          <div className="portal-cards">
            <button
              className="portal-card portal-card-central"
              onClick={() => { setPortal("central"); setView("central"); }}
            >
              <Inbox size={40} />
              <div className="portal-card-title">Central Operations Console</div>
              <div className="portal-card-desc">
                Email intake · AI/ML triage · Case dispatch · SLA monitoring · Incidents · Human triage queue · Error queue · Audit trail · Customer lookup · Analytics
              </div>
              <div className="portal-card-note">Intake operators &amp; supervisors</div>
            </button>

            <button
              className="portal-card portal-card-dept"
              onClick={() => { setPortal("desks"); setSelectedDesk(null); }}
            >
              <Building2 size={40} />
              <div className="portal-card-title">Department Desk Portal</div>
              <div className="portal-card-desc">
                Eight desks · Trust &amp; Safety · Resolution · Compliance · Network · Billing &amp; Contracts · Roaming · Equipment · Central Ops
              </div>
              <div className="portal-card-note">Department operators &amp; investigators</div>
            </button>

            <button
              className="portal-card portal-card-request"
              onClick={() => setPortal("request")}
            >
              <MessageSquare size={40} />
              <div className="portal-card-title">Assisted Service Portal</div>
              <div className="portal-card-desc">
                Submit service requests · Security escalations · Account access issues · Compliance grievances · Track request status
              </div>
              <div className="portal-card-note">Service area staff &amp; relationship managers</div>
            </button>

          </div>
        </div>
      </div>
    );
  }

  // ── Assisted service request portal ────────────────────────────────────────
  if (portal === "request") {
    return (
      <div className="app-shell">
        <AppHeader
          eyebrow="Docket Assisted Service Operations"
          title="Service Request Portal"
          readiness={readiness} socketOnline={socketOnline}
          onLogoClick={() => setPortal(null)}
          navRight={
            <button className="portal-exit-btn" onClick={() => setPortal(null)}>
              <ArrowLeft size={14} /> Portal
            </button>
          }
        />
        <main className="workspace workspace-full">
          <RequestPortalView onBack={() => setPortal(null)} />
        </main>
      </div>
    );
  }

  // ── Central portal ───────────────────────────────────────────────────────────
  if (portal === "central") {
    return (
      <div className="app-shell">
        <AppHeader
          eyebrow="Docket Internal Service Operations"
          title="Central Operations Console"
          readiness={readiness} socketOnline={socketOnline}
          status={status} tone={statusTone}
          onLogoClick={() => setPortal(null)}
          canUndo={canUndo} canRedo={canRedo}
          onUndo={undoView} onRedo={redoView}
          navRight={
            <button className="portal-exit-btn" onClick={() => setPortal(null)}>
              <ArrowLeft size={14} /> Portal
            </button>
          }
        />

        <div className="layout">
          <aside className="side-nav">
            <div className="nav-section-label">Operations</div>
            <NavBtn id="central" current={view} Icon={Inbox} label="Central" onClick={() => navigateTo("central")} />
            <NavBtn id="triage_queue" current={view} Icon={BrainCircuit} label="Triage" onClick={() => navigateTo("triage_queue")} badge={triageQueue.length || undefined} />
            <NavBtn id="error_queue" current={view} Icon={AlertOctagon} label="Errors" onClick={() => navigateTo("error_queue")} badge={errorQueue.length || undefined} />

            <div className="nav-section-label">Tools</div>
            <NavBtn id="sla" current={view} Icon={Clock} label="SLA" onClick={() => navigateTo("sla")} badge={slaAlerts.length || undefined} />
            <NavBtn id="incidents" current={view} Icon={ShieldAlert} label="Incidents" onClick={() => navigateTo("incidents")} />
            <NavBtn id="history" current={view} Icon={Search} label="History" onClick={() => navigateTo("history")} />
            <NavBtn id="audit" current={view} Icon={Archive} label="Audit" onClick={openAudit} />
            <NavBtn id="analytics" current={view} Icon={TrendingUp} label="Analytics" onClick={() => navigateTo("analytics")} />
            <NavBtn id="customers" current={view} Icon={Users} label="Customers" onClick={() => navigateTo("customers")} />
            <NavBtn id="assistant" current={view} Icon={Bot} label="Assistant" onClick={() => navigateTo("assistant")} />
          </aside>

          <main className="workspace">
            {!import.meta.env.VITE_SMTP_ENABLED && (
              <div className="smtp-warning-banner">
                <AlertTriangle size={12} />
                SMTP not configured — Send Communication stores messages in the timeline but does not deliver emails.
              </div>
            )}
            {view === "central" && (
              <CentralView
                dashboard={dashboard}
                queue={queue}
                selectedCase={selectedCase}
                emailText={emailText}
                setEmailText={setEmailText}
                processText={processText}
                processFile={processFile}
                loadCase={loadCase}
                busy={busy}
                progress={progress}
                readiness={readiness}
                verifyingModels={verifyingModels}
                verifyModels={verifyModelStack}
                refreshAll={() => refreshAll().catch(error => notice(error.message, "error"))}
              />
            )}
            {view === "triage_queue" && (
              <TriageQueueView
                cases={triageQueue}
                loadCase={loadCase}
                selectedCase={selectedCase}
                onRelease={async (caseId, depts, cls, note) => {
                  setBusy(true);
                  try {
                    const [primary, ...secondary] = depts;
                    await api.releaseTriage(caseId, { department: primary, departments: secondary, classification: cls, note, actor: "triage_operator" });
                    notice(`Case ${caseId} released to ${depts.join(", ")}.`, "success");
                    await refreshAll(); await refreshAllDepts();
                    setSelectedCase(null);
                  } catch (e: any) { notice(e.message, "error"); }
                  finally { setBusy(false); }
                }}
                busy={busy}
                refresh={() => api.triageQueue().then(setTriageQueue).catch(() => undefined)}
              />
            )}
            {view === "error_queue" && (
              <ErrorQueueView
                errors={errorQueue}
                onDismiss={async (errorId) => {
                  try {
                    await api.dismissError(errorId);
                    notice("Error record dismissed.", "success");
                    await refreshAll();
                  } catch (e: any) { notice(e.message, "error"); }
                }}
                refresh={() => api.errorQueue().then(setErrorQueue).catch(() => undefined)}
              />
            )}
            {view === "sla" && <SlaView alerts={slaAlerts} workloads={dashboard?.department_workloads ?? []} />}
            {view === "incidents" && <IncidentView incidents={incidents} />}
            {view === "history" && (
              <HistoryView
                query={historyQuery}
                setQuery={setHistoryQuery}
                results={historyResults}
                search={async (mode: "keyword" | "semantic") => {
                  if (!historyQuery.trim()) return;
                  try {
                    if (mode === "keyword") {
                      const rows = await api.caseSearch(historyQuery);
                      setHistoryResults(rows);
                      notice(`Found ${rows.length} case${rows.length !== 1 ? "s" : ""}.`, "success");
                    } else {
                      const r = await api.historicalSearch(historyQuery);
                      setHistoryResults(r.matches ?? []);
                      notice(`Found ${(r.matches ?? []).length} semantic matches.`, "success");
                    }
                  } catch (error: any) {
                    notice(error.message, "error");
                  }
                }}
                loadCase={loadCase}
                selectedCase={selectedCase}
                onContinuation={async (caseId: string, note: string) => {
                  try {
                    const newCase = await api.createContinuation(caseId, note);
                    notice(`Reopened as ${newCase.case_id} — switching to Central view`, "success");
                    await refreshAll(); await refreshAllDepts();
                    setSelectedCase(newCase);
                    navigateTo("central");
                  } catch (e: any) { notice(e.message, "error"); }
                }}
              />
            )}
            {view === "audit" && <AuditView rows={auditRows} />}
            {view === "analytics" && <AnalyticsView />}
            {view === "customers" && <CustomersView />}
            {view === "assistant" && (
              <div style={{ height: "100%", maxWidth: 720, margin: "0 auto", padding: "0 0" }}>
                <AdminAssistant />
              </div>
            )}
          </main>
        </div>
      </div>
    );
  }

  // ── Desks portal — Step 1: desk selector ─────────────────────────────────────
  if (portal === "desks" && selectedDesk === null) {
    const deskDescriptions: Record<string, string> = {
      "dept_fraud":      "Investigate security alerts, bar lines, request forensics, flag transactions, escalate high-risk cases",
      "dept_customer":   "Handle customer complaints, offer resolutions, send communications, manage refund and service requests",
      "dept_compliance": "Review regulatory obligations, file the regulator reports, manage identity verification and legal-notice cases, grant compliance clearances",
      "dept_technical":  "Diagnose self care failures, create IT tickets, attach system logs, resolve transaction and access issues",
      "dept_central":    "Manage escalations, override priorities, transfer cases between departments, co-ordinate multi-department reviews",
      "dept_contracts":      "Manage home, auto and personal contract queries, instalment bounce cases, foreclosure requests, disbursement delays and NOC issuance",
      "dept_nri":        "Handle roaming/roaming/roaming account issues, international remittances, FEMA compliance, and repatriation requests",
      "dept_cards":      "Resolve card disputes, chargeback requests, service kiosk cash failures, card activation, PIN resets and reward point queries",
    };
    return (
      <div className="app-shell">
        <AppHeader
          eyebrow="Docket Internal Service Operations"
          title="Department Desk Portal"
          readiness={readiness} socketOnline={socketOnline}
          onLogoClick={() => setPortal(null)}
          navRight={
            <button className="portal-exit-btn" onClick={() => setPortal(null)}>
              <ArrowLeft size={14} /> Portal
            </button>
          }
        />
        <div className="portal-landing">
          <p className="portal-subtitle">Select your assigned desk to sign in</p>
          <div className="portal-cards desk-selector-cards">
            {deptNavItems.map(([vid, slug, cfg]) => {
              const escalated = deptQueues[slug]?.counts.escalated ?? 0;
              const pendingTriage = deptQueues[slug]?.counts.pending_triage ?? 0;
              const total = deptQueues[slug]?.counts.total ?? deptQueues[slug]?.items?.length ?? 0;
              return (
                <button
                  key={vid}
                  className={`portal-card portal-card-desk ${cfg.colorClass}`}
                  onClick={() => { setSelectedDesk(vid as DeskView); refreshDept(slug); setSelectedCase(null); }}
                  title={deskDescriptions[vid]}
                >
                  <cfg.Icon size={22} />
                  <div className="portal-card-title">{cfg.label}</div>
                  <div className="portal-card-desc">{deskDescriptions[vid]}</div>
                  <div className="desk-card-stats">
                    <span>{total} cases</span>
                    {pendingTriage > 0 && (
                      <span className="desk-card-triage">
                        <BrainCircuit size={12} /> {pendingTriage} awaiting triage
                      </span>
                    )}
                    {escalated > 0 && (
                      <span className="desk-card-escalated">
                        <AlertTriangle size={12} /> {escalated} escalated
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  // ── Desks portal — Step 2: single desk console ────────────────────────────────
  if (portal === "desks" && selectedDesk !== null) {
    const deskEntry = deptNavItems.find(([vid]) => vid === selectedDesk)!;
    const [, deskSlug, deskCfg] = deskEntry;
    return (
      <div className="app-shell">
        <AppHeader
          eyebrow={`Department Desk · ${deskCfg.shortLabel}`}
          title={deskCfg.label}
          readiness={readiness} socketOnline={socketOnline}
          status={status} tone={statusTone}
          onLogoClick={() => { setPortal(null); setSelectedDesk(null); setSelectedCase(null); }}
          navRight={
            <>
              <button className="portal-exit-btn" onClick={() => { setSelectedDesk(null); setSelectedCase(null); }}>
                <ArrowLeft size={14} /> Desks
              </button>
              <button className="portal-exit-btn" onClick={() => { setPortal(null); setSelectedDesk(null); setSelectedCase(null); }}>
                <ArrowLeft size={14} /> Portal
              </button>
            </>
          }
        />
        <main className="workspace workspace-full">
          <DeskConsoleView
            key={deskSlug}
            config={deskCfg}
            queue={deptQueues[deskSlug] ?? null}
            selectedCase={selectedCase}
            loadCase={loadCase}
            action={(action, payload) => deskAction(action, deskCfg.label, payload)}
            busy={busy}
            refreshQueue={() => refreshDept(deskSlug)}
            moveInQueue={(caseId, direction) => deptQueueMove(deskSlug, caseId, direction)}
            reorderQueue={(caseId, newIndex) => deptQueueReorder(deskSlug, caseId, newIndex)}
            onUnpin={(caseId) => unpinCase(deskSlug, caseId)}
          />
        </main>
      </div>
    );
  }

  return null;
}

const REQUEST_TYPE_OPTIONS: { value: PortalRequestType; label: string; description: string }[] = [
  { value: "trust", label: "Unauthorised Use", description: "Suspicious debit, phishing, card or account misuse" },
  { value: "technical", label: "Digital Service Issue", description: "the mobile app, UPI, self care portal, app or system issue" },
  { value: "account_access", label: "Account Access", description: "Login, locked access, password or credential problem" },
  { value: "compliance", label: "Compliance / Regulatory", description: "identity verification, notice, statutory or regulatory matter" },
  { value: "grievance", label: "Service Complaint", description: "Service area, service, refund, delay or unresolved complaint" },
  { value: "escalation", label: "Escalation", description: "Existing matter requiring Central Ops attention" },
  { value: "general", label: "General Enquiry", description: "Non-urgent service information request" },
];

const URGENCY_OPTIONS: { value: PortalUrgency; label: string }[] = [
  { value: "critical", label: "Critical" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
];

const EMPTY_PORTAL_FORM: PortalSubmitPayload = {
  requester_name: "",
  requester_email: "",
  request_type: "grievance",
  description: "",
  reference_number: "",
  urgency: "medium",
  connection_id: "",
};

function RequestPortalView({ onBack }: { onBack: () => void }) {
  const [form, setForm] = useState<PortalSubmitPayload>(EMPTY_PORTAL_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<PortalSubmitResult | null>(null);

  function update<K extends keyof PortalSubmitPayload>(key: K, value: PortalSubmitPayload[K]) {
    setForm(prev => ({ ...prev, [key]: value }));
  }

  function validate(): string | null {
    if (form.requester_name.trim().length < 2) return "Enter the requester name.";
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.requester_email.trim())) return "Enter a valid requester email.";
    if (form.description.trim().length < 20) return "Describe the request in at least 20 characters.";
    if (form.connection_id && !/^[0-9]{4,20}$/.test(form.connection_id.trim())) {
      return "Account number should contain 4 to 20 digits only.";
    }
    return null;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const validationError = validate();
    if (validationError) {
      setError(validationError);
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      const payload: PortalSubmitPayload = {
        requester_name: form.requester_name.trim(),
        requester_email: form.requester_email.trim(),
        request_type: form.request_type,
        description: form.description.trim(),
        urgency: form.urgency,
        reference_number: form.reference_number?.trim() || undefined,
        connection_id: form.connection_id?.trim() || undefined,
      };
      setResult(await api.portalSubmit(payload));
    } catch (err: any) {
      setError(err.message || "Unable to submit the service request.");
    } finally {
      setSubmitting(false);
    }
  }

  if (result) {
    return (
      <section className="portal-request-success">
        <CheckCircle2 size={42} className="success-icon" />
        <h2>Request Registered</h2>
        <div className="success-case-id">{result.case_id}</div>
        <div className="success-meta">
          <span className={priorityClass(result.priority)}>{result.priority}</span>
          <span className="success-dept">{result.suggested_department}</span>
        </div>
        <p className="success-note">{result.message}</p>
        <div className="success-actions">
          <button
            type="button"
            onClick={() => {
              setForm(EMPTY_PORTAL_FORM);
              setResult(null);
              setError("");
            }}
          >
            New Request
          </button>
          <button type="button" className="primary" onClick={onBack}>
            <ArrowLeft size={14} /> Portal
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="portal-request-shell">
      <form className="portal-request-form" onSubmit={submit}>
        <div className="portal-request-header">
          <FileText size={24} />
          <div>
            <h2>Register Assisted Service Request</h2>
            <p>Submissions are assigned to Central Operations for triage and dispatch.</p>
          </div>
        </div>

        {error && <div className="portal-request-error">{error}</div>}

        <fieldset className="portal-fieldset">
          <legend>Requester</legend>
          <div className="portal-field-row">
            <label className="portal-field">
              <span>Name</span>
              <input value={form.requester_name} onChange={e => update("requester_name", e.target.value)} maxLength={120} />
            </label>
            <label className="portal-field">
              <span>Email</span>
              <input value={form.requester_email} onChange={e => update("requester_email", e.target.value)} maxLength={200} inputMode="email" />
            </label>
          </div>
          <div className="portal-field-row">
            <label className="portal-field">
              <span>Reference <span className="optional">optional</span></span>
              <input value={form.reference_number ?? ""} onChange={e => update("reference_number", e.target.value)} maxLength={80} />
            </label>
            <label className="portal-field">
              <span>Connection ID <span className="optional">optional</span></span>
              <input value={form.connection_id ?? ""} onChange={e => update("connection_id", e.target.value)} maxLength={20} inputMode="numeric" />
            </label>
          </div>
        </fieldset>

        <fieldset className="portal-fieldset">
          <legend>Request Type</legend>
          <div className="portal-type-grid">
            {REQUEST_TYPE_OPTIONS.map(option => (
              <label key={option.value} className={`portal-type-card ${form.request_type === option.value ? "selected" : ""}`}>
                <input
                  type="radio"
                  name="request_type"
                  checked={form.request_type === option.value}
                  onChange={() => update("request_type", option.value)}
                />
                <strong>{option.label}</strong>
                <small>{option.description}</small>
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset className="portal-fieldset">
          <legend>Priority</legend>
          <div className="portal-urgency-row">
            {URGENCY_OPTIONS.map(option => (
              <label key={option.value} className={`portal-urgency-card ${form.urgency === option.value ? "selected" : ""}`}>
                <input
                  type="radio"
                  name="urgency"
                  checked={form.urgency === option.value}
                  onChange={() => update("urgency", option.value)}
                />
                <span className={priorityClass(option.label)}>{option.label}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset className="portal-fieldset">
          <legend>Description</legend>
          <label className="portal-field">
            <span>Request Details</span>
            <textarea
              value={form.description}
              onChange={e => update("description", e.target.value)}
              maxLength={5000}
              placeholder="Summarise the issue, action requested, relevant transaction details, and any customer-visible impact."
            />
          </label>
          <span className={`field-hint ${form.description.trim().length > 0 && form.description.trim().length < 20 ? "warn" : ""}`}>
            {form.description.trim().length}/5000 characters
          </span>
        </fieldset>

        <div className="portal-submit-row">
          <button type="button" onClick={onBack} disabled={submitting}>
            <ArrowLeft size={14} /> Portal
          </button>
          <button type="submit" className="primary" disabled={submitting}>
            <Send size={15} /> {submitting ? "Submitting..." : "Submit Request"}
          </button>
        </div>
      </form>
    </section>
  );
}

// ─── Nav Button ───────────────────────────────────────────────────────────────
function NavBtn({ id, current, Icon, label, onClick, colorClass, badge }: {
  id: string; current: string; Icon: any; label: string;
  onClick: () => void; colorClass?: string; badge?: number;
}) {
  return (
    <button
      className={`nav-btn ${current === id ? "active" : ""} ${colorClass ?? ""}`}
      onClick={onClick}
      title={label}
    >
      <Icon size={16} />
      <span>{label}</span>
      {badge ? <span className="nav-badge">{badge}</span> : null}
    </button>
  );
}

// ─── Central View ─────────────────────────────────────────────────────────────
function CentralView(props: {
  dashboard: Dashboard | null; queue: CaseSummary[];
  selectedCase: CaseDetail | null;
  emailText: string; setEmailText: (v: string) => void;
  processText: () => void; processFile: (f: File | null) => void;
  loadCase: (id: string) => void; busy: boolean;
  progress: { pct: number; stage: string } | null;
  refreshAll: () => void;
  readiness: SystemReadiness | null; verifyingModels: boolean;
  verifyModels: () => void;
}) {
  const { dashboard, queue, selectedCase } = props;
  const { progress } = props;
  // Newest-first in central; queue ordering is done per-department, not here
  const centralActive = [...queue].sort((a, b) => (b.created_at ?? "") > (a.created_at ?? "") ? 1 : -1);
  const closedQueue = [...(dashboard?.closed_queue ?? [])];
  const [queueTab, setQueueTab] = useState<"active" | "closed" | "assistant">("active");

  return (
    <PanelGroup orientation="horizontal" style={{ height: "100%", overflow: "hidden" }}>
      {/* Left: Email Intake */}
      <Panel defaultSize={22} minSize={12}>
      <section className="panel intake-panel" style={{ height: "100%" }}>
        <PanelTitle title="Email Intake" icon={<Upload size={17} />} />
        <label className="file-drop">
          <input type="file" accept=".txt,.eml,text/plain,message/rfc822" onChange={e => props.processFile(e.target.files?.[0] ?? null)} />
          <Upload size={20} /><span>Upload .txt / .eml</span>
        </label>
        <textarea
          value={props.emailText}
          onChange={e => props.setEmailText(e.target.value)}
          placeholder={"From: customer@example.com\nSubject: ...\n\nPaste email here..."}
        />
        <div className="intake-footer-row">
          <span className={`char-counter ${props.emailText.length > 0 && props.emailText.trim().length < 30 ? "char-counter-warn" : ""}`}>
            {props.emailText.length.toLocaleString()} chars
          </span>
          {props.emailText.length > 0 && (
            <button
              type="button"
              style={{ fontSize: 11, padding: "3px 8px" }}
              onClick={() => props.setEmailText("")}
              title="Clear intake"
            >
              <XCircle size={12} /> Clear
            </button>
          )}
        </div>
        <div className="button-row">
          <button className="primary" onClick={props.processText} disabled={props.busy || !props.emailText.trim()}>
            <Bot size={16} /> {props.busy ? "Analysing…" : "Process"}
          </button>
          <SampleEmailButton onInsert={props.setEmailText} />
          <button onClick={() => props.refreshAll()} disabled={props.busy}>
            <RefreshCcw size={14} /> Refresh
          </button>
        </div>

        {/* Progress card — centered overlay shown while processing */}
        {progress && (
          <div className="progress-overlay">
            <div className="progress-card">
              <div className="progress-card-header">
                <Bot size={18} />
                <span>Analysing Email</span>
              </div>
              <div className="progress-card-body">
                <div className="progress-pct-big">{progress.pct}%</div>
                <div className="progress-stage-text">{progress.stage}</div>
                <div className="progress-track">
                  <div className="progress-fill" style={{ width: `${progress.pct}%` }} />
                </div>
                <div className="progress-hint">Triage in progress · please keep this window open</div>
              </div>
            </div>
          </div>
        )}

        <div className="model-readiness">
          <div className="model-readiness-head">
            <strong>Inference Readiness</strong>
            <button onClick={props.verifyModels} disabled={props.verifyingModels || props.busy}>
              <MonitorCheck size={13} /> {props.verifyingModels ? "Testing…" : "Verify"}
            </button>
          </div>
          <div className="model-row">
            <span>BERT semantic + NER</span>
            <strong className={`model-state model-${props.readiness?.bert.status ?? "checking"}`}>
              {props.readiness?.bert.status ?? "checking"}
            </strong>
          </div>
          <div className="model-row">
            <span>Ollama {props.readiness?.ollama.model ?? "the configured local model"}</span>
            <strong className={`model-state model-${props.readiness?.ollama.status ?? "checking"}`}>
              {props.readiness?.ollama.status ?? "checking"}
            </strong>
          </div>
          {(props.readiness?.bert.error || props.readiness?.ollama.error) && (
            <small className="diagnostic-error">{props.readiness?.bert.error || props.readiness?.ollama.error}</small>
          )}
        </div>

        <MetricGrid items={[
          ["Total Cases",    dashboard?.counts.total_cases ?? 0,                           "brand"],
          ["Open",           dashboard?.counts.open_cases ?? 0,                            "info"],
          ["Critical",       dashboard?.counts.critical_unresolved ?? 0,                   "danger"],
          ["Needs Triage",   dashboard?.counts.needs_human_triage ?? 0,                   "warning"],
          ["Error Queue",    dashboard?.counts.error_queue ?? 0,                           "danger"],
          ["Low Confidence", dashboard?.ai_confidence_metrics?.low_confidence_cases ?? 0, "warning"],
        ]} />

        {dashboard && (
          <div className="classification-stream">
            <div className="stream-head">Recent Cases</div>
            {(dashboard.classification_stream ?? []).length === 0 && (
              <div className="stream-empty">No cases yet.</div>
            )}
            {(dashboard.classification_stream ?? []).map((row: any, i: number) => {
              const isSettled = row.workflow_state === "RESOLVED" || row.workflow_state === "CLOSED";
              const conf = row.confidence_score != null ? Math.round(row.confidence_score * 100) : null;
              const dept = (row.primary_department ?? "")
                .replace(" & Escalation Desk", "").replace(" Desk", "").replace("Central Operations", "Central Ops");
              return (
                <button key={i} className="stream-row stream-row-btn" onClick={() => props.loadCase(row.case_id)}>
                  <span className="stream-row-top">
                    <span className="stream-cls">{row.classification}</span>
                    {conf !== null && conf > 0 && <span className="stream-conf">{conf}%</span>}
                  </span>
                  <span className="stream-row-meta">
                    <span className="stream-id">{row.case_id}</span>
                    {dept && <span className="stream-dept">{dept}</span>}
                    <span className={`stream-state ${isSettled ? "stream-state-settled" : "stream-state-active"}`}>
                      {(row.workflow_state ?? "").replace(/_/g, " ")}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </section>
      </Panel>
      <PanelResizeHandle className="resize-handle-v" />

      {/* Middle: Read-only case list — newest first, no ranking controls */}
      <Panel defaultSize={39} minSize={15}>
      <section className="panel" style={{ height: "100%" }}>
        <div className="queue-tabs">
          <button className={`queue-tab ${queueTab === "active" ? "active" : ""}`} onClick={() => setQueueTab("active")}>
            Active <span className="tab-count">{centralActive.length}</span>
          </button>
          <button className={`queue-tab ${queueTab === "closed" ? "active" : ""}`} onClick={() => setQueueTab("closed")}>
            Closed <span className="tab-count">{closedQueue.length}</span>
          </button>
        </div>
        {queueTab === "active"
          ? <CentralCaseList cases={centralActive} loadCase={props.loadCase} selectedCaseId={selectedCase?.case_id} />
          : <ClosedCaseList  cases={closedQueue}   loadCase={props.loadCase} selectedCaseId={selectedCase?.case_id} />
        }
      </section>
      </Panel>
      <PanelResizeHandle className="resize-handle-v" />

      {/* Right: Read-only case detail */}
      <Panel defaultSize={39} minSize={15}>
      <section className="panel" style={{ height: "100%" }}>
        <PanelTitle title="Case Detail" icon={<FileSearch size={17} />} />
        {!selectedCase
          ? (
            <div className="empty-state">
              <FileSearch size={32} style={{ color: "var(--faint)", marginBottom: 8 }} />
              <div>No case selected.</div>
              <small>Click a case from the queue to view details here.</small>
            </div>
          )
          : <CentralCaseDetail c={selectedCase} loadCase={props.loadCase} />
        }
      </section>
      </Panel>
    </PanelGroup>
  );
}

// ─── Central read-only case list (no ordering arrows) ─────────────────────────
function CentralCaseList({ cases, loadCase, selectedCaseId }: {
  cases: CaseSummary[]; loadCase: (id: string) => void; selectedCaseId?: string;
}) {
  if (!cases.length) return (
    <div className="empty-state">
      <Inbox size={32} style={{ color: "var(--faint)", marginBottom: 8 }} />
      <div>No active cases.</div>
      <small>Process an email above to create the first case.</small>
    </div>
  );
  return (
    <div className="case-list">
      {cases.map(item => (
        <button
          key={item.case_id}
          className={`case-row ${selectedCaseId === item.case_id ? "selected" : ""} ${item.is_parent ? "case-row-parent" : ""} ${item.parent_case_id ? "case-row-child" : ""}`}
          onClick={() => loadCase(item.case_id)}
        >
          <span className={priorityClass(item.priority)}>{item.priority}</span>
          <span className="case-main">
            <span className="case-id-line">
              <strong>{item.case_id}</strong>
              <CopyButton text={item.case_id} title="Copy case ID" />
              {item.is_parent && <span className="multi-dept-badge">MULTI-DEPT ×{item.child_count}</span>}
              {item.needs_human_triage && <span className="triage-badge" style={{ fontSize: 9 }}><BrainCircuit size={9} /> Triage</span>}
              {item.intake_flags?.verdict === "external_bank" && <span style={{ fontSize: 9, padding: "1px 5px", borderRadius: 4, background: "var(--danger-bg)", color: "var(--danger)", fontWeight: 600 }}>Non-Docket</span>}
              {item.intake_flags?.verdict === "off_topic" && <span style={{ fontSize: 9, padding: "1px 5px", borderRadius: 4, background: "var(--warning-bg)", color: "var(--warning)", fontWeight: 600 }}>Off-topic</span>}
              {item.intake_flags?.verdict === "sparse" && <span style={{ fontSize: 9, padding: "1px 5px", borderRadius: 4, background: "var(--info-bg)", color: "var(--info)", fontWeight: 600 }}>Sparse</span>}
              {item.intake_flags?.thread_detected && <span style={{ fontSize: 9, padding: "1px 5px", borderRadius: 4, background: "var(--compliance-bg)", color: "var(--compliance)", fontWeight: 600 }}>Thread</span>}
              {item.intake_flags?.repeat_detection?.repeat_type === "exact_repeat" && (
                <span style={{ fontSize: 9, padding: "1px 5px", borderRadius: 4, background: "var(--danger-bg)", color: "var(--danger)", fontWeight: 600, display: "flex", alignItems: "center", gap: 2 }}>
                  <RotateCcw size={8} /> Repeat
                </span>
              )}
              {item.intake_flags?.clarification?.needed && (
                <span style={{ fontSize: 9, padding: "1px 5px", borderRadius: 4, background: "var(--info-bg)", color: "var(--info)", fontWeight: 600, display: "flex", alignItems: "center", gap: 2 }}>
                  <Mail size={8} /> Clarify
                </span>
              )}
              {item.intake_flags?.sender_validation_run && !item.intake_flags?.customer_matched && (
                <UnverifiedBadge />
              )}
            </span>
            <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <small>{item.summary || item.classification}</small>
              <SlaChip alerts={item.sla_alerts ?? []} slaMeta={item.sla_metadata} state={item.workflow_state} />
            </span>
          </span>
          <span className={stateClass(item.workflow_state)}>{item.workflow_state.replace(/_/g, " ")}</span>
        </button>
      ))}
    </div>
  );
}

// ─── Central read-only case detail ────────────────────────────────────────────
function CentralCaseDetail({ c, loadCase }: { c: CaseDetail; loadCase: (id: string) => void }) {
  const [expandedTl, setExpandedTl] = useState<number | null>(null);
  const [openSolution, setOpenSolution] = useState<SuggestedSolution | null>(null);
  const [customerData, setCustomerData] = useState<any | null>(null);
  const [customerLoading, setCustomerLoading] = useState(false);
  const [threadData, setThreadData] = useState<any | null>(null);
  const [threadLoading, setThreadLoading] = useState(false);
  const [draftResponse, setDraftResponse] = useState<any | null>(null);
  const [draftLoading, setDraftLoading] = useState(false);
  const [feedbackClass, setFeedbackClass] = useState("");
  const [feedbackNote, setFeedbackNote] = useState("");
  const [feedbackSent, setFeedbackSent] = useState(false);
  const ai = c.ai_analysis || {};
  const fields = c.extracted_fields || {};
  const timeline = (c.unified_timeline || []).slice().reverse().slice(0, 15);
  const solutions = c.suggested_solutions ?? [];
  const children = c.children ?? [];

  // Reset panels when case changes
  useEffect(() => {
    setCustomerData(null); setThreadData(null);
    setDraftResponse(null); setFeedbackSent(false); setFeedbackClass(""); setFeedbackNote("");
  }, [c.case_id]);

  async function lookupCustomer() {
    setCustomerLoading(true);
    try { setCustomerData(await api.customerLookup(c.case_id)); }
    catch { setCustomerData({ error: "Lookup failed." }); }
    finally { setCustomerLoading(false); }
  }

  async function summarizeThread() {
    setThreadLoading(true);
    try { setThreadData(await api.summarizeThread(c.case_id)); }
    catch { setThreadData({ error: "Summarization failed." }); }
    finally { setThreadLoading(false); }
  }

  async function generateDraft() {
    setDraftLoading(true);
    try { setDraftResponse(await api.generateDraftResponse(c.case_id)); }
    catch { setDraftResponse({ error: "Draft generation failed." }); }
    finally { setDraftLoading(false); }
  }

  async function sendFeedback() {
    if (!feedbackClass) return;
    try {
      await api.submitFeedback(c.case_id, feedbackClass, feedbackNote || undefined);
      setFeedbackSent(true);
    } catch { /* ignore */ }
  }

  return (
    <div className="case-workspace">
      {/* Solution popup */}
      {openSolution && (
        <div className="solution-overlay" onClick={() => setOpenSolution(null)}>
          <div className="solution-popup" onClick={e => e.stopPropagation()}>
            <div className="solution-popup-head">
              <Lightbulb size={15} />
              <span>Matched — {openSolution.case_id}</span>
              <button className="solution-popup-close" onClick={() => setOpenSolution(null)}>✕</button>
            </div>
            <div className="solution-popup-meta">
              <span className="suggestion-cls">{openSolution.classification}</span>
              <span className="suggestion-score">{Math.round(openSolution.similarity * 100)}% match</span>
            </div>
            {openSolution.summary && <div className="solution-popup-summary">{openSolution.summary}</div>}
            <div className="solution-popup-resolution">
              <div className="solution-popup-label">Resolution applied:</div>
              <div className="solution-popup-text">{openSolution.resolution_text}</div>
            </div>
          </div>
        </div>
      )}

      {ai.reopened_from && (
        <div className="reopen-banner">
          <RotateCcw size={11} />
          <span>Reopened from </span>
          <button className="reopen-banner-link" onClick={() => loadCase(ai.reopened_from)}>{ai.reopened_from}</button>
          {ai.reopen_note && <span className="reopen-banner-note"> — {ai.reopen_note}</span>}
        </div>
      )}

      {ai.portal_submission && (ai.suggested_department || c.extracted_fields?.suggested_department) && (
        <div className="portal-routing-banner">
          <Navigation size={11} />
          <span>Portal submission — suggested routing: </span>
          <strong>{ai.suggested_department || c.extracted_fields?.suggested_department}</strong>
          {ai.portal_request_type && <span className="portal-routing-type"> · {ai.portal_request_type}</span>}
        </div>
      )}

      <div className="summary-line">
        <span className={priorityClass(c.priority)}>{c.priority}</span>
        <strong>{c.case_id}</strong>
        <CopyButton text={c.case_id} title="Copy case ID" />
        <span className={stateClass(c.workflow_state)}>{c.workflow_state.replace(/_/g, " ")}</span>
        {c.needs_human_triage && <span className="triage-badge"><BrainCircuit size={10} /> Triage</span>}
        {c.language && c.language !== "English" && <span className="lang-badge"><Globe size={10} /> {c.language}</span>}
        {c.incident_group && <span className="incident-badge">{c.incident_group}</span>}
      </div>

      <div className="central-readonly-note">
        Read-only view. Case actions are performed at the assigned departmental desk.
      </div>

      {c.intake_flags?.sender_validation_run && !c.intake_flags?.customer_matched && (
        <div className="unverified-sender-banner">
          <span className="unverified-sender-icon">⚠</span>
          <div>
            <strong>Unverified sender</strong>
            <div className="unverified-sender-detail">
              {c.intake_flags.customer_validation_status === "no_identifiers"
                ? "No account identifiers found in this email. Sender could not be matched to any Docket customer."
                : c.intake_flags.customer_validation_status === "none"
                ? "Identifiers were extracted but did not match any customer record in the Docket database."
                : "Sender identity could not be fully verified against the Docket customer database."}
            </div>
          </div>
        </div>
      )}

      {c.intake_flags && <SmartIntakePanel flags={c.intake_flags as IntakeFlags} loadCase={loadCase} />}

      {/* Entities extracted from this email */}
      {ai.extracted_entities && <ExtractedEntitiesPanel entities={ai.extracted_entities} />}

      <div className="info-grid">
        <Info label="Classification" value={c.classification} />
        <Info label="Primary Desk"   value={c.primary_department} />
        <Info label="AI Confidence"  value={`${Math.round(c.confidence_score * 100)}%`} />
        <Info label="Risk Score"     value={`${Math.round(c.risk_score * 100)}%`} />
        <Info label="Customer"       value={fields.customer_name || c.customer_metadata?.sender_email_masked} />
        <Info label="Amount"         value={fields.amount_involved ? `₹${Number(fields.amount_involved).toLocaleString("en-IN")}` : null} />
        {ai.sentiment && (
          <div className="info">
            <span>Sentiment</span>
            <strong style={{ fontWeight: "normal" }}>
              <SentimentDisplay sentiment={ai.sentiment} tone={ai.emotional_tone} distress={ai.distress_level} intensity={ai.sentiment_intensity} />
            </strong>
          </div>
        )}
      </div>

      {c.secondary_departments?.length > 0 && (
        <div className="tag-row">
          {c.secondary_departments.map((s: any, i: number) => (
            <span key={i} className="finding-tag tag-info">{s.department} ({Math.round((s.confidence ?? 0) * 100)}%)</span>
          ))}
        </div>
      )}

      {/* Multi-dept sub-case status — key logic: closed for each dept independently */}
      {c.is_parent && children.length > 0 && (
        <section className="subsection">
          <h3>Department Sub-Cases</h3>
          <div className="central-subdept-note">
            Each department resolves their own sub-case independently. The case closes in central only when all departments have closed.
          </div>
          <div className="subdept-grid">
            {children.map(child => (
              <button key={child.case_id} className="subdept-card" onClick={() => loadCase(child.case_id)}>
                <div className="subdept-dept">{child.primary_department}</div>
                <span className={stateClass(child.workflow_state)}>{child.workflow_state.replace(/_/g, " ")}</span>
                <span className={priorityClass(child.priority)}>{child.priority}</span>
                {child.sla_alerts?.length > 0 && (
                  <span className="sla-badge sla-nearing_breach" style={{ fontSize: 9 }}>SLA</span>
                )}
              </button>
            ))}
          </div>
        </section>
      )}

      {solutions.length > 0 && (
        <section className="subsection">
          <h3 style={{ display: "flex", alignItems: "center", gap: 5, color: "var(--brand)" }}>
            <Lightbulb size={13} /> Previous Solutions ({solutions.length})
          </h3>
          <div className="suggestion-chips">
            {solutions.map((s: SuggestedSolution) => (
              <button key={s.case_id} className="suggestion-chip" onClick={() => setOpenSolution(s)}>
                <span className="suggestion-chip-cls">{s.classification}</span>
                <span className="suggestion-chip-score">{Math.round(s.similarity * 100)}% match</span>
                <ChevronDown size={11} />
              </button>
            ))}
          </div>
        </section>
      )}

      {/* Original case context (reopened cases only) */}
      {ai.reopened_from && (
        <OriginalCaseContextPanel ai={ai} loadCase={loadCase} />
      )}

      {/* Customer Validation */}
      <section className="subsection">
        <h3 style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Users size={13} /> Customer Record
          {!customerData && (
            <button style={{ marginLeft: "auto", fontSize: 10, padding: "2px 8px" }} onClick={lookupCustomer} disabled={customerLoading}>
              {customerLoading ? "Looking up…" : "Validate"}
            </button>
          )}
        </h3>
        {customerLoading && <div style={{ fontSize: 11, color: "var(--muted)" }}>Looking up customer…</div>}
        {customerData && !customerData.error && (
          <CustomerCard data={customerData} onRevalidate={lookupCustomer} loading={customerLoading} />
        )}
        {customerData?.error && <div style={{ fontSize: 11, color: "var(--danger)" }}>{customerData.error}</div>}
        {!customerData && !customerLoading && (
          <div style={{ fontSize: 11, color: "var(--very-muted)" }}>Click "Validate" to match identifiers from this case against the customer database.</div>
        )}
      </section>

      {/* Thread Summary */}
      <section className="subsection">
        <h3 style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <MessageSquare size={13} /> Email Thread
          {!threadData && (
            <button style={{ marginLeft: "auto", fontSize: 10, padding: "2px 8px" }} onClick={summarizeThread} disabled={threadLoading}>
              {threadLoading ? "Summarising…" : "Detect & Summarise"}
            </button>
          )}
        </h3>
        {threadData && !threadData.error && (
          <div className="thread-summary-panel">
            {!threadData.is_thread ? (
              <div style={{ fontSize: 11, color: "var(--muted)" }}>Single email — no thread detected.</div>
            ) : (
              <>
                <div className="thread-meta">
                  <span className="thread-depth-badge">Thread depth: {threadData.depth}</span>
                  {threadData.cached && <span style={{ fontSize: 10, color: "var(--very-muted)" }}>cached</span>}
                </div>
                {threadData.summary && (
                  <div className="thread-summary-text">{threadData.summary}</div>
                )}
                <button style={{ fontSize: 10, padding: "2px 8px", marginTop: 6 }} onClick={summarizeThread} disabled={threadLoading}>
                  {threadLoading ? "…" : "Re-summarise"}
                </button>
              </>
            )}
          </div>
        )}
        {threadData?.error && <div style={{ fontSize: 11, color: "var(--danger)" }}>{threadData.error}</div>}
        {!threadData && !threadLoading && (
          <div style={{ fontSize: 11, color: "var(--very-muted)" }}>Click "Detect & Summarise" to check if this is an email thread and get a Gemma4 summary.</div>
        )}
      </section>

      {/* AI Draft Response */}
      <section className="subsection">
        <h3 style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Send size={13} /> AI Draft Response
          {!draftResponse && (
            <button style={{ marginLeft: "auto", fontSize: 10, padding: "2px 8px" }} onClick={generateDraft} disabled={draftLoading}>
              {draftLoading ? "Generating…" : "Generate Draft"}
            </button>
          )}
          {draftResponse && !draftResponse.error && (
            <button style={{ marginLeft: "auto", fontSize: 10, padding: "2px 8px" }} onClick={generateDraft} disabled={draftLoading}>
              {draftLoading ? "Regenerating…" : "Regenerate"}
            </button>
          )}
        </h3>
        {draftResponse && !draftResponse.error && (
          <div style={{ fontSize: 11, lineHeight: 1.6 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
              <span style={{ fontSize: 10, padding: "1px 6px", borderRadius: 4, background: draftResponse.source === "llm" ? "var(--compliance-bg)" : "var(--surface-3)", color: draftResponse.source === "llm" ? "var(--compliance)" : "var(--muted)" }}>
                {draftResponse.source === "llm" ? "AI Generated" : "Template"}
              </span>
            </div>
            <div style={{ background: "var(--surface-raised)", borderRadius: 6, padding: "8px 10px", marginBottom: 8 }}>
              <div style={{ fontWeight: 600, color: "var(--text-primary)", marginBottom: 4 }}>Subject: {draftResponse.subject}</div>
              <pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit", fontSize: 11, color: "var(--text-secondary)", margin: 0 }}>{draftResponse.body}</pre>
            </div>
          </div>
        )}
        {draftResponse?.error && <div style={{ fontSize: 11, color: "var(--danger)" }}>{draftResponse.error}</div>}
        {!draftResponse && !draftLoading && (
          <div style={{ fontSize: 11, color: "var(--very-muted)" }}>Generate an AI-powered draft response for this customer email.</div>
        )}
      </section>

      {/* ML Feedback */}
      <section className="subsection">
        <h3 style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <BrainCircuit size={13} /> Classification Feedback
        </h3>
        {feedbackSent ? (
          <div style={{ fontSize: 11, color: "var(--success)" }}>✓ Feedback recorded. Thank you for improving the model.</div>
        ) : (
          <div style={{ fontSize: 11 }}>
            <div style={{ color: "var(--very-muted)", marginBottom: 6 }}>
              AI classified as: <strong style={{ color: "var(--text-primary)" }}>{c.classification}</strong>
              {" "}(confidence: {Math.round((c.confidence_score ?? 0) * 100)}%)
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <select
                style={{ fontSize: 11, padding: "4px 6px", borderRadius: 4, background: "var(--surface-raised)", border: "1px solid var(--border)", color: "var(--text-primary)", width: "100%" }}
                value={feedbackClass}
                onChange={e => setFeedbackClass(e.target.value)}
              >
                <option value="">— Select correct classification —</option>
                {["UNAUTHORISED_USE","CUSTOMER_GRIEVANCE","CONNECTION_FAULT","VERIFICATION_QUERY","REGULATORY","LEGAL_NOTICE","PORTAL_ACCESS","PAYMENT_FAILURE","ESCALATION","DUPLICATE","SPAM","GENERAL_QUERY"].map(cls => (
                  <option key={cls} value={cls}>{cls}</option>
                ))}
              </select>
              <input
                style={{ fontSize: 11, padding: "4px 6px", borderRadius: 4, background: "var(--surface-raised)", border: "1px solid var(--border)", color: "var(--text-primary)", width: "100%" }}
                placeholder="Optional note (e.g. missed fraud keywords)"
                value={feedbackNote}
                onChange={e => setFeedbackNote(e.target.value)}
              />
              <button
                style={{ fontSize: 10, padding: "4px 10px", alignSelf: "flex-start" }}
                onClick={sendFeedback}
                disabled={!feedbackClass}
              >
                Submit Feedback
              </button>
            </div>
          </div>
        )}
      </section>

      {/* Timeline */}
      <section className="subsection">
        <h3>Timeline</h3>
        <div className="timeline">
          {timeline.map((ev, i) => (
            <div key={i}>
              <button
                className={`timeline-row timeline-row-btn ${expandedTl === i ? "timeline-expanded" : ""} ${ev.event_type === "send_communication" ? "timeline-row-comm" : ""}`}
                onClick={() => setExpandedTl(expandedTl === i ? null : i)}
              >
                <strong>{ev.event_type === "send_communication" ? "✉ Communication" : ev.event_type?.replace(/_/g, " ")}</strong>
                <span>{ev.message}</span>
                <small>{new Date(ev.timestamp).toLocaleString("en-IN", { dateStyle: "short", timeStyle: "short" })}</small>
                <ChevronDown size={12} style={{ flexShrink: 0, transform: expandedTl === i ? "rotate(180deg)" : "none", transition: "transform .15s", color: "var(--faint)" }} />
              </button>
              {expandedTl === i && (
                <div className="timeline-detail">
                  {ev.actor && <div><span className="tl-label">By:</span> {ev.actor}{ev.department ? ` · ${ev.department}` : ""}</div>}
                  {ev.event_type === "send_communication" ? (
                    <div className="tl-comm-body">
                      {ev.metadata?.recipient_email && (
                        <div className="tl-comm-to"><span className="tl-label">To:</span> {ev.metadata.recipient_email}</div>
                      )}
                      <div className="tl-comm-status">
                        <span className="tl-label">Status:</span>
                        <span className="tl-comm-pending">Queued · pending SMTP</span>
                      </div>
                      <pre className="tl-comm-text">{ev.metadata?.communication_body}</pre>
                    </div>
                  ) : (
                    <>
                      {ev.metadata?.note && (
                        <div className="tl-note-body"><span className="tl-label">Note:</span> {ev.metadata.note}</div>
                      )}
                      {ev.metadata?.transfer_to && (
                        <div><span className="tl-label">Transferred to:</span> {ev.metadata.transfer_to}</div>
                      )}
                      {ev.metadata?.requested_department && (
                        <div><span className="tl-label">Requested dept:</span> {ev.metadata.requested_department}</div>
                      )}
                      {ev.metadata?.ticket_ref && (
                        <div><span className="tl-label">Ticket ref:</span> {ev.metadata.ticket_ref}</div>
                      )}
                      {ev.metadata?.regulatory_ref && (
                        <div><span className="tl-label">Regulatory ref:</span> {ev.metadata.regulatory_ref}</div>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

// ─── Toast notification ────────────────────────────────────────────────────────
const ACTION_LABELS: Record<string, string> = {
  acknowledge:        "Acknowledged",
  start_review:       "Review Started",
  send_communication: "Communication Sent",
  resolve:            "Case Resolved",
  request_department: "Transfer Requested",
  close:              "Case Closed",
  escalate:           "Case Escalated",
  reopen:             "Case Reopened",
};

function Toast({ message, onDismiss }: { message: string; onDismiss: () => void }) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 8000);
    return () => clearTimeout(t);
  }, [onDismiss]);
  return (
    <div className="toast-overlay" onClick={onDismiss}>
      <div className="toast-dialog" onClick={e => e.stopPropagation()}>
        <div className="toast-header">
          <CheckCircle2 size={18} />
          <span>Action Completed</span>
        </div>
        <div className="toast-body">
          <div className="toast-action-name">{message}</div>
          <p className="toast-body-text">
            The action has been recorded and the case has been updated successfully.
          </p>
        </div>
        <div className="toast-footer">
          <button className="primary toast-ok-btn" onClick={onDismiss}>OK</button>
        </div>
      </div>
    </div>
  );
}

// ─── Department Desk Console ──────────────────────────────────────────────────
function DeskConsoleView(props: {
  config: DeskConfig;
  queue: DepartmentQueue | null;
  selectedCase: CaseDetail | null;
  loadCase: (id: string) => void;
  action: (action: string, payload?: Record<string, any>) => Promise<string | false>;
  busy: boolean;
  refreshQueue: () => void;
  moveInQueue: (caseId: string, direction: "up" | "down") => void;
  reorderQueue: (caseId: string, newIndex: number) => void;
  onUnpin: (caseId: string) => void;
}) {
  const { config, queue, selectedCase } = props;
  const [note, setNote] = useState("");
  const [transferOpen, setTransferOpen] = useState(false);
  const [transferTo, setTransferTo] = useState("");
  const [deskTab, setDeskTab] = useState<"active" | "closed">("active");
  const [mainTab, setMainTab] = useState<"queue" | "analytics">("queue");
  const [toast, setToast] = useState<string | null>(null);
  const { Icon, colorClass, metricKeys, label, shortLabel, actions, keyFields } = config;

  const activeItems = useMemo(() => {
    const raw = queue?.items ?? [];
    const slaRank = (item: CaseSummary) => {
      const alerts = item.sla_alerts ?? [];
      if (alerts.some((a: any) => a.severity === "OVERDUE")) return 0;
      if (alerts.some((a: any) => a.severity === "NEARING_BREACH")) return 1;
      return 2;
    };
    return [...raw].sort((a, b) => {
      // Pinned cases float to top in their operator-set order
      const aPinned = a.queue_pinned && a.queue_position != null;
      const bPinned = b.queue_pinned && b.queue_position != null;
      if (aPinned && bPinned) return (a.queue_position ?? 9999) - (b.queue_position ?? 9999);
      if (aPinned) return -1;
      if (bPinned) return 1;
      // Auto-rank: SLA urgency first, then dept priority
      const sla = slaRank(a) - slaRank(b);
      if (sla !== 0) return sla;
      return config.caseSort(a, b);
    });
  }, [queue, config]);

  const closedItems = useMemo(() => queue?.closed_items ?? [], [queue]);

  const caseIsInQueue = useMemo(() => {
    if (!selectedCase) return false;
    return (
      activeItems.some(item => item.case_id === selectedCase.case_id) ||
      closedItems.some(item => item.case_id === selectedCase.case_id)
    );
  }, [selectedCase, activeItems, closedItems]);

  function nextStepHint(state: string): string {
    const hints: Record<string, string> = {
      ASSIGNED:               "Acknowledge the case to take ownership.",
      ACKNOWLEDGED:           "Start review to begin investigation.",
      UNDER_REVIEW:           "Communicate with the customer, then resolve.",
      WAITING_FOR_ACTION:     "Waiting on a response. Resolve when ready.",
      MULTI_DEPARTMENT_REVIEW:"Coordinate with other desks before resolving.",
      ESCALATED:              "Resolve or continue review once addressed.",
      RESOLVED:               "Case resolved. No further action needed.",
      CLOSED:                 "Case closed.",
      REOPENED:               "Acknowledge and restart the review flow.",
    };
    return hints[state] ?? "Select a valid action below.";
  }

  function blockReason(action: DeskAction): string | null {
    if (!selectedCase) return "Select a case from the queue.";
    if (!caseIsInQueue) return "This case is not in your queue.";
    if (selectedCase.is_parent) return "Parent case — open a sub-case to act.";
    const workflowReason = selectedCase.blocked_actions?.[action.id];
    if (workflowReason) return workflowReason;
    if (action.requires === "note" && !note.trim()) return "Enter a note first.";
    return null;
  }

  async function fireAction(action: DeskAction) {
    if (blockReason(action)) return;

    // Transfer button: toggle the transfer UI open instead of firing immediately
    if (action.id === "request_department") {
      setTransferOpen(true);
      return;
    }

    const payload: Record<string, any> = {};
    if (note.trim()) payload.note = note;

    const result = await props.action(action.id, payload);
    if (result !== false) {
      setNote("");
      setToast(ACTION_LABELS[action.id] || action.id.replace(/_/g, " "));
    }
  }

  async function confirmTransfer() {
    if (!transferTo) return;
    const payload: Record<string, any> = { requested_department: transferTo, transfer_to: transferTo };
    if (note.trim()) payload.note = note;
    const result = await props.action("request_department", payload);
    if (result !== false) {
      setNote(""); setTransferTo(""); setTransferOpen(false);
      setToast("Transfer Requested");
    }
  }

  return (
    <>
      {toast && <Toast message={toast} onDismiss={() => setToast(null)} />}

      {/* ── Main tab switcher: Queue vs Analytics ── */}
      <div className="queue-tabs" style={{ margin: "0 0 0 0", borderBottom: "1px solid var(--line)", flexShrink: 0 }}>
        <button
          className={`queue-tab ${mainTab === "queue" ? "active" : ""}`}
          onClick={() => setMainTab("queue")}
        >
          Queue
        </button>
        <button
          className={`queue-tab ${mainTab === "analytics" ? "active" : ""}`}
          onClick={() => setMainTab("analytics")}
        >
          Analytics
        </button>
      </div>

      {mainTab === "analytics" ? (
        <div style={{ flex: 1, overflow: "auto", padding: "16px 20px" }}>
          <DeptAnalytics deptSlug={config.slug} deptLabel={config.label} />
        </div>
      ) : (
      <PanelGroup orientation="horizontal" style={{ height: "100%", overflow: "hidden" }}>

      {/* Left: Queue panel */}
      <Panel defaultSize={30} minSize={18}>
      <section className={`panel desk-queue-panel ${colorClass}`} style={{ height: "100%" }}>
        <div className={`desk-header ${colorClass}`}>
          <Icon size={20} />
          <div style={{ minWidth: 0, overflow: "hidden" }}>
            <div className="desk-title">{shortLabel}</div>
            <div className="desk-subtitle">{label}</div>
          </div>
          <button className="refresh-btn" onClick={props.refreshQueue} title="Refresh queue">
            <RefreshCcw size={14} />
          </button>
        </div>

        <div className="desk-metrics">
          {metricKeys.map(m => (
            <div key={m.key} className="desk-metric">
              <span>{m.label}</span>
              <strong>{queue ? m.compute(queue) : "—"}</strong>
            </div>
          ))}
        </div>

        <div className="queue-tabs">
          <button className={`queue-tab ${deskTab === "active" ? "active" : ""}`} onClick={() => setDeskTab("active")}>
            Active <span className="tab-count">{activeItems.length}</span>
          </button>
          <button className={`queue-tab ${deskTab === "closed" ? "active" : ""}`} onClick={() => setDeskTab("closed")}>
            Closed <span className="tab-count">{closedItems.length}</span>
          </button>
        </div>
        {queue === null
          ? <QueueSkeleton />
          : deskTab === "active"
            ? <QueueList cases={activeItems} loadCase={props.loadCase} selectedCaseId={selectedCase?.case_id} moveInQueue={props.moveInQueue} reorderQueue={props.reorderQueue} onUnpin={props.onUnpin} busy={props.busy} />
            : <ClosedCaseList cases={closedItems} loadCase={props.loadCase} selectedCaseId={selectedCase?.case_id} />
        }
      </section>
      </Panel>
      <PanelResizeHandle className="resize-handle-v" />

      {/* Right: Actions + Case workspace */}
      <Panel defaultSize={70} minSize={30}>
      <section className="panel desk-workspace-panel" style={{ height: "100%" }}>
        <PanelTitle title={`${shortLabel} Console`} icon={<Icon size={17} />} />

        {/* Workflow state guide */}
        <div className="workflow-guide">
          {selectedCase ? (
            <>
              <strong>{selectedCase.workflow_state.replace(/_/g, " ")}</strong>
              <span>{nextStepHint(selectedCase.workflow_state)}</span>
            </>
          ) : (
            <span style={{ color: "var(--muted)" }}>Select a case from the queue to begin.</span>
          )}
        </div>

        {/* Standard action buttons */}
        <div className="desk-actions">
          {actions.map(a => {
            const reason = blockReason(a);
            const isTransfer = a.id === "request_department";
            return (
              <button
                key={a.id}
                disabled={props.busy || Boolean(reason)}
                className={[
                  a.variant === "primary" ? "primary" : a.variant === "danger" ? "danger" : a.variant === "warn" ? "warn" : "",
                  isTransfer && transferOpen ? "primary" : "",
                ].filter(Boolean).join(" ")}
                onClick={() => fireAction(a)}
                title={reason ?? `${a.label} — ready`}
              >
                <a.Icon size={14} /> {a.label}
              </button>
            );
          })}
        </div>

        {/* Transfer UI — only shown after clicking Transfer to Desk */}
        {transferOpen && (
          <div className="transfer-panel">
            <div className="transfer-panel-head">
              <ArrowRightLeft size={14} />
              <span>Select destination desk</span>
              <button style={{ marginLeft: "auto", padding: "2px 6px", fontSize: 11 }} onClick={() => { setTransferOpen(false); setTransferTo(""); }}>
                Cancel
              </button>
            </div>
            <select value={transferTo} onChange={e => setTransferTo(e.target.value)}>
              <option value="">— Choose a department —</option>
              {Object.values(DESKS).filter(d => d.label !== label).map(d => (
                <option key={d.slug} value={d.label}>{d.label}</option>
              ))}
            </select>
            <button className="primary" disabled={!transferTo || props.busy} onClick={confirmTransfer}>
              <CheckCheck size={14} /> Confirm Transfer
            </button>
          </div>
        )}

        {/* Note box — shown when a note is needed or transfer is open */}
        <textarea
          className="note-box"
          value={note}
          onChange={e => setNote(e.target.value)}
          placeholder="Add a note (required for Send Communication and Resolve Case)"
        />

        {/* Case detail */}
        <CaseWorkspace selectedCase={selectedCase} keyFields={keyFields} compact loadCase={props.loadCase} />
      </section>
      </Panel>
      </PanelGroup>
      )}
    </>
  );
}

// ─── Findings Strip ───────────────────────────────────────────────────────────
function FindingsStrip({ findings, slug }: { findings: Record<string, any>; slug: string }) {
  if (!Object.keys(findings).length) return null;
  const tags: { label: string; cls: string }[] = [];

  if (findings.service_suspended) tags.push({ label: "Account Blocked", cls: "tag-danger" });
  if (findings.forensics_requested) tags.push({ label: "Forensics Requested", cls: "tag-warn" });
  if (findings.flagged_transactions?.length) tags.push({ label: `${findings.flagged_transactions.length} Txn Flagged`, cls: "tag-warn" });
  if (findings.resolution_offered) tags.push({ label: "Resolution Offered", cls: "tag-success" });
  if (findings.regulatory_report_raised) tags.push({ label: `Regulatory Report Filed${findings.regulatory_ref ? ` (${findings.regulatory_ref})` : ""}`, cls: "tag-info" });
  if (findings.it_ticket_created) tags.push({ label: `IT Ticket: ${findings.it_ticket_ref ?? "Open"}`, cls: "tag-info" });

  if (!tags.length) return null;
  return (
    <div className="findings-strip">
      {tags.map((t, i) => <span key={i} className={`finding-tag ${t.cls}`}>{t.label}</span>)}
    </div>
  );
}

// ─── Triage Label Display ─────────────────────────────────────────────────────
function TriageLabelDisplay({ labels }: { labels: TriageLabel[] }) {
  if (!labels?.length) return null;
  return (
    <div className="triage-labels">
      {labels.map((l, i) => (
        <span key={i} className={`triage-label-pill source-${l.source}`}>
          {l.label}
          <span className="triage-label-conf">{Math.round(l.confidence * 100)}%</span>
          <span className="triage-label-conf" style={{ opacity: .6 }}>{l.source}</span>
        </span>
      ))}
    </div>
  );
}

// ─── Human Triage Queue View ──────────────────────────────────────────────────
// All 8 operational desks — always shown regardless of what's in the DB
const ALL_TRIAGE_DESKS = Object.values(DESKS).map(d => ({ label: d.label, slug: d.slug }));

function TriageQueueView(props: {
  cases: any[];
  loadCase: (id: string) => void;
  selectedCase: CaseDetail | null;
  onRelease: (caseId: string, depts: string[], cls: string | undefined, note: string | undefined) => Promise<void>;
  busy: boolean;
  refresh: () => void;
}) {
  const { cases, selectedCase, onRelease, busy } = props;
  const [activeCaseId, setActiveCaseId] = useState<string | null>(null);
  const [selectedDepts, setSelectedDepts] = useState<string[]>([]);
  const [editedComplaint, setEditedComplaint] = useState("");
  const [complaintSaving, setComplaintSaving] = useState(false);
  const [complaintSaved, setComplaintSaved] = useState(false);
  const [customerData, setCustomerData] = useState<any | null>(null);
  const [customerLoading, setCustomerLoading] = useState(false);
  const [showRawEmail, setShowRawEmail] = useState(false);

  function toggleDept(label: string) {
    setSelectedDepts(prev =>
      prev.includes(label) ? prev.filter(d => d !== label) : [...prev, label]
    );
  }

  // When a case is selected: pre-fill complaint editor + auto-load customer match
  useEffect(() => {
    if (!selectedCase || selectedCase.case_id !== activeCaseId) return;
    const edited = selectedCase.ai_analysis?.operator_edited_complaint;
    setEditedComplaint(edited || selectedCase.normalized_text || "");
    setComplaintSaved(!!edited);
    // Auto-load customer validation
    setCustomerData(null);
    setCustomerLoading(true);
    api.customerLookup(selectedCase.case_id)
      .then(d => setCustomerData(d))
      .catch(() => setCustomerData({ error: "Lookup failed." }))
      .finally(() => setCustomerLoading(false));
  }, [selectedCase?.case_id, activeCaseId]);

  async function saveComplaint() {
    if (!activeCaseId || !editedComplaint.trim()) return;
    setComplaintSaving(true);
    try {
      await api.editTriageComplaint(activeCaseId, editedComplaint);
      setComplaintSaved(true);
    } catch { /* ignore */ }
    finally { setComplaintSaving(false); }
  }

  const activeCase = cases.find(c => c.case_id === activeCaseId) ?? null;

  return (
    <div className="triage-console">
      <div className="triage-header">
        <BrainCircuit size={22} />
        <div>
          <h3>Human Triage Queue</h3>
          <p>Cases where AI confidence was too low to auto-route — a human must classify and release each one.</p>
        </div>
        <span className="triage-count">{cases.length}</span>
        <button onClick={props.refresh}><RefreshCcw size={13} /></button>
      </div>

      {!cases.length
        ? <div className="empty">No cases waiting for human triage. The AI is handling everything confidently.</div>
        : (
          <PanelGroup orientation="horizontal" style={{ flex: 1, overflow: "hidden" }}>
            <Panel defaultSize={60} minSize={25}>
            <div className="triage-list" style={{ height: "100%", overflowY: "auto" }}>
              {cases.map(c => (
                <div
                  key={c.case_id}
                  className={`triage-case-row ${activeCaseId === c.case_id ? "selected" : ""}`}
                  onClick={() => { setActiveCaseId(c.case_id); props.loadCase(c.case_id); setSelectedDepts([]); setComplaintSaved(false); setEditedComplaint(""); }}
                >
                  <div className="triage-case-meta">
                    <span className="triage-case-id">{c.case_id}</span>
                    {c.language && c.language !== "English" && (
                      <span className="lang-badge"><Globe size={10} /> {c.language}</span>
                    )}
                    <span className={`priority ${(c.priority || "low").toLowerCase()}`}>{c.priority}</span>
                    <span className="triage-badge"><BrainCircuit size={10} /> Needs Review</span>
                    <small style={{ marginLeft: "auto", color: "var(--very-muted)", fontSize: 10 }}>
                      {c.created_at ? new Date(c.created_at).toLocaleString("en-IN") : ""}
                    </small>
                  </div>
                  <TriageLabelDisplay labels={c.triage_labels || []} />
                  {c.abstain_reason && (
                    <div className="triage-abstain-reason">"{c.abstain_reason}"</div>
                  )}
                  <small style={{ color: "var(--muted)", fontSize: 10.5 }}>
                    {c.summary || c.classification || "No summary"}
                  </small>
                </div>
              ))}
            </div>
            </Panel>
            <PanelResizeHandle className="resize-handle-v" />
            <Panel defaultSize={40} minSize={25}>
            <div className="triage-action-panel" style={{ height: "100%", overflowY: "auto" }}>
              {!activeCase
                ? <div className="empty" style={{ padding: 16 }}>Select a case to classify and release it.</div>
                : (
                  <>
                    {/* ── Case header ── */}
                    <div className="triage-intel-header">
                      <span className={priorityClass(activeCase.priority)}>{activeCase.priority}</span>
                      <strong style={{ fontSize: 12 }}>{activeCase.case_id}</strong>
                      <span className={stateClass(activeCase.workflow_state)}>{activeCase.workflow_state.replace(/_/g," ")}</span>
                      {activeCase.language && activeCase.language !== "English" && (
                        <span className="lang-badge"><Globe size={9} />{activeCase.language}</span>
                      )}
                    </div>

                    {/* ── AI labels + abstain ── */}
                    <TriageLabelDisplay labels={activeCase.triage_labels || []} />
                    {activeCase.abstain_reason && (
                      <div className="pipeline-warning" style={{ fontSize: 11 }}>
                        AI abstained: {activeCase.abstain_reason}
                      </div>
                    )}

                    {/* ── Summary ── */}
                    {(selectedCase?.case_id === activeCaseId) && selectedCase?.summary && (
                      <div className="triage-summary-block">{selectedCase.summary}</div>
                    )}

                    {/* ── Unverified sender ── */}
                    {selectedCase?.case_id === activeCaseId && selectedCase?.intake_flags?.sender_validation_run && !selectedCase?.intake_flags?.customer_matched && (
                      <div className="unverified-sender-banner" style={{ marginBottom: 8 }}>
                        <span className="unverified-sender-icon">⚠</span>
                        <div>
                          <strong>Unverified sender</strong>
                          <div className="unverified-sender-detail">
                            {selectedCase.intake_flags.customer_validation_status === "no_identifiers"
                              ? "No account identifiers found. Sender could not be matched to any Docket customer."
                              : "Identifiers extracted but no matching customer record found in Docket database."}
                          </div>
                        </div>
                      </div>
                    )}

                    {/* ── Smart intake flags ── */}
                    {selectedCase?.case_id === activeCaseId && selectedCase?.intake_flags && (
                      <SmartIntakePanel flags={selectedCase.intake_flags as IntakeFlags} />
                    )}

                    {/* ── Extracted entities ── */}
                    {selectedCase?.case_id === activeCaseId && selectedCase?.ai_analysis?.extracted_entities && (
                      <ExtractedEntitiesPanel entities={selectedCase.ai_analysis.extracted_entities} />
                    )}

                    {/* ── Key info grid ── */}
                    {selectedCase?.case_id === activeCaseId && (() => {
                      const ai = selectedCase.ai_analysis || {};
                      const fields = selectedCase.extracted_fields || {};
                      return (
                        <div className="triage-info-grid">
                          <div className="triage-info-item"><span>Classification</span><strong>{selectedCase.classification}</strong></div>
                          <div className="triage-info-item"><span>Confidence</span><strong>{Math.round(selectedCase.confidence_score * 100)}%</strong></div>
                          <div className="triage-info-item"><span>Risk Score</span><strong>{Math.round(selectedCase.risk_score * 100)}%</strong></div>
                          <div className="triage-info-item"><span>AI Dept</span><strong>{selectedCase.primary_department}</strong></div>
                          {fields.amount_involved && <div className="triage-info-item"><span>Amount</span><strong>₹{Number(fields.amount_involved).toLocaleString("en-IN")}</strong></div>}
                          {fields.transaction_type && <div className="triage-info-item"><span>Txn Type</span><strong>{fields.transaction_type}</strong></div>}
                          {ai.sentiment && (
                            <div className="triage-info-item" style={{ gridColumn: "1 / -1" }}>
                              <span>Sentiment</span>
                              <strong><SentimentDisplay sentiment={ai.sentiment} tone={ai.emotional_tone} distress={ai.distress_level} intensity={ai.sentiment_intensity} /></strong>
                            </div>
                          )}
                        </div>
                      );
                    })()}

                    {/* ── AI adjudication ── */}
                    {selectedCase?.case_id === activeCaseId && (() => {
                      const ai = selectedCase.ai_analysis || {};
                      return ai.bert_classification ? (
                        <div className="triage-adjud-row">
                          <AdjudBadge label="BERT" cls={ai.bert_classification} conf={ai.bert_confidence} />
                          <ArrowRight size={12} style={{ color: "var(--muted)", flexShrink: 0 }} />
                          <AdjudBadge label="Gemma4" cls={ai.gemma_classification} conf={ai.gemma_confidence} />
                          <ArrowRight size={12} style={{ color: "var(--muted)", flexShrink: 0 }} />
                          <span className={`adjud-mode adjud-${ai.adjudication_mode ?? "unknown"}`} style={{ fontSize: 9 }}>
                            {ai.adjudication_mode ?? "—"}
                          </span>
                        </div>
                      ) : null;
                    })()}

                    {/* ── Customer DB match ── */}
                    <div className="triage-section-label">Customer Database Match</div>
                    {customerLoading && <div style={{ fontSize: 11, color: "var(--muted)", padding: "4px 0" }}>Looking up customer…</div>}
                    {!customerLoading && customerData && !customerData.error && (
                      <CustomerCard
                        data={customerData}
                        onRevalidate={() => {
                          setCustomerLoading(true);
                          api.customerLookup(activeCaseId!).then(setCustomerData).catch(() => setCustomerData({ error: "Lookup failed." })).finally(() => setCustomerLoading(false));
                        }}
                        loading={customerLoading}
                      />
                    )}
                    {!customerLoading && customerData?.error && (
                      <div style={{ fontSize: 11, color: "var(--danger)", marginBottom: 8 }}>{customerData.error}</div>
                    )}

                    {/* ── Secondary tags / fraud indicators ── */}
                    {selectedCase?.case_id === activeCaseId && (() => {
                      const tags: string[] = selectedCase.ai_analysis?.raw_triage?.secondary_tags ?? [];
                      return tags.length > 0 ? (
                        <div className="tag-row" style={{ marginBottom: 8 }}>
                          {tags.map((t: string) => <span key={t} className="finding-tag tag-warn">{t}</span>)}
                        </div>
                      ) : null;
                    })()}

                    {/* ── Raw email (collapsible) ── */}
                    {selectedCase?.case_id === activeCaseId && selectedCase?.normalized_text && (
                      <div style={{ marginBottom: 10 }}>
                        <button
                          style={{ fontSize: 10, padding: "2px 8px", marginBottom: 4 }}
                          onClick={() => setShowRawEmail(p => !p)}
                        >
                          {showRawEmail ? "Hide" : "Show"} email text
                        </button>
                        {showRawEmail && (
                          <pre style={{ fontSize: 10, background: "var(--surface)", border: "1px solid var(--line)", borderRadius: "var(--r)", padding: 8, maxHeight: 200, overflow: "auto", whiteSpace: "pre-wrap", wordBreak: "break-word", color: "var(--ink-mid)" }}>
                            {selectedCase.normalized_text}
                          </pre>
                        )}
                      </div>
                    )}

                    <div className="triage-divider" />
                    <div style={{ marginBottom: 8 }}>
                      <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 6 }}>
                        Target Departments <span style={{ color: "var(--danger)" }}>*</span>
                        <span style={{ fontSize: 9.5, fontWeight: 400, color: "var(--muted)", marginLeft: 6 }}>
                          First checked = primary · check multiple for multi-dept dispatch
                        </span>
                      </div>
                      <div className="triage-dept-checkboxes">
                        {ALL_TRIAGE_DESKS.map(d => {
                          const checked = selectedDepts.includes(d.label);
                          const idx = selectedDepts.indexOf(d.label);
                          return (
                            <label key={d.slug} className={`triage-dept-checkbox ${checked ? "checked" : ""}`}>
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => toggleDept(d.label)}
                              />
                              <span className="triage-dept-name">{d.label}</span>
                              {checked && (
                                <span className="triage-dept-order">
                                  {idx === 0 ? "primary" : `+${idx}`}
                                </span>
                              )}
                            </label>
                          );
                        })}
                      </div>
                    </div>
                    <button
                      className="primary"
                      disabled={busy || selectedDepts.length === 0}
                      onClick={async () => {
                        await onRelease(activeCase.case_id, selectedDepts, undefined, undefined);
                        setActiveCaseId(null);
                        setSelectedDepts([]);
                        setEditedComplaint("");
                      }}
                    >
                      <CheckCheck size={14} />
                      {selectedDepts.length === 0
                        ? "Select department(s) to release"
                        : selectedDepts.length === 1
                        ? `Release to ${selectedDepts[0]}`
                        : `Release to ${selectedDepts.length} departments`}
                    </button>

                    {/* Complaint editor */}
                    {selectedCase != null && selectedCase.case_id === activeCase.case_id && (
                      <div className="subsection" style={{ marginTop: 12 }}>
                        <h3 style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
                          <Wrench size={12} /> Edit Complaint Text
                          {complaintSaved && <span style={{ fontSize: 10, color: "var(--success)", marginLeft: 4 }}>✓ Saved</span>}
                        </h3>
                        <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 4 }}>
                          Clean up typos or formatting before releasing. Original is preserved in the audit trail.
                        </div>
                        <textarea
                          value={editedComplaint}
                          onChange={e => { setEditedComplaint(e.target.value); setComplaintSaved(false); }}
                          style={{ width: "100%", minHeight: 120, fontSize: 11, fontFamily: "monospace", resize: "vertical" }}
                          placeholder="Email complaint text…"
                        />
                        <button
                          style={{ fontSize: 10, padding: "3px 10px", marginTop: 4 }}
                          onClick={saveComplaint}
                          disabled={complaintSaving || !editedComplaint.trim()}
                        >
                          {complaintSaving ? "Saving…" : "Save Edits"}
                        </button>
                      </div>
                    )}

                    {/* ── Customer history + similar cases ── */}
                    <CustomerHistoryPanel caseId={activeCase.case_id} />
                    {selectedCase?.case_id === activeCaseId && (
                      <SimilarCasesInsightPanel
                        solutions={selectedCase.suggested_solutions ?? []}
                        synthesis={selectedCase.solutions_synthesis}
                      />
                    )}
                  </>
                )}
            </div>
            </Panel>
          </PanelGroup>
        )
      }
    </div>
  );
}

// ─── Error Queue View ─────────────────────────────────────────────────────────
function ErrorQueueView(props: { errors: any[]; onDismiss: (id: string) => Promise<void>; refresh: () => void }) {
  const { errors, onDismiss } = props;
  return (
    <section className="panel full">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", paddingBottom: 10, borderBottom: "1px solid var(--line)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <AlertOctagon size={17} style={{ color: "var(--danger)" }} />
          <h2 style={{ margin: 0, fontSize: 13, fontWeight: 700 }}>Error Queue — Failed Intakes</h2>
        </div>
        <button onClick={props.refresh}><RefreshCcw size={13} /> Refresh</button>
      </div>
      {!errors.length
        ? <div className="empty" style={{ color: "var(--success)" }}>No unresolved intake errors. Everything is processing correctly.</div>
        : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, overflowY: "auto", flex: 1 }}>
            {errors.map(err => (
              <div key={err.error_id} className="error-row">
                <div className="error-row-meta">
                  <span className="error-id">{err.error_id}</span>
                  <span className="error-type">{err.error_type}</span>
                  <span className="error-file">{err.source_filename}</span>
                  <small style={{ color: "var(--very-muted)", fontSize: 10, marginLeft: "auto" }}>
                    {err.created_at ? new Date(err.created_at).toLocaleString("en-IN") : ""}
                  </small>
                  <button className="danger" style={{ padding: "3px 8px", fontSize: 10 }} onClick={() => onDismiss(err.error_id)}>
                    <XCircle size={11} /> Dismiss
                  </button>
                </div>
                {err.raw_email_preview && (
                  <div className="error-preview">{err.raw_email_preview.slice(0, 200)}</div>
                )}
                <details>
                  <summary style={{ fontSize: 10.5, color: "var(--muted)", cursor: "pointer" }}>Show error detail</summary>
                  <div className="error-detail">{err.error_detail}</div>
                </details>
              </div>
            ))}
          </div>
        )
      }
    </section>
  );
}

// ─── SLA View ─────────────────────────────────────────────────────────────────
function SlaView({ alerts, workloads }: { alerts: any[]; workloads: any[] }) {
  const [open, setOpen] = useState<any | null>(null);
  const overdue = alerts.filter(a => a.severity === "OVERDUE");
  const nearing = alerts.filter(a => a.severity === "NEARING_BREACH");

  function fmtDue(iso: string) {
    try { return new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }); }
    catch { return iso; }
  }
  function fmtLeft(v: number) {
    const abs = Math.abs(v);
    const h = Math.floor(abs / 60); const m = abs % 60;
    const str = h > 0 ? `${h}h ${m}m` : `${m}m`;
    return v < 0 ? `−${str} overdue` : str;
  }

  return (
    <>
      {/* SLA detail popup */}
      {open && (
        <div className="solution-overlay" onClick={() => setOpen(null)}>
          <div className="solution-popup sla-popup" onClick={e => e.stopPropagation()}>
            <div className="solution-popup-head">
              <Clock size={15} />
              <span>{open.case_id} — SLA Detail</span>
              <button className="solution-popup-close" onClick={() => setOpen(null)}>✕</button>
            </div>

            <div className="sla-popup-grid">
              <div className="sla-popup-row">
                <span className="sla-popup-label">Status</span>
                <span className={`sla-badge sla-${(open.severity ?? "").toLowerCase()}`}>{open.severity}</span>
              </div>
              <div className="sla-popup-row">
                <span className="sla-popup-label">SLA Type</span>
                <span className="sla-popup-val">{open.sla_type?.replace(/_/g, " ")}</span>
              </div>
              <div className="sla-popup-row">
                <span className="sla-popup-label">Priority</span>
                <span className={priorityClass(open.sla_profile)}>{open.sla_profile}</span>
              </div>
              <div className="sla-popup-row">
                <span className="sla-popup-label">Deadline</span>
                <span className="sla-popup-val">{fmtDue(open.due_at)}</span>
              </div>
              <div className="sla-popup-row">
                <span className="sla-popup-label">Time Left</span>
                <span className={`sla-popup-val ${open.minutes_left < 0 ? "sla-overdue-val" : open.minutes_left < 120 ? "sla-warn-val" : ""}`}>
                  {fmtLeft(open.minutes_left)}
                </span>
              </div>
              <div className="sla-popup-row">
                <span className="sla-popup-label">Classification</span>
                <span className="sla-popup-val">{open.classification ?? "—"}</span>
              </div>
              <div className="sla-popup-row">
                <span className="sla-popup-label">Workflow State</span>
                <span className={`state-badge state-${(open.workflow_state ?? "").toLowerCase().replace(/_/g, "-")}`}>
                  {(open.workflow_state ?? "—").replace(/_/g, " ")}
                </span>
              </div>
              <div className="sla-popup-row">
                <span className="sla-popup-label">Handling Desk</span>
                <span className="sla-popup-val">{open.department ?? "—"}</span>
              </div>
              {open.assigned_operator && (
                <div className="sla-popup-row">
                  <span className="sla-popup-label">Assigned To</span>
                  <span className="sla-popup-val">{open.assigned_operator}</span>
                </div>
              )}
              {open.customer && (
                <div className="sla-popup-row">
                  <span className="sla-popup-label">Customer</span>
                  <span className="sla-popup-val">{open.customer}</span>
                </div>
              )}
              {open.amount_involved && (
                <div className="sla-popup-row">
                  <span className="sla-popup-label">Amount</span>
                  <span className="sla-popup-val">₹{Number(open.amount_involved).toLocaleString("en-IN")}</span>
                </div>
              )}
            </div>

            {open.summary && (
              <div className="solution-popup-resolution">
                <div className="solution-popup-label">Case Summary</div>
                <div className="sla-popup-summary">{open.summary}</div>
              </div>
            )}
          </div>
        </div>
      )}

      <PanelGroup orientation="horizontal" style={{ overflow: "hidden" }}>
      <Panel defaultSize={50} minSize={20}>
      <section className="panel" style={{ height: "100%" }}>
        <PanelTitle title="SLA Monitoring Board" icon={<Clock size={17} />} />
        <div className="sla-legend">
          <span className="sla-dot overdue" /> Overdue: <strong>{overdue.length}</strong>
          <span className="sla-dot nearing" style={{ marginLeft: 8 }} /> Nearing: <strong>{nearing.length}</strong>
          <small style={{ marginLeft: "auto", color: "var(--faint)" }}>Click any row for details</small>
        </div>

        {!alerts.length
          ? <div className="empty">No SLA alerts — all cases are within their deadlines.</div>
          : (
            <div className="sla-table">
              <div className="sla-thead">
                <span>Case</span>
                <span>What</span>
                <span>Who handles</span>
                <span>SLA type</span>
                <span>Deadline</span>
                <span>Time left</span>
              </div>
              {alerts.map((a, i) => (
                <button
                  key={i}
                  className={`sla-trow ${a.severity === "OVERDUE" ? "sla-trow-overdue" : "sla-trow-nearing"}`}
                  onClick={() => setOpen(a)}
                  title={a.summary ?? a.case_id}
                >
                  <span className="sla-cell-id">{a.case_id}</span>
                  <span className="sla-cell-what">
                    {a.summary
                      ? <>{a.summary}</>
                      : <em style={{ color: "var(--faint)" }}>{a.classification ?? "—"}</em>
                    }
                  </span>
                  <span className="sla-cell-dept">{a.department}</span>
                  <span className="sla-cell-type">{(a.sla_type ?? "").replace(/_/g, " ")}</span>
                  <span className="sla-cell-due">{fmtDue(a.due_at)}</span>
                  <span className={`sla-cell-left ${a.minutes_left < 0 ? "sla-overdue-val" : a.minutes_left < 120 ? "sla-warn-val" : ""}`}>
                    {fmtLeft(a.minutes_left)}
                  </span>
                </button>
              ))}
            </div>
          )
        }
      </section>
      </Panel>
      <PanelResizeHandle className="resize-handle-v" />
      <Panel defaultSize={50} minSize={20}>
      <section className="panel" style={{ height: "100%" }}>
        <PanelTitle title="Department Workloads" icon={<GitService area size={17} />} />
        <DataTable
          rows={workloads}
          columns={[
            { key: "department", label: "Department" },
            { key: "total", label: "Total Cases" },
          ]}
        />
      </section>
      </Panel>
      </PanelGroup>
    </>
  );
}

// ─── Incident View ────────────────────────────────────────────────────────────
function IncidentView({ incidents }: { incidents: any[] }) {
  return (
    <section className="panel full">
      <PanelTitle title="Incident Monitoring Dashboard" icon={<ShieldAlert size={17} />} />
      <DataTable
        rows={incidents}
        columns={[
          { key: "incident_id", label: "Incident ID" },
          { key: "title", label: "Title" },
          { key: "status", label: "Status", render: v => <span className={`state-badge state-${(v as string).toLowerCase()}`}>{v as string}</span> },
          { key: "pattern_key", label: "Pattern" },
          { key: "case_count", label: "Cases", render: (_, row) => row.case_ids?.length ?? 0 },
        ]}
      />
    </section>
  );
}

// ─── History View ─────────────────────────────────────────────────────────────
function HistoryView(props: {
  query: string;
  setQuery: (v: string) => void;
  results: any[];
  search: (mode: "keyword" | "semantic") => Promise<void>;
  loadCase: (id: string) => void;
  selectedCase: any;
  onContinuation: (caseId: string, note: string) => void;
}) {
  const [mode, setMode] = useState<"keyword" | "semantic">("keyword");
  const [continuationTarget, setContinuationTarget] = useState<string | null>(null);
  const [continuationNote, setContinuationNote] = useState("");
  const [searching, setSearching] = useState(false);

  async function doSearch() {
    if (!props.query.trim() || searching) return;
    setSearching(true);
    try { await props.search(mode); } finally { setSearching(false); }
  }

  return (
    <div className="hv-layout">
      {/* ── Left: search panel ── */}
      <div className="hv-left">
        <PanelTitle title="Case Search" icon={<Search size={17} />} />

        {/* Mode toggle */}
        <div className="hv-mode-row">
          <button className={`hv-mode-btn ${mode === "keyword" ? "hv-mode-active" : ""}`} onClick={() => setMode("keyword")}>
            Keyword / customer reference / Name
          </button>
          <button className={`hv-mode-btn ${mode === "semantic" ? "hv-mode-active" : ""}`} onClick={() => setMode("semantic")}>
            Semantic (AI)
          </button>
        </div>

        <div className="search-row" style={{ marginBottom: 8 }}>
          <input
            value={props.query}
            onChange={e => props.setQuery(e.target.value)}
            onKeyDown={e => e.key === "Enter" && doSearch()}
            placeholder={mode === "keyword"
              ? "Enter name, customer reference, email, case ID or keyword…"
              : "Describe the issue — AI finds similar past cases…"}
          />
          <button className="primary" onClick={doSearch} disabled={searching}>
            {searching ? <RefreshCcw size={14} className="spin" /> : <Search size={14} />}
          </button>
        </div>

        {mode === "keyword" && (
          <p className="hv-hint">Searches all cases including resolved and closed. Matches customer name, customer reference, email, case ID, classification.</p>
        )}
        {mode === "semantic" && (
          <p className="hv-hint">{searching ? "Searching — AI semantic search can take up to 60s…" : "Describe the issue — AI finds similar past cases by issue description."}</p>
        )}

        {/* Results list */}
        <div className="hv-results">
          {props.results.length === 0 && (
            <div className="hv-empty">No results yet — run a search above.</div>
          )}
          {props.results.map((r: any) => {
            const isSettled = r.workflow_state === "RESOLVED" || r.workflow_state === "CLOSED";
            const isActive = props.selectedCase?.case_id === r.case_id;
            return (
              <div key={r.case_id} className={`hv-row ${isActive ? "hv-row-active" : ""}`}>
                <div className="hv-row-top">
                  <span className="hv-case-id">{r.case_id}</span>
                  <span className={`hv-state ${isSettled ? "hv-state-settled" : "hv-state-active"}`}>
                    {(r.workflow_state ?? "").replace(/_/g, " ")}
                  </span>
                  {r.similarity !== undefined && (
                    <span className="hv-sim">{Math.round(Number(r.similarity) * 100)}%</span>
                  )}
                </div>
                <div className="hv-row-mid">
                  <span className="hv-cls">{r.classification}</span>
                  <span className="hv-dept">{r.primary_department}</span>
                </div>
                {r.summary && <div className="hv-row-summary">{r.summary}</div>}
                <div className="hv-row-actions">
                  <button className="hv-btn-view" onClick={() => props.loadCase(r.case_id)}>
                    Open
                  </button>
                  {isSettled && continuationTarget !== r.case_id && (
                    <button className="hv-btn-part2" onClick={() => { setContinuationTarget(r.case_id); setContinuationNote(""); }}>
                      ↩ Reopen as New
                    </button>
                  )}
                </div>
                {continuationTarget === r.case_id && (
                  <div className="hv-continuation-form">
                    <div className="hv-reopen-hint">A fresh case will be created carrying all context (timeline, communications, solutions, customer info) from this one as reference.</div>
                    <input
                      className="hv-cont-input"
                      placeholder="Why is this being reopened? (new complaint, unresolved issue…)"
                      value={continuationNote}
                      onChange={e => setContinuationNote(e.target.value)}
                      autoFocus
                    />
                    <div className="hv-cont-actions">
                      <button className="hv-btn-confirm" onClick={() => {
                        props.onContinuation(r.case_id, continuationNote);
                        setContinuationTarget(null);
                      }}>Reopen</button>
                      <button className="hv-btn-cancel" onClick={() => setContinuationTarget(null)}>Cancel</button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Right: case detail ── */}
      <div className="hv-right">
        {props.selectedCase ? (
          <CentralCaseDetail c={props.selectedCase} loadCase={props.loadCase} />
        ) : (
          <div className="hv-no-case">
            <Search size={28} style={{ opacity: 0.2 }} />
            <span>Select a case from the results to view details</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Audit View ───────────────────────────────────────────────────────────────
// ─── Repeat Detection Sub-panel ───────────────────────────────────────────────
// ─── Regulatory Breach Sub-panel ─────────────────────────────────────────────
function RegulatoryBreachPanel({ rb }: { rb: RegulatoryBreach }) {
  if (!rb?.detected) return null;
  const isHighSev = rb.severity === "CRITICAL" || rb.severity === "HIGH";
  const color = isHighSev ? "var(--danger)" : "var(--warning)";
  const bg = isHighSev ? "var(--danger-bg)" : "var(--warning-bg)";
  const border = isHighSev ? "var(--danger-border)" : "var(--warning-border)";
  const slaText = rb.compliance_sla_hours
    ? rb.compliance_sla_hours >= 168
      ? `${Math.round(rb.compliance_sla_hours / 168)} weeks`
      : rb.compliance_sla_hours >= 24
      ? `${Math.round(rb.compliance_sla_hours / 24)} days`
      : `${rb.compliance_sla_hours} hours`
    : null;

  return (
    <div style={{ marginTop: 10, background: bg, border: `1px solid ${border}`, borderRadius: 8, padding: "8px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
        <Scale size={12} style={{ color }} />
        <span style={{ fontWeight: 700, fontSize: 11, color }}>
          {rb.breach_type.replace(/_/g, " ")}
        </span>
        <span style={{ padding: "1px 6px", borderRadius: 4, fontSize: 9, fontWeight: 700, background: bg, color }}>
          {rb.severity}
        </span>
        {rb.urgency_boosted && (
          <span style={{ padding: "1px 6px", borderRadius: 4, fontSize: 9, background: "var(--danger-bg)", color: "var(--danger)", fontWeight: 700 }}>
            URGENT LANGUAGE
          </span>
        )}
        {slaText && (
          <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--faint)" }}>
            SLA: {slaText}
          </span>
        )}
      </div>
      {rb.details && (
        <div style={{ fontSize: 11, color: "var(--ink-mid)", marginBottom: 6, lineHeight: 1.5 }}>
          {rb.details}
        </div>
      )}
      <div style={{ fontSize: 10, color: "var(--muted)", display: "flex", gap: 4, alignItems: "flex-start" }}>
        <span style={{ color, marginTop: 1 }}>→</span>
        <span>{rb.escalation_path}</span>
      </div>
    </div>
  );
}

// ─── Attachment Extraction Sub-panel ──────────────────────────────────────────
function AttachmentPanel({ atts, entities }: {
  atts: AttachmentExtracted[];
  entities?: IntakeFlags["attachment_entities"];
}) {
  const [open, setOpen] = useState(false);
  if (!atts || atts.length === 0) return null;
  const extracted = atts.filter(a => a.char_count > 0);
  const total = atts.length;
  const hasEntities = entities && Object.values(entities).some(v => v.length > 0);

  return (
    <div style={{ marginTop: 10, background: "var(--success-bg)", border: "1px solid var(--success-border)", borderRadius: 8, padding: "8px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
        <FileText size={12} style={{ color: "var(--success)" }} />
        <span style={{ fontWeight: 700, fontSize: 11, color: "var(--success)" }}>
          Attachment Extraction
        </span>
        <span style={{ fontSize: 10, color: "var(--faint)" }}>
          {extracted.length}/{total} yielded text
        </span>
        <button
          style={{ marginLeft: "auto", fontSize: 10, background: "none", border: "none", color: "var(--brand)", cursor: "pointer", padding: 0, display: "flex", alignItems: "center", gap: 3 }}
          onClick={() => setOpen(s => !s)}
        >
          <ChevronDown size={10} style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform .15s" }} />
          {open ? "Collapse" : "Expand"}
        </button>
      </div>

      {/* Merged entity chips */}
      {hasEntities && (
        <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginBottom: open ? 8 : 0 }}>
          {(entities!.amounts || []).slice(0, 4).map((v, i) => (
            <span key={i} style={{ fontSize: 10, padding: "1px 6px", borderRadius: 4, background: "var(--success-bg)", color: "var(--success)", fontWeight: 600 }}>₹{v}</span>
          ))}
          {(entities!.reference_ids || []).slice(0, 3).map((v, i) => (
            <span key={i} style={{ fontSize: 10, padding: "1px 6px", borderRadius: 4, background: "var(--info-bg)", color: "var(--info)" }}>Ref: {v}</span>
          ))}
          {(entities!.dates || []).slice(0, 2).map((v, i) => (
            <span key={i} style={{ fontSize: 10, padding: "1px 6px", borderRadius: 4, background: "var(--surface-3)", color: "var(--muted)" }}>{v}</span>
          ))}
          {(entities!.error_codes || []).slice(0, 2).map((v, i) => (
            <span key={i} style={{ fontSize: 10, padding: "1px 6px", borderRadius: 4, background: "var(--danger-bg)", color: "var(--danger)", fontWeight: 600 }}>Err: {v}</span>
          ))}
        </div>
      )}

      {open && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {atts.map((a, i) => (
            <div key={i} style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 6, padding: "5px 8px", fontSize: 10 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <span style={{ color: "var(--ink)", fontWeight: 600 }}>{a.filename}</span>
                <span style={{ color: "var(--faint)" }}>{a.content_type}</span>
                <span style={{
                  padding: "1px 5px", borderRadius: 4, fontSize: 9,
                  background: a.char_count > 0 ? "var(--success-bg)" : "var(--surface-3)",
                  color: a.char_count > 0 ? "var(--success)" : "var(--muted)",
                }}>
                  {a.extraction_method} · {a.char_count} chars
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Auto Draft Sub-panel ─────────────────────────────────────────────────────
function AutoDraftPanel({ draft }: { draft: AutoDraft }) {
  const [showBody, setShowBody] = useState(false);
  if (!draft?.subject) return null;
  const isLlm = draft.source === "llm";
  const ctx = draft.context_injected || {};

  return (
    <div style={{ marginTop: 10, background: "var(--compliance-bg)", border: "1px solid #d8b4fe", borderRadius: 8, padding: "8px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
        <Send size={12} style={{ color: "var(--compliance)" }} />
        <span style={{ fontWeight: 700, fontSize: 11, color: "var(--compliance)" }}>
          Auto-Generated Response Draft
        </span>
        <span style={{ fontSize: 9, padding: "1px 5px", borderRadius: 4, background: isLlm ? "var(--compliance-bg)" : "var(--surface-3)", color: isLlm ? "var(--compliance)" : "var(--muted)", fontWeight: 600 }}>
          {isLlm ? "AI" : "Template"}
        </span>
        {ctx.has_regulatory && (
          <span style={{ fontSize: 9, padding: "1px 5px", borderRadius: 4, background: "var(--danger-bg)", color: "var(--danger)", fontWeight: 700 }}>Regulatory</span>
        )}
        {ctx.customer_segment && ctx.customer_segment !== "RETAIL" && (
          <span style={{ fontSize: 9, padding: "1px 5px", borderRadius: 4, background: "var(--warning-bg)", color: "var(--warning)", fontWeight: 600 }}>{ctx.customer_segment}</span>
        )}
      </div>
      <div style={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 6, padding: "6px 10px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontSize: 11, fontWeight: 600, color: "var(--ink)" }}>{draft.subject}</span>
          <button
            style={{ fontSize: 10, background: "none", border: "none", color: "var(--brand)", cursor: "pointer", padding: 0 }}
            onClick={() => setShowBody(s => !s)}
          >
            {showBody ? "Collapse" : "Preview"}
          </button>
        </div>
        {showBody && (
          <pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit", fontSize: 10, color: "var(--muted)", margin: "6px 0 0", lineHeight: 1.6 }}>
            {draft.body}
          </pre>
        )}
      </div>
      {(ctx.amount || (ctx.reference_ids || []).length > 0) && (
        <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 5 }}>
          {ctx.amount && <span style={{ fontSize: 9, padding: "1px 5px", borderRadius: 4, background: "var(--success-bg)", color: "var(--success)", fontWeight: 600 }}>₹{ctx.amount} injected</span>}
          {(ctx.reference_ids || []).slice(0, 2).map((r: string, i: number) => (
            <span key={i} style={{ fontSize: 9, padding: "1px 5px", borderRadius: 4, background: "var(--info-bg)", color: "var(--info)" }}>Ref {r}</span>
          ))}
        </div>
      )}
    </div>
  );
}

function RepeatDetectionPanel({ rd, loadCase }: { rd: RepeatDetection; loadCase?: (id: string) => void }) {
  const [showPrior, setShowPrior] = useState(false);
  const isRepeat = rd.repeat_type === "exact_repeat";
  const isNewIssue = rd.repeat_type === "new_issue";

  if (rd.repeat_type === "first_contact" && rd.prior_cases.length === 0) return null;

  const accentColor = isRepeat ? "var(--danger)" : "var(--warning)";
  const bgColor = isRepeat ? "var(--danger-bg)" : "var(--warning-bg)";
  const borderColor = isRepeat ? "var(--danger-border)" : "var(--warning-border)";

  return (
    <div style={{ marginTop: 10, background: bgColor, border: `1px solid ${borderColor}`, borderRadius: 8, padding: "8px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
        {isRepeat ? <RotateCcw size={12} style={{ color: accentColor }} /> : <History size={12} style={{ color: accentColor }} />}
        <span style={{ fontWeight: 700, fontSize: 11, color: accentColor }}>
          {isRepeat ? "Exact Repeat Submission" : "Known Customer — New Issue"}
        </span>
        <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--faint)" }}>
          {rd.open_count} open · {rd.resolved_count} resolved
        </span>
      </div>

      {isRepeat && rd.same_complaint_case && (
        <div style={{ fontSize: 11, color: "var(--ink-mid)", marginBottom: 6 }}>
          Near-identical to <span style={{ fontFamily: "monospace", color: accentColor }}>{rd.same_complaint_case}</span>
          {rd.top_similarity != null && ` (${Math.round(rd.top_similarity * 100)}% match)`}
          . Check if that case is unresolved or the customer was unsatisfied with the outcome.
        </div>
      )}

      {isNewIssue && rd.prior_cases.length > 0 && (
        <div style={{ fontSize: 11, color: "var(--ink-mid)", marginBottom: 6 }}>
          {rd.open_count > 0
            ? `Customer has ${rd.open_count} open case(s) on record — context pre-loaded.`
            : `Returning customer with ${rd.resolved_count} resolved case(s) on record.`}
        </div>
      )}

      {rd.prior_cases.length > 0 && (
        <>
          <button
            style={{ fontSize: 10, background: "none", border: "none", color: "var(--brand)", cursor: "pointer", padding: 0, display: "flex", alignItems: "center", gap: 4 }}
            onClick={(e) => { e.stopPropagation(); setShowPrior(s => !s); }}
          >
            <History size={10} />
            {showPrior ? "Hide" : "Show"} prior cases ({rd.prior_cases.length})
            <ChevronDown size={10} style={{ transform: showPrior ? "rotate(180deg)" : "none", transition: "transform .15s" }} />
          </button>
          {showPrior && (
            <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 4 }}>
              {rd.prior_cases.map((pc) => (
                <div
                  key={pc.case_id}
                  onClick={(e) => { e.stopPropagation(); loadCase?.(pc.case_id); }}
                  style={{
                    background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 6,
                    padding: "5px 8px", fontSize: 10, display: "flex", gap: 10, alignItems: "center",
                    cursor: loadCase ? "pointer" : "default", transition: "background .12s",
                  }}
                  onMouseEnter={e => { if (loadCase) e.currentTarget.style.background = "var(--surface-2)"; }}
                  onMouseLeave={e => { e.currentTarget.style.background = "var(--surface)"; }}
                >
                  <span style={{ fontFamily: "monospace", color: "var(--brand)", fontWeight: 700 }}>{pc.case_id}</span>
                  <span style={{ color: "var(--ink-mid)" }}>{pc.classification}</span>
                  <span style={{
                    padding: "1px 5px", borderRadius: 4, fontSize: 9, fontWeight: 700,
                    background: pc.workflow_state === "CLOSED" || pc.workflow_state === "RESOLVED" ? "var(--success-bg)" : "var(--warning-bg)",
                    color: pc.workflow_state === "CLOSED" || pc.workflow_state === "RESOLVED" ? "var(--success)" : "var(--warning)",
                  }}>
                    {pc.workflow_state}
                  </span>
                  {pc.similarity != null && (
                    <span style={{ marginLeft: "auto", color: "var(--faint)" }}>{Math.round(pc.similarity * 100)}% similar</span>
                  )}
                  {loadCase && <span style={{ color: "var(--brand-muted)", fontSize: 9 }}>→ open</span>}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ─── Clarification Request Sub-panel ─────────────────────────────────────────
function ClarificationPanel({ cl }: { cl: ClarificationDraft }) {
  const [showBody, setShowBody] = useState(false);
  if (!cl?.needed) return null;

  return (
    <div style={{ marginTop: 10, background: "var(--info-bg)", border: "1px solid var(--info-border)", borderRadius: 8, padding: "8px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
        <Mail size={12} style={{ color: "var(--info)" }} />
        <span style={{ fontWeight: 700, fontSize: 11, color: "var(--info)" }}>Clarification Required</span>
      </div>

      <div style={{ fontSize: 11, color: "var(--ink-mid)", marginBottom: 6 }}>{cl.reason}</div>

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
        {(cl.missing_fields ?? []).map(f => (
          <span key={f} style={{ fontSize: 10, padding: "2px 7px", borderRadius: 4, background: "var(--danger-bg)", color: "var(--danger)", fontWeight: 600 }}>
            Missing: {f.replace(/_/g, " ")}
          </span>
        ))}
        {(cl.conflict_fields ?? []).map(f => (
          <span key={f} style={{ fontSize: 10, padding: "2px 7px", borderRadius: 4, background: "var(--warning-bg)", color: "var(--warning)", fontWeight: 600 }}>
            Conflict: {f.replace(/_/g, " ")}
          </span>
        ))}
      </div>

      {cl.subject && (
        <div style={{ background: "var(--surface-raised)", borderRadius: 6, padding: "7px 10px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
            <div style={{ fontWeight: 600, fontSize: 11, color: "var(--text-primary)" }}>
              Draft: {cl.subject}
            </div>
            <button
              style={{ fontSize: 10, background: "none", border: "none", color: "var(--brand)", cursor: "pointer", padding: 0 }}
              onClick={() => setShowBody(s => !s)}
            >
              {showBody ? "Hide" : "Preview"} email
            </button>
          </div>
          {showBody && cl.body && (
            <pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit", fontSize: 10, color: "var(--text-secondary)", margin: 0, lineHeight: 1.6 }}>
              {cl.body}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Impersonation Risk Sub-panel ─────────────────────────────────────────────
function ImpersonationRiskPanel({ risk }: { risk: ImpersonationRisk }) {
  const [showSignals, setShowSignals] = useState(false);
  if (!risk || !risk.level || risk.level === "NONE" || risk.level === "UNKNOWN") return null;

  const isHigh = risk.level === "HIGH" || risk.level === "CRITICAL";
  const isMed = risk.level === "MEDIUM";
  const color = isHigh ? "var(--danger)" : isMed ? "var(--warning)" : "var(--warning)";
  const bg = isHigh ? "var(--danger-bg)" : "var(--warning-bg)";
  const border = isHigh ? "var(--danger-border)" : "var(--warning-border)";

  return (
    <div style={{ marginTop: 10, background: bg, border: `1px solid ${border}`, borderRadius: 8, padding: "8px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
        <ShieldAlert size={12} style={{ color }} />
        <span style={{ fontWeight: 700, fontSize: 11, color }}>
          Impersonation Risk: {risk.level}
        </span>
        <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--faint)" }}>
          score {risk.score}/100
        </span>
      </div>
      <div style={{ fontSize: 11, color: "var(--ink-mid)", marginBottom: 6 }}>{risk.recommendation}</div>
      {(risk.signals?.length ?? 0) > 0 && (
        <>
          <button
            style={{ fontSize: 10, background: "none", border: "none", color: "var(--brand)", cursor: "pointer", padding: 0, display: "flex", alignItems: "center", gap: 4 }}
            onClick={() => setShowSignals(s => !s)}
          >
            <InfoIcon size={10} />
            {showSignals ? "Hide" : "Show"} signals ({risk.signals.length})
            <ChevronDown size={10} style={{ transform: showSignals ? "rotate(180deg)" : "none", transition: "transform .15s" }} />
          </button>
          {showSignals && (
            <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 4 }}>
              {(risk.signals ?? []).map((s, i) => (
                <div key={i} style={{ fontSize: 10, color: "var(--ink-mid)", display: "flex", gap: 6, alignItems: "flex-start" }}>
                  <span style={{ color, marginTop: 1 }}>•</span>
                  <span>{s}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ─── Smart Intake Panel ───────────────────────────────────────────────────────
function SmartIntakePanel({ flags, loadCase }: { flags: IntakeFlags; loadCase?: (id: string) => void }) {
  const [showTurns, setShowTurns] = useState(false);
  const [showSolutions, setShowSolutions] = useState(false);

  const hasRepeatInfo = flags?.repeat_detection && flags.repeat_detection.repeat_type !== "first_contact";
  const hasClarification = flags?.clarification?.needed;
  const hasImpersonationRisk = flags?.impersonation_risk &&
    !["NONE", "UNKNOWN"].includes(flags.impersonation_risk.level);
  const hasRegulatory = flags?.regulatory_breach?.detected;
  const hasAttachments = (flags?.attachments_extracted?.length ?? 0) > 0;
  const hasDraft = Boolean(flags?.auto_draft?.subject);

  const isProceed = !flags?.verdict || flags.verdict === "proceed";
  if (isProceed && !hasRepeatInfo && !hasClarification && !hasImpersonationRisk
      && !hasRegulatory && !hasAttachments && !hasDraft) return null;
  if (!flags || !flags.verdict) return null;

  const verdict = flags.verdict;
  const isExternalBank = verdict === "external_bank";
  const isOffTopic = verdict === "off_topic";
  const isSparse = verdict === "sparse";
  const isEmpty = verdict === "empty";
  const isThread = flags.thread_detected;

  return (
    <section className="subsection">
      <h3 style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
        <Microscope size={13} />
        Smart Intake Analysis
        <span style={{
          marginLeft: "auto",
          fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 10,
          background: isExternalBank ? "var(--danger-bg)" : isOffTopic ? "var(--warning-bg)" : isSparse ? "var(--info-bg)" : "var(--surface-3)",
          color: isExternalBank ? "var(--danger)" : isOffTopic ? "var(--warning)" : isSparse ? "var(--info)" : "var(--muted)",
        }}>
          {verdict.replace("_", " ").toUpperCase()}
        </span>
      </h3>

      {/* Non-Docket / Off-topic */}
      {(isExternalBank || isOffTopic) && (
        <div style={{ fontSize: 11, lineHeight: 1.7 }}>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
            {flags.competitor_provider && (
              <span style={{ background: "var(--danger-bg)", color: "var(--danger)", padding: "2px 8px", borderRadius: 6, fontSize: 10, fontWeight: 600 }}>
                Competitor: {flags.competitor_provider}
              </span>
            )}
            {flags.off_topic_domain && (
              <span style={{ background: "var(--warning-bg)", color: "var(--warning)", padding: "2px 8px", borderRadius: 6, fontSize: 10, fontWeight: 600 }}>
                Off-topic: {flags.off_topic_domain}
              </span>
            )}
            {flags.external_confidence !== undefined && (
              <span style={{ background: "var(--surface-3)", color: "var(--muted)", padding: "2px 8px", borderRadius: 6, fontSize: 10 }}>
                Confidence: {Math.round(flags.external_confidence * 100)}%
              </span>
            )}
          </div>
          {flags.external_reason && (
            <div style={{ color: "var(--muted)", marginBottom: 8, fontSize: 11 }}>{flags.external_reason}</div>
          )}
          {flags.redirect_subject && (
            <div style={{ background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: 6, padding: "8px 10px" }}>
              <div style={{ fontWeight: 600, color: "var(--ink)", marginBottom: 4, fontSize: 11 }}>
                Auto-reply sent: {flags.redirect_subject}
              </div>
              <pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit", fontSize: 10, color: "var(--muted)", margin: 0 }}>
                {flags.redirect_body}
              </pre>
            </div>
          )}
        </div>
      )}

      {/* Sparse / Empty */}
      {(isSparse || isEmpty) && (
        <div style={{ fontSize: 11, lineHeight: 1.7 }}>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
            <span style={{ background: "var(--info-bg)", color: "var(--info)", padding: "2px 8px", borderRadius: 6, fontSize: 10, fontWeight: 600 }}>
              {flags.meaningful_word_count ?? 0} meaningful words
            </span>
            <span style={{ background: "var(--surface-3)", color: "var(--muted)", padding: "2px 8px", borderRadius: 6, fontSize: 10 }}>
              {flags.identifier_count ?? 0} identifiers found
            </span>
            {flags.extracted_issue_hint && (
              <span style={{ background: "var(--success-bg)", color: "var(--success)", padding: "2px 8px", borderRadius: 6, fontSize: 10, fontWeight: 600 }}>
                Issue hint: {flags.extracted_issue_hint}
              </span>
            )}
          </div>

          {/* Customer DB match */}
          {flags.sparse_customer_match?.matched_customer && (
            <div style={{ background: "var(--success-bg)", border: "1px solid var(--success-border)", borderRadius: 6, padding: "8px 10px", marginBottom: 8 }}>
              <div style={{ fontWeight: 700, color: "var(--success)", marginBottom: 4, fontSize: 11 }}>
                ✓ Customer matched from database
              </div>
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                {[
                  ["Name", flags.sparse_customer_match.matched_customer.name],
                  ["customer reference", flags.sparse_customer_match.matched_customer.customer_ref],
                  ["Service area", flags.sparse_customer_match.matched_customer.service_area],
                  ["identity verification", flags.sparse_customer_match.matched_customer.verification_status],
                  ["Status", flags.sparse_customer_match.matched_customer.status],
                ].map(([label, value]) => value ? (
                  <div key={label}>
                    <span style={{ fontSize: 10, color: "var(--faint)" }}>{label}: </span>
                    <span style={{ fontSize: 11, color: "var(--ink)", fontWeight: 500 }}>{value}</span>
                  </div>
                ) : null)}
              </div>
              {flags.sparse_customer_match.matched_customer.product_types?.length > 0 && (
                <div style={{ marginTop: 4 }}>
                  <span style={{ fontSize: 10, color: "var(--faint)" }}>Products: </span>
                  {flags.sparse_customer_match.matched_customer.product_types.map((p: string) => (
                    <span key={p} style={{ fontSize: 10, background: "var(--compliance-bg)", color: "var(--compliance)", padding: "1px 6px", borderRadius: 4, marginRight: 4 }}>{p}</span>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Suggested solutions from history */}
          {flags.sparse_solutions && flags.sparse_solutions.length > 0 && (
            <div>
              <button
                style={{ fontSize: 11, background: "none", border: "none", color: "var(--brand)", cursor: "pointer", padding: 0, marginBottom: 6, display: "flex", alignItems: "center", gap: 4 }}
                onClick={() => setShowSolutions(s => !s)}
              >
                <Lightbulb size={12} />
                {flags.sparse_solutions!.length} matched historical solution{flags.sparse_solutions!.length !== 1 ? "s" : ""} found
                <ChevronDown size={11} style={{ transform: showSolutions ? "rotate(180deg)" : "none", transition: "transform .15s" }} />
              </button>
              {showSolutions && (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {flags.sparse_solutions!.map((sol: any, i: number) => (
                    <div key={i} style={{ background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: 6, padding: "8px 10px" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                        <span style={{ fontSize: 10, background: "var(--danger-bg)", color: "var(--danger)", padding: "1px 6px", borderRadius: 4, fontWeight: 600 }}>{sol.classification}</span>
                        <span style={{ fontSize: 10, color: "var(--ink-mid)", fontWeight: 500 }}>{Math.round((sol.similarity ?? 0) * 100)}% match</span>
                        <span style={{ fontSize: 10, color: "var(--faint)", marginLeft: "auto" }}>{sol.source ?? sol.similarity_method}</span>
                      </div>
                      {sol.resolution_text && (
                        <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.5 }}>
                          {sol.resolution_text.slice(0, 200)}{sol.resolution_text.length > 200 ? "…" : ""}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Thread info */}
      {isThread && (
        <div style={{ marginTop: flags.verdict !== "proceed" ? 10 : 0 }}>
          <button
            style={{ fontSize: 11, background: "none", border: "none", color: "var(--brand)", cursor: "pointer", padding: 0, marginBottom: 6, display: "flex", alignItems: "center", gap: 4 }}
            onClick={() => setShowTurns(s => !s)}
          >
            <MessageSquare size={12} />
            Thread: {flags.thread_depth} turns — AI processed latest message only
            <ChevronDown size={11} style={{ transform: showTurns ? "rotate(180deg)" : "none", transition: "transform .15s" }} />
          </button>
          {showTurns && flags.thread_turns && (
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {flags.thread_turns.map((turn: any, i: number) => (
                <div key={i} style={{ background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: 6, padding: "6px 10px" }}>
                  <div style={{ fontSize: 10, color: i === 0 ? "var(--success)" : "var(--faint)", marginBottom: 2, fontWeight: i === 0 ? 700 : 400 }}>
                    {i === 0 ? "▶ Latest message (processed by AI)" : `Turn ${i}`}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--ink-mid)" }}>
                    {turn.text?.slice(0, 250)}{turn.text?.length > 250 ? "…" : ""}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Regulatory breach */}
      {flags.regulatory_breach?.detected && (
        <RegulatoryBreachPanel rb={flags.regulatory_breach} />
      )}

      {/* Attachment extraction */}
      {(flags.attachments_extracted?.length ?? 0) > 0 && (
        <AttachmentPanel
          atts={flags.attachments_extracted!}
          entities={flags.attachment_entities}
        />
      )}

      {/* Auto-generated response draft */}
      {flags.auto_draft && (
        <AutoDraftPanel draft={flags.auto_draft} />
      )}

      {/* Impersonation risk */}
      {flags.impersonation_risk && (
        <ImpersonationRiskPanel risk={flags.impersonation_risk} />
      )}

      {/* Repeat / known-customer detection */}
      {flags.repeat_detection && (
        <RepeatDetectionPanel rd={flags.repeat_detection} loadCase={loadCase} />
      )}

      {/* Clarification request draft */}
      {flags.clarification && (
        <ClarificationPanel cl={flags.clarification} />
      )}
    </section>
  );
}

function AnalyticsView() {
  return (
    <section className="panel full" style={{ overflowY: "auto" }}>
      <PanelTitle title="AI/ML Analytics" icon={<TrendingUp size={17} />} />
      <div style={{ padding: "0 4px 16px" }}>
        <AnalyticsComponent />
      </div>
    </section>
  );
}

function CustomersView() {
  return (
    <section className="panel full" style={{ overflowY: "auto" }}>
      <PanelTitle title="Customer Database" icon={<Users size={17} />} />
      <div style={{ padding: "0 4px 16px" }}>
        <CustomerDatabase />
      </div>
    </section>
  );
}

function AuditView({ rows }: { rows: any[] }) {
  return (
    <section className="panel full">
      <PanelTitle title="Audit Timeline" icon={<Archive size={17} />} />
      <DataTable
        rows={rows}
        columns={[
          { key: "timestamp", label: "Time", render: v => new Date(v as string).toLocaleString("en-IN") },
          { key: "case_id", label: "Case" },
          { key: "role", label: "Role" },
          { key: "action", label: "Action" },
        ]}
      />
    </section>
  );
}



// ─── Queue List — drag-and-drop + arrow fallback ───────────────────────────────
function QueueSkeleton() {
  const heights = [62, 56, 68, 56, 62, 56];
  return (
    <div className="queue-skeleton">
      {heights.map((h, i) => (
        <div key={i} className="skel-card" style={{ height: h, animationDelay: `${i * 0.08}s` }} />
      ))}
    </div>
  );
}

function QueueList({ cases, loadCase, selectedCaseId, moveInQueue, reorderQueue, onUnpin, busy }: {
  cases: CaseSummary[]; loadCase: (id: string) => void; selectedCaseId?: string;
  moveInQueue: (caseId: string, direction: "up" | "down") => void;
  reorderQueue: (caseId: string, newIndex: number) => void;
  onUnpin: (caseId: string) => void;
  busy: boolean;
}) {
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const [overIdx, setOverIdx] = useState<number | null>(null);

  if (!cases.length) return <div className="empty">No active cases.</div>;

  return (
    <div className="dept-queue-list">
      {cases.map((item, idx) => {
        const priority = (item.department_priority ?? item.priority ?? "LOW").toLowerCase();
        const state = item.department_status ?? item.workflow_state ?? "";
        const isSelected = selectedCaseId === item.case_id;
        const isDragging = dragIdx === idx;
        const isOver = overIdx === idx && dragIdx !== null && dragIdx !== idx;

        return (
          <div
            key={item.case_id}
            draggable
            className={`dq-row dq-priority-${priority} ${isSelected ? "dq-selected" : ""} ${isDragging ? "dq-dragging" : ""} ${isOver ? "dq-drop-target" : ""}`}
            style={{ animationDelay: `${idx * 35}ms` }}
            onDragStart={e => {
              setDragIdx(idx);
              e.dataTransfer.effectAllowed = "move";
              e.dataTransfer.setData("text/plain", String(idx));
            }}
            onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; setOverIdx(idx); }}
            onDragLeave={() => setOverIdx(null)}
            onDrop={e => {
              e.preventDefault();
              if (dragIdx !== null && dragIdx !== idx) {
                reorderQueue(cases[dragIdx].case_id, idx);
              }
              setDragIdx(null); setOverIdx(null);
            }}
            onDragEnd={() => { setDragIdx(null); setOverIdx(null); }}
          >
            {/* Drag handle + rank */}
            <div className="dq-rank">
              <span className="dq-drag-handle" title="Drag to reorder">⠿</span>
              <span className="dq-num">{idx + 1}</span>
              <button
                className="dq-arrow" disabled={busy || idx === 0}
                onClick={e => { e.stopPropagation(); moveInQueue(item.case_id, "up"); }}
                title="Move up"
              ><ChevronUp size={11} /></button>
              <button
                className="dq-arrow" disabled={busy || idx === cases.length - 1}
                onClick={e => { e.stopPropagation(); moveInQueue(item.case_id, "down"); }}
                title="Move down"
              ><ChevronDown size={11} /></button>
            </div>

            <button className="dq-body" onClick={() => loadCase(item.case_id)}>
              <div className="dq-top">
                <span className={`priority ${priority}`}>{priority.toUpperCase()}</span>
                <span className="dq-id">{item.case_id}</span>
                <SlaChip alerts={item.sla_alerts ?? []} slaMeta={item.sla_metadata} state={item.workflow_state} />
                {item.intake_flags?.sender_validation_run && !item.intake_flags?.customer_matched && (
                  <UnverifiedBadge />
                )}
                {item.queue_pinned && (
                  <span className="pinned-badge" title="Manually positioned — click to unpin">
                    📌
                  </span>
                )}
              </div>
              <div className="dq-bottom">
                <span className="dq-summary">{item.summary || item.classification}</span>
                <span className={`state-badge state-${state.toLowerCase().replace(/_/g, "-")}`}>
                  {state.replace(/_/g, " ")}
                </span>
              </div>
            </button>
            {item.queue_pinned && (
              <button
                className="dq-unpin-btn"
                title="Release from manual position — auto-rank by SLA and priority"
                disabled={busy}
                onClick={e => { e.stopPropagation(); onUnpin(item.case_id); }}
              >
                ✕
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─── Closed Case List ─────────────────────────────────────────────────────────
function ClosedCaseList({ cases, loadCase, selectedCaseId }: {
  cases: CaseSummary[]; loadCase: (id: string) => void; selectedCaseId?: string;
}) {
  if (!cases.length) return <div className="empty">No closed cases yet.</div>;
  return (
    <div className="case-list">
      {cases.map(item => (
        <button
          key={item.case_id}
          className={`case-row case-row-closed ${selectedCaseId === item.case_id ? "selected" : ""}`}
          onClick={() => loadCase(item.case_id)}
        >
          <span className={stateClass(item.workflow_state)}>{item.workflow_state.replace(/_/g, " ")}</span>
          <span className="case-main">
            <strong>{item.case_id}</strong>
            {item.resolution_text
              ? <small className="resolution-preview"><CheckCircle2 size={12} /> {item.resolution_text.slice(0, 80)}{item.resolution_text.length > 80 ? "..." : ""}</small>
              : <small>{item.summary || item.classification}</small>}
          </span>
        </button>
      ))}
    </div>
  );
}

// ─── Case List ────────────────────────────────────────────────────────────────
function CaseList({ cases, loadCase, selectedCaseId, departmentMode = false }: {
  cases: any[]; loadCase: (id: string) => void; selectedCaseId?: string; departmentMode?: boolean;
}) {
  if (!cases.length) return <div className="empty">No active cases in queue.</div>;
  return (
    <div className="case-list">
      {cases.map(item => {
        // In department queues use the desk-local priority; fall back to global
        const displayPriority = departmentMode ? (item.department_priority ?? item.priority) : item.priority;
        return (
          <button key={item.case_id} className={`case-row ${selectedCaseId === item.case_id ? "selected" : ""} ${item.is_parent ? "case-row-parent" : ""} ${item.parent_case_id ? "case-row-child" : ""}`} onClick={() => loadCase(item.case_id)}>
            <span className={priorityClass(displayPriority)}>{displayPriority}</span>
            <span className="case-main">
              <span className="case-id-line">
                <strong>{item.case_id}</strong>
                {item.is_parent && <span className="multi-dept-badge">MULTI-DEPT ×{item.child_count}</span>}
                {item.parent_case_id && <span className="child-badge">SUB-CASE</span>}
              </span>
              <small>{item.summary || item.classification}</small>
            </span>
            <span className={stateClass(departmentMode ? item.department_status : item.workflow_state)}>
              {(departmentMode ? (item.department_status ?? item.workflow_state) : item.workflow_state).replace(/_/g, " ")}
            </span>
          </button>
        );
      })}
    </div>
  );
}

// ─── Case Workspace ───────────────────────────────────────────────────────────
function CaseWorkspace({ selectedCase, keyFields, compact = false, loadCase }: {
  selectedCase: CaseDetail | null; keyFields: string[]; compact?: boolean;
  loadCase?: (id: string) => void;
}) {
  const [expandedTl, setExpandedTl] = useState<number | null>(null);
  const [openSolution, setOpenSolution] = useState<SuggestedSolution | null>(null);
  const [draftResponse, setDraftResponse] = useState<any | null>(null);
  const [draftLoading, setDraftLoading] = useState(false);
  const [feedbackClass, setFeedbackClass] = useState("");
  const [feedbackNote, setFeedbackNote] = useState("");
  const [feedbackSent, setFeedbackSent] = useState(false);
  const [customerData, setCustomerData] = useState<any | null>(null);
  const [customerLoading, setCustomerLoading] = useState(false);

  // Auto-trigger customer lookup whenever a case is opened in the desk view
  useEffect(() => {
    setDraftResponse(null); setFeedbackSent(false); setFeedbackClass(""); setFeedbackNote("");
    setCustomerData(null);
    if (!selectedCase) return;
    setCustomerLoading(true);
    api.customerLookup(selectedCase.case_id)
      .then(setCustomerData)
      .catch(() => setCustomerData({ error: "Lookup failed." }))
      .finally(() => setCustomerLoading(false));
  }, [selectedCase?.case_id]);

  async function generateDraft() {
    if (!selectedCase) return;
    setDraftLoading(true);
    try { setDraftResponse(await api.generateDraftResponse(selectedCase.case_id)); }
    catch { setDraftResponse({ error: "Draft generation failed." }); }
    finally { setDraftLoading(false); }
  }

  async function sendFeedback() {
    if (!selectedCase || !feedbackClass) return;
    try {
      await api.submitFeedback(selectedCase.case_id, feedbackClass, feedbackNote || undefined);
      setFeedbackSent(true);
    } catch { /* ignore */ }
  }

  if (!selectedCase) return <div className="empty">Select a case from the queue.</div>;
  const fields = selectedCase.extracted_fields || {};
  const ai = selectedCase.ai_analysis || {};
  const timeline = (selectedCase.unified_timeline || []).slice().reverse().slice(0, 12);
  const solutions = selectedCase.suggested_solutions ?? [];

  return (
    <div className="case-workspace">
      {/* Solution popup */}
      {openSolution && (
        <div className="solution-overlay" onClick={() => setOpenSolution(null)}>
          <div className="solution-popup" onClick={e => e.stopPropagation()}>
            <div className="solution-popup-head">
              <Lightbulb size={15} />
              <span>Matched Case — {openSolution.case_id}</span>
              <button className="solution-popup-close" onClick={() => setOpenSolution(null)}>✕</button>
            </div>
            <div className="solution-popup-meta">
              <span className="suggestion-cls">{openSolution.classification}</span>
              <span className="suggestion-score">{Math.round(openSolution.similarity * 100)}% match · {openSolution.similarity_method}</span>
            </div>
            {openSolution.summary && (
              <div className="solution-popup-summary">{openSolution.summary}</div>
            )}
            <div className="solution-popup-resolution">
              <div className="solution-popup-label">Resolution applied in that case:</div>
              <div className="solution-popup-text">{openSolution.resolution_text}</div>
            </div>
          </div>
        </div>
      )}

      {ai.reopened_from && (
        <div className="reopen-banner">
          <RotateCcw size={11} />
          <span>Reopened from </span>
          <button className="reopen-banner-link" onClick={() => loadCase?.(ai.reopened_from)}>{ai.reopened_from}</button>
          {ai.reopen_note && <span className="reopen-banner-note"> — {ai.reopen_note}</span>}
        </div>
      )}

      {ai.portal_submission && (ai.suggested_department || selectedCase.extracted_fields?.suggested_department) && (
        <div className="portal-routing-banner">
          <Navigation size={11} />
          <span>Portal submission — suggested routing: </span>
          <strong>{ai.suggested_department || selectedCase.extracted_fields?.suggested_department}</strong>
          {ai.portal_request_type && <span className="portal-routing-type"> · {ai.portal_request_type}</span>}
        </div>
      )}

      <div className="summary-line">
        <span className={priorityClass(selectedCase.priority)}>{selectedCase.priority}</span>
        <strong>{selectedCase.case_id}</strong>
        <span className={stateClass(selectedCase.workflow_state)}>{selectedCase.workflow_state.replace(/_/g, " ")}</span>
        {selectedCase.needs_human_triage && (
          <span className="triage-badge"><BrainCircuit size={10} /> Needs Triage</span>
        )}
        {selectedCase.language && selectedCase.language !== "English" && (
          <span className="lang-badge"><Globe size={10} /> {selectedCase.language}</span>
        )}
        {selectedCase.incident_group && <span className="incident-badge">{selectedCase.incident_group}</span>}
      </div>

      {selectedCase.intake_flags && <SmartIntakePanel flags={selectedCase.intake_flags as IntakeFlags} loadCase={loadCase} />}

      <div className="info-grid">
        <Info label="Classification" value={selectedCase.classification} />
        <Info label="Primary Desk" value={selectedCase.primary_department} />
        <Info label="AI Confidence" value={`${Math.round(selectedCase.confidence_score * 100)}%`} />
        <Info label="Risk Score" value={`${Math.round(selectedCase.risk_score * 100)}%`} />
        {ai.sentiment && (
          <div className="info">
            <span>Sentiment</span>
            <strong style={{ fontWeight: "normal" }}>
              <SentimentDisplay sentiment={ai.sentiment} tone={ai.emotional_tone} distress={ai.distress_level} intensity={ai.sentiment_intensity} />
            </strong>
          </div>
        )}
      </div>

      {/* Triage lock banner — shown when case is still in HUMAN_TRIAGE */}
      {selectedCase.workflow_state === "HUMAN_TRIAGE" && (
        <div className="triage-lock-banner">
          <BrainCircuit size={14} />
          <div>
            <strong>Pending human triage review</strong>
            <div className="triage-lock-detail">This case has been flagged for manual classification review. It will appear in your queue once a triage reviewer releases it.</div>
          </div>
        </div>
      )}

      {/* Entities extracted from email */}
      {ai.extracted_entities && <ExtractedEntitiesPanel entities={ai.extracted_entities} />}

      {ai.adjudication_mode === "low_confidence" && (
        <div className="pipeline-warning">
          Model conflict: BERT says {ai.bert_classification ?? "—"}, Gemma says {ai.gemma_classification ?? "—"}. Verify routing.
        </div>
      )}

      {!compact && (
        <section className="subsection">
          <h3>AI Adjudication</h3>
          <div className="adjud-row">
            <AdjudBadge label="BERT" cls={ai.bert_classification} conf={ai.bert_confidence} />
            <ArrowRight className="adjud-arrow" size={16} />
            <AdjudBadge label="Gemma4" cls={ai.gemma_classification} conf={ai.gemma_confidence} />
            <ArrowRight className="adjud-arrow" size={16} />
            <span className={`adjud-mode adjud-${ai.adjudication_mode ?? "unknown"}`}>
              {ai.adjudication_mode ?? "—"}
            </span>
          </div>
          {(selectedCase.triage_labels?.length ?? 0) > 0 && (
            <div style={{ marginTop: 6 }}>
              <TriageLabelDisplay labels={selectedCase.triage_labels!} />
            </div>
          )}
          {ai.abstain && (
            <div className="pipeline-warning" style={{ borderColor: "var(--triage-col)", color: "var(--triage-col)" }}>
              AI abstained: {ai.abstain_reason || "confidence below threshold"}.
            </div>
          )}
        </section>
      )}

      <section className="subsection">
        <h3>Key Fields</h3>
        <div className="field-grid">
          {keyFields.map(k => (
            <Info key={k} label={k.replaceAll("_", " ")} value={fmt(fields[k])} />
          ))}
        </div>
      </section>

      {/* ── Inline Customer Record (auto-loaded at desk) ── */}
      <section className="subsection">
        <h3 style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Users size={12} /> Customer Record
          {customerLoading && <span style={{ fontSize: 10, color: "var(--muted)", marginLeft: 4 }}>Loading…</span>}
          {customerData && !customerData.error && (
            <span className={`validation-status-badge status-${customerData.validation_status}`} style={{ marginLeft: 4 }}>
              {customerData.validation_status === "full" ? "✓ Matched"
                : customerData.validation_status === "partial" ? "~ Partial"
                : customerData.validation_status === "no_identifiers" ? "— No ID"
                : "✗ No Match"}
            </span>
          )}
          <button style={{ marginLeft: "auto", fontSize: 10, padding: "2px 8px" }}
            onClick={() => {
              if (!selectedCase) return;
              setCustomerLoading(true);
              api.customerLookup(selectedCase.case_id)
                .then(setCustomerData)
                .catch(() => setCustomerData({ error: "Lookup failed." }))
                .finally(() => setCustomerLoading(false));
            }}
            disabled={customerLoading}
          >
            {customerLoading ? "…" : "↻ Re-check"}
          </button>
        </h3>

        {/* Impersonation warning — shown when IMPERSONATION tag is on the case */}
        {((selectedCase.ai_analysis?.raw_triage?.secondary_tags ?? []) as string[])
          .includes("IMPERSONATION") && (
          <div className="impersonation-alert">
            <Siren size={14} /> Impersonation attempt detected — verify sender identity before taking any account action.
          </div>
        )}

        {customerData && !customerData.error && customerData.matched_customer && (
          <div className="desk-resolution-card">
            {/* Core identity row */}
            <div className="dcc-header">
              <div>
                <div className="dcc-name">{customerData.matched_customer.name}</div>
                <div className="dcc-meta">customer reference: {customerData.matched_customer.customer_ref} · {customerData.matched_customer.service_area}</div>
              </div>
              <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
                <span className={`verification-badge verification-${customerData.matched_customer.verification_status?.toLowerCase()}`}>
                  identity verification: {customerData.matched_customer.verification_status}
                </span>
                <span style={{ fontSize: 10, color: customerData.matched_customer.status === "ACTIVE" ? "var(--success)" : "var(--danger)" }}>
                  ● {customerData.matched_customer.status}
                </span>
              </div>
            </div>

            {/* Contact & identifiers */}
            <div className="dcc-grid">
              <div className="dcc-row"><span>Mobile</span><strong>{customerData.matched_customer.mobile_number || "—"}</strong></div>
              <div className="dcc-row"><span>Email</span><strong>{customerData.matched_customer.email || "—"}</strong></div>
              <div className="dcc-row"><span>Segment</span><strong>{customerData.matched_customer.segment || customerData.matched_customer.customer_segment || "—"}</strong></div>
              <div className="dcc-row"><span>RM</span><strong>{customerData.matched_customer.account_manager || "—"}</strong></div>
            </div>

            {/* Accounts */}
            {customerData.matched_customer.connection_ids?.length > 0 && (
              <div className="dcc-section">
                <div className="dcc-section-label">Accounts</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {customerData.matched_customer.connection_ids.map((a: string) => (
                    <span key={a} className="dcc-chip dcc-chip-account">{a}</span>
                  ))}
                </div>
              </div>
            )}

            {/* Products */}
            {customerData.matched_customer.product_types?.length > 0 && (
              <div className="dcc-section">
                <div className="dcc-section-label">Products</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {customerData.matched_customer.product_types.map((p: string) => (
                    <span key={p} className="dcc-chip">{p}</span>
                  ))}
                </div>
              </div>
            )}

            {/* What we matched on */}
            {customerData.matched_fields?.length > 0 && (
              <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>
                Matched on: {customerData.matched_fields.join(", ")}
              </div>
            )}
          </div>
        )}

        {customerData && !customerData.error && customerData.validation_status === "no_identifiers" && (
          <div style={{ fontSize: 11, color: "var(--muted)" }}>
            No account/customer reference/card identifiers found in the email — cannot auto-match customer record.
          </div>
        )}
        {customerData && !customerData.error && customerData.validation_status === "none" && (
          <div style={{ fontSize: 11, color: "var(--danger)" }}>
            Identifiers found but no matching record in database. Possible typo, unregistered reference, or impersonation attempt.
          </div>
        )}
        {customerData?.error && <div style={{ fontSize: 11, color: "var(--danger)" }}>{customerData.error}</div>}
        {!customerData && !customerLoading && (
          <div style={{ fontSize: 11, color: "var(--very-muted)" }}>Customer record will appear here automatically.</div>
        )}
      </section>

      {ai.fraud_indicators?.length > 0 && (
        <section className="subsection">
          <h3>Fraud Indicators</h3>
          <div className="tag-row">
            {(ai.fraud_indicators as string[]).map(f => <span key={f} className="finding-tag tag-danger">{f}</span>)}
          </div>
        </section>
      )}

      {selectedCase.secondary_departments?.length > 0 && (
        <section className="subsection">
          <h3>Secondary Departments</h3>
          <div className="tag-row">
            {selectedCase.secondary_departments.map((s: any, i) => (
              <span key={i} className="finding-tag tag-info">{s.department} ({Math.round((s.confidence ?? 0) * 100)}%)</span>
            ))}
          </div>
        </section>
      )}

      {/* ── Original case context (only on reopened cases) ── */}
      {ai.reopened_from && (
        <OriginalCaseContextPanel ai={ai} loadCase={loadCase} />
      )}

      {/* ── Customer history ── */}
      <CustomerHistoryPanel caseId={selectedCase.case_id} />

      {/* ── Similar cases insight (synthesis + list) ── */}
      <SimilarCasesInsightPanel
        solutions={solutions}
        synthesis={selectedCase.solutions_synthesis}
      />

      {/* ── Previous solutions — click any card to open popup ── */}
      {solutions.length > 0 && (
        <section className="subsection suggested-solutions">
          <h3 className="suggestion-title"><Lightbulb size={14} /> Previous Solutions ({solutions.length} matched)</h3>
          <div className="suggestion-chips">
            {solutions.map((s: SuggestedSolution) => (
              <button
                key={s.case_id}
                className="suggestion-chip"
                onClick={() => setOpenSolution(s)}
                title="Click to view solution"
              >
                <span className="suggestion-chip-cls">{s.classification}</span>
                <span className="suggestion-chip-score">{Math.round(s.similarity * 100)}% match</span>
                <ChevronDown size={11} />
              </button>
            ))}
          </div>
        </section>
      )}

      {/* ── AI Draft Response ── */}
      <section className="subsection">
        <h3 style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Send size={13} /> AI Draft Response
          <button style={{ marginLeft: "auto", fontSize: 10, padding: "2px 8px" }} onClick={generateDraft} disabled={draftLoading}>
            {draftLoading ? "Generating…" : draftResponse ? "Regenerate" : "Generate Draft"}
          </button>
        </h3>
        {draftResponse && !draftResponse.error && (
          <div style={{ fontSize: 11, lineHeight: 1.6 }}>
            <span style={{ fontSize: 10, padding: "1px 6px", borderRadius: 4, background: draftResponse.source === "llm" ? "var(--compliance-bg)" : "var(--surface-3)", color: draftResponse.source === "llm" ? "var(--compliance)" : "var(--muted)", marginBottom: 6, display: "inline-block" }}>
              {draftResponse.source === "llm" ? "AI Generated" : "Template"}
            </span>
            <div style={{ background: "var(--surface-raised)", borderRadius: 6, padding: "8px 10px", marginTop: 6 }}>
              <div style={{ fontWeight: 600, color: "var(--text-primary)", marginBottom: 4 }}>Subject: {draftResponse.subject}</div>
              <pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit", fontSize: 11, color: "var(--text-secondary)", margin: 0 }}>{draftResponse.body}</pre>
            </div>
          </div>
        )}
        {draftResponse?.error && <div style={{ fontSize: 11, color: "var(--danger)" }}>{draftResponse.error}</div>}
        {!draftResponse && !draftLoading && (
          <div style={{ fontSize: 11, color: "var(--very-muted)" }}>Click "Generate Draft" for an AI-powered response template.</div>
        )}
      </section>

      {/* ── ML Classification Feedback ── */}
      <section className="subsection">
        <h3 style={{ display: "flex", alignItems: "center", gap: 6 }}><BrainCircuit size={13} /> Classification Feedback</h3>
        {feedbackSent ? (
          <div style={{ fontSize: 11, color: "var(--success)" }}>✓ Feedback recorded — thank you for improving the model.</div>
        ) : (
          <div style={{ fontSize: 11 }}>
            <div style={{ color: "var(--very-muted)", marginBottom: 6 }}>
              AI classified as: <strong style={{ color: "var(--text-primary)" }}>{selectedCase.classification}</strong>
              {" "}({Math.round((selectedCase.confidence_score ?? 0) * 100)}% confidence)
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <select style={{ fontSize: 11, padding: "4px 6px", borderRadius: 4, background: "var(--surface-raised)", border: "1px solid var(--border)", color: "var(--text-primary)", width: "100%" }}
                value={feedbackClass} onChange={e => setFeedbackClass(e.target.value)}>
                <option value="">— Select correct classification —</option>
                {["UNAUTHORISED_USE","CUSTOMER_GRIEVANCE","CONNECTION_FAULT","VERIFICATION_QUERY","REGULATORY","LEGAL_NOTICE","PORTAL_ACCESS","PAYMENT_FAILURE","ESCALATION","DUPLICATE","SPAM","GENERAL_QUERY"].map(cls => (
                  <option key={cls} value={cls}>{cls}</option>
                ))}
              </select>
              <input style={{ fontSize: 11, padding: "4px 6px", borderRadius: 4, background: "var(--surface-raised)", border: "1px solid var(--border)", color: "var(--text-primary)", width: "100%" }}
                placeholder="Optional note" value={feedbackNote} onChange={e => setFeedbackNote(e.target.value)} />
              <button style={{ fontSize: 10, padding: "4px 10px", alignSelf: "flex-start" }} onClick={sendFeedback} disabled={!feedbackClass}>Submit Feedback</button>
            </div>
          </div>
        )}
      </section>

      {/* ── Timeline — click any row to expand details ── */}
      <section className="subsection">
        <h3>Timeline</h3>
        <div className="timeline">
          {timeline.map((ev, i) => (
            <div key={i}>
              <button
                className={`timeline-row timeline-row-btn ${expandedTl === i ? "timeline-expanded" : ""} ${ev.event_type === "send_communication" ? "timeline-row-comm" : ""}`}
                onClick={() => setExpandedTl(expandedTl === i ? null : i)}
              >
                <strong>{ev.event_type === "send_communication" ? "✉ Communication" : ev.event_type?.replace(/_/g, " ")}</strong>
                <span>{ev.message}</span>
                <small>{new Date(ev.timestamp).toLocaleString("en-IN", { dateStyle: "short", timeStyle: "short" })}</small>
                <ChevronDown size={12} style={{ flexShrink: 0, transform: expandedTl === i ? "rotate(180deg)" : "none", transition: "transform .15s", color: "var(--faint)" }} />
              </button>
              {expandedTl === i && (
                <div className="timeline-detail">
                  {ev.actor && <div><span className="tl-label">By:</span> {ev.actor}{ev.department ? ` · ${ev.department}` : ""}</div>}
                  {ev.event_type === "send_communication" ? (
                    <div className="tl-comm-body">
                      {ev.metadata?.recipient_email && (
                        <div className="tl-comm-to"><span className="tl-label">To:</span> {ev.metadata.recipient_email}</div>
                      )}
                      <div className="tl-comm-status">
                        <span className="tl-label">Status:</span>
                        <span className="tl-comm-pending">Queued · pending SMTP</span>
                      </div>
                      <pre className="tl-comm-text">{ev.metadata?.communication_body}</pre>
                    </div>
                  ) : (
                    <>
                      {ev.metadata?.note && (
                        <div className="tl-note-body"><span className="tl-label">Note:</span> {ev.metadata.note}</div>
                      )}
                      {ev.metadata?.transfer_to && (
                        <div><span className="tl-label">Transferred to:</span> {ev.metadata.transfer_to}</div>
                      )}
                      {ev.metadata?.requested_department && (
                        <div><span className="tl-label">Requested dept:</span> {ev.metadata.requested_department}</div>
                      )}
                      {ev.metadata?.ticket_ref && (
                        <div><span className="tl-label">Ticket ref:</span> {ev.metadata.ticket_ref}</div>
                      )}
                      {ev.metadata?.regulatory_ref && (
                        <div><span className="tl-label">Regulatory ref:</span> {ev.metadata.regulatory_ref}</div>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </section>

      {/* ── Parent case: sub-cases panel ── */}
      {selectedCase.is_parent && (selectedCase.children ?? []).length > 0 && (
        <section className="subsection">
          <h3>Sub-Cases (read-only per desk)</h3>
          <SubCasesPanel children={selectedCase.children!} loadCase={loadCase} />
        </section>
      )}

      {/* ── Child case: parent + sibling panel ── */}
      {selectedCase.parent_case_id && (
        <section className="subsection">
          <h3>Parent &amp; Sibling Cases</h3>
          {selectedCase.parent_summary && (
            <div className="parent-summary-row">
              <span className="parent-label">Parent</span>
              <button
                className="sibling-btn"
                onClick={() => loadCase?.(selectedCase.parent_summary!.case_id)}
              >
                <span className="multi-dept-badge">PARENT</span>
                <strong>{selectedCase.parent_summary.case_id}</strong>
                <span className={stateClass(selectedCase.parent_summary.workflow_state)}>
                  {selectedCase.parent_summary.workflow_state}
                </span>
              </button>
            </div>
          )}
          {(selectedCase.sibling_cases ?? []).length > 0 && (
            <div className="siblings-list">
              {selectedCase.sibling_cases!.map(s => (
                <button
                  key={s.case_id}
                  className="sibling-btn"
                  onClick={() => loadCase?.(s.case_id)}
                >
                  <span className="child-badge">SIBLING</span>
                  <strong>{s.case_id}</strong>
                  <span className="sibling-dept">{s.primary_department}</span>
                  <span className={stateClass(s.workflow_state)}>{s.workflow_state}</span>
                </button>
              ))}
            </div>
          )}
        </section>
      )}

      {!compact && (
        <section className="subsection">
          <h3>Normalized Email View (connection ids masked)</h3>
          <pre>{selectedCase.normalized_text}</pre>
        </section>
      )}
    </div>
  );
}

// ─── Sub-cases panel (shown on parent case) ───────────────────────────────────
function SubCasesPanel({ children, loadCase }: { children: CaseSummary[]; loadCase?: (id: string) => void }) {
  return (
    <div className="sub-cases-panel">
      {children.map(child => (
        <div key={child.case_id} className="sub-case-row">
          <span className="child-badge">SUB</span>
          <button
            className="sub-case-btn"
            onClick={() => loadCase?.(child.case_id)}
            title="Open sub-case"
          >
            <strong>{child.case_id}</strong>
          </button>
          <span className="sub-case-dept">{child.primary_department}</span>
          <span className={stateClass(child.workflow_state)}>{child.workflow_state}</span>
          <span className={priorityClass(child.priority)}>{child.priority}</span>
          {child.sla_alerts?.length > 0 && (
            <span className="sla-badge sla-nearing_breach"><AlertTriangle size={12} /> SLA</span>
          )}
        </div>
      ))}
    </div>
  );
}

// ─── Adjudication badge ───────────────────────────────────────────────────────
function AdjudBadge({ label, cls, conf }: { label: string; cls?: string; conf?: number }) {
  return (
    <div className="adjud-badge">
      <span className="adjud-model">{label}</span>
      <span className="adjud-cls">{cls ?? "—"}</span>
      <span className="adjud-conf">{conf != null ? `${Math.round(conf * 100)}%` : ""}</span>
    </div>
  );
}

// ─── Original Case Context Panel (shown on reopened cases) ──────────────────
function OriginalCaseContextPanel({ ai, loadCase }: { ai: any; loadCase?: (id: string) => void }) {
  const [open, setOpen] = useState(true);
  const [tlOpen, setTlOpen] = useState(false);
  const [commOpen, setCommOpen] = useState(false);

  const origId: string = ai.reopened_from;
  const origResolution: string = ai.original_resolution || "";
  const origClassification: string = ai.original_classification || "";
  const origDept: string = ai.original_department || "";
  const origState: string = ai.original_workflow_state || "";
  const origTimeline: any[] = ai.original_timeline || [];
  const origComms: any[] = ai.original_communications || [];
  const origSolutions: any[] = ai.original_suggested_solutions || [];

  return (
    <div className="ocp-panel">
      <button className="ocp-header" onClick={() => setOpen(p => !p)}>
        <RotateCcw size={12} />
        <span className="ocp-title">Original Case Reference</span>
        <button className="ocp-link" onClick={e => { e.stopPropagation(); loadCase?.(origId); }}>{origId}</button>
        <ChevronDown size={12} style={{ marginLeft: "auto", transform: open ? "rotate(180deg)" : "none", transition: "transform .15s", flexShrink: 0 }} />
      </button>

      {open && (
        <div className="ocp-body">
          <div className="ocp-meta-row">
            {origClassification && <span className="ocp-chip ocp-cls">{origClassification}</span>}
            {origDept && <span className="ocp-chip ocp-dept">{origDept}</span>}
            {origState && <span className="ocp-chip ocp-state">{origState.replace(/_/g, " ")}</span>}
          </div>

          {ai.reopen_note && (
            <div className="ocp-reopen-note">
              <span className="ocp-field-label">Reason reopened:</span> {ai.reopen_note}
            </div>
          )}

          {origResolution && (
            <div className="ocp-section">
              <div className="ocp-field-label">Original resolution</div>
              <div className="ocp-resolution">{origResolution}</div>
            </div>
          )}

          {origSolutions.length > 0 && (
            <div className="ocp-section">
              <div className="ocp-field-label">Solutions applied previously ({origSolutions.length})</div>
              {origSolutions.slice(0, 3).map((s: any, i: number) => (
                <div key={i} className="ocp-solution-row">
                  <span className="ocp-sol-cls">{s.classification}</span>
                  <span className="ocp-sol-res">{s.resolution_text}</span>
                </div>
              ))}
            </div>
          )}

          {origComms.length > 0 && (
            <div className="ocp-section">
              <button className="ocp-toggle" onClick={() => setCommOpen(p => !p)}>
                <MessageSquare size={10} /> Communications from original ({origComms.length})
                <ChevronDown size={10} style={{ marginLeft: "auto", transform: commOpen ? "rotate(180deg)" : "none", transition: "transform .15s" }} />
              </button>
              {commOpen && origComms.map((c: any, i: number) => (
                <div key={i} className="ocp-comm-row">
                  <div className="ocp-comm-meta">{c.actor} · {c.department} · {new Date(c.timestamp).toLocaleString("en-IN", { dateStyle: "short", timeStyle: "short" })}</div>
                  <div className="ocp-comm-text">{c.communication_body || c.note}</div>
                </div>
              ))}
            </div>
          )}

          {origTimeline.length > 0 && (
            <div className="ocp-section">
              <button className="ocp-toggle" onClick={() => setTlOpen(p => !p)}>
                <Clock size={10} /> Original timeline ({origTimeline.length} events)
                <ChevronDown size={10} style={{ marginLeft: "auto", transform: tlOpen ? "rotate(180deg)" : "none", transition: "transform .15s" }} />
              </button>
              {tlOpen && [...origTimeline].reverse().map((ev: any, i: number) => (
                <div key={i} className="ocp-tl-row">
                  <span className="ocp-tl-type">{ev.event_type?.replace(/_/g, " ")}</span>
                  <span className="ocp-tl-msg">{ev.message}</span>
                  <span className="ocp-tl-time">{new Date(ev.timestamp).toLocaleString("en-IN", { dateStyle: "short", timeStyle: "short" })}</span>
                  {ev.metadata?.note && <div className="ocp-tl-note">{ev.metadata.note}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Customer Record Card (inline expandable) ────────────────────────────────
function CustomerRecordModal(_props: any) { return null; } // kept for TS — replaced by CustomerCard

function CrmRow({ label, value }: { label: string; value?: any }) {
  if (value == null || value === "" || (Array.isArray(value) && !value.length)) return null;
  const display = Array.isArray(value) ? value.join(", ") : String(value);
  return (
    <div className="crm-row">
      <span className="crm-label">{label}</span>
      <span className="crm-value">{display}</span>
    </div>
  );
}

function CrmAccCard({ top, status, meta }: { top: React.ReactNode; status?: string; meta: React.ReactNode }) {
  const cls = (status || "").toLowerCase();
  return (
    <div className="crm-acc-card">
      <div className="crm-acc-top">
        {top}
        {status && <span className={`crm-acc-status crm-acc-${cls}`}>{status}</span>}
      </div>
      <div className="crm-acc-meta">{meta}</div>
    </div>
  );
}

function CustomerCard({ data, onRevalidate, loading }: {
  data: any; onRevalidate: () => void; loading: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"identity"|"accounts"|"cards"|"contracts"|"digital"|"risk">("identity");
  const c = data.matched_customer;
  const risk = data.impersonation_risk;

  const addr = c?.address || {};
  const addrStr = [addr.street, addr.city, addr.state, addr.pincode].filter(Boolean).join(", ");

  const tabs = [
    { id: "identity" as const,  label: "Identity" },
    { id: "accounts" as const,  label: `Accounts${(c?.connection_details||[]).length ? ` (${c.connection_details.length})` : ""}` },
    { id: "cards" as const,     label: `Cards${(c?.device_details||[]).length ? ` (${c.device_details.length})` : ""}` },
    { id: "contracts" as const,     label: `Contracts${(c?.contracts||[]).length ? ` (${c.contracts.length})` : ""}` },
    { id: "digital" as const,   label: "Digital" },
    { id: "risk" as const,      label: "Risk" },
  ];

  return (
    <div className="cust-card">
      {/* Top bar: status + risk + revalidate */}
      <div className="cust-card-top">
        <span className={`validation-status-badge status-${data.validation_status}`}>
          {data.validation_status === "full" ? "✓ Full Match"
            : data.validation_status === "partial" ? "~ Partial Match"
            : data.validation_status === "no_identifiers" ? "— No Identifiers"
            : "✗ No Match"}
        </span>
        {risk?.level && risk.level !== "NONE" && risk.level !== "UNKNOWN" && (
          <span className={`crm-risk-badge crm-risk-${risk.level.toLowerCase()}`}>⚠ {risk.level}</span>
        )}
        <button className="cust-revalidate" onClick={onRevalidate} disabled={loading}>
          <RefreshCcw size={11} />
        </button>
      </div>

      {/* Extracted identifiers */}
      {(() => {
        const rows = Object.entries(data.extracted || {}).filter(([, v]) => (v as string[]).length > 0);
        return rows.length > 0 ? (
          <div className="cust-extracted">
            <div className="cust-ext-label">Extracted from email</div>
            {rows.map(([key, vals]) => (
              <div key={key} className="cust-ext-row">
                <span className="cust-ext-key">{key.replace(/_/g, " ")}</span>
                <span className="cust-ext-val">{(vals as string[]).join(", ")}</span>
              </div>
            ))}
          </div>
        ) : null;
      })()}

      {/* No match notice */}
      {!c && data.validation_status === "none" && (
        <div className="cust-no-match">Identifiers found but no matching record — may be a typo or unregistered reference.</div>
      )}

      {/* Matched customer summary + expandable full record */}
      {c && (
        <>
          <button className="cust-record-btn" onClick={() => setOpen(o => !o)}>
            <div className="cust-record-avatar">{(c.name||"?")[0].toUpperCase()}</div>
            <div className="cust-record-info">
              <div className="cust-record-name">{c.name}</div>
              <div className="cust-record-meta">
                <span className="cust-customer_ref">customer reference {c.customer_ref}</span>
                {c.customer_segment && <span className="cust-seg">{c.customer_segment}</span>}
                {c.verification_status && <span className={`verification-badge verification-${c.verification_status.toLowerCase()}`}>{c.verification_status}</span>}
              </div>
              <div className="cust-record-sub">
                {c.service_area && <span>{c.service_area}</span>}
                {(c.connection_ids||[]).length > 0 && <span>{c.connection_ids.length} account{c.connection_ids.length !== 1 ? "s" : ""}</span>}
                {(c.contracts||[]).length > 0 && <span>{c.contracts.length} contract{c.contracts.length !== 1 ? "s" : ""}</span>}
                {c.payment_score && <span>Score {c.payment_score}</span>}
              </div>
            </div>
            <ChevronDown size={14} className={`cust-chevron ${open ? "cust-chevron-open" : ""}`} />
          </button>

          {open && (
            <div className="crm-drawer">
              {/* Tab bar */}
              <div className="crm-tabs">
                {tabs.map(t => (
                  <button key={t.id} className={`crm-tab ${tab === t.id ? "crm-tab-active" : ""}`} onClick={() => setTab(t.id)}>
                    {t.label}
                  </button>
                ))}
              </div>

              <div className="crm-body">
                {/* ── IDENTITY ── */}
                {tab === "identity" && (
                  <>
                    <div className="crm-sect">
                      <div className="crm-sect-title">Personal</div>
                      <CrmRow label="Full Name"    value={c.name} />
                      <CrmRow label="Date of Birth" value={c.date_of_birth} />
                      <CrmRow label="PAN"           value={c.tax_id} />
                      <CrmRow label="Aadhaar"       value={c.id_last4} />
                      <CrmRow label="Occupation"    value={c.occupation} />
                      <CrmRow label="Annual Income" value={c.annual_income ? `₹ ${Number(c.annual_income).toLocaleString("en-IN")}` : null} />
                      <CrmRow label="Credit Score"  value={c.payment_score} />
                      <CrmRow label="Address"       value={addrStr || null} />
                      <CrmRow label="Segment"       value={c.customer_segment} />
                      <CrmRow label="Status"        value={c.status} />
                    </div>
                    <div className="crm-sect">
                      <div className="crm-sect-title">Contact</div>
                      <CrmRow label="Mobile"   value={c.mobile_number} />
                      <CrmRow label="Email"    value={c.email} />
                      <CrmRow label="Service area"   value={c.service_area} />
                      <CrmRow label="Service area Code" value={c.area_code} />
                      <CrmRow label="RM"       value={c.account_manager} />
                    </div>
                    {c.alternate_contact && (
                      <div className="crm-sect">
                        <div className="crm-sect-title">Nominee</div>
                        <CrmRow label="Name"         value={c.alternate_contact.name} />
                        <CrmRow label="Relationship" value={c.alternate_contact.relationship} />
                        <CrmRow label="DOB"          value={c.alternate_contact.dob} />
                        <CrmRow label="Mobile"       value={c.alternate_contact.mobile} />
                      </div>
                    )}
                    {c.roaming_profile && (
                      <div className="crm-sect">
                        <div className="crm-sect-title">NRI Details</div>
                        <CrmRow label="Country"      value={c.roaming_profile.country_of_residence} />
                        <CrmRow label="Passport"     value={c.roaming_profile.passport_number} />
                        <CrmRow label="Visa"         value={c.roaming_profile.visa_type} />
                        <CrmRow label="FEMA Date"    value={c.roaming_profile.fema_declaration_date} />
                      </div>
                    )}
                  </>
                )}

                {/* ── ACCOUNTS ── */}
                {tab === "accounts" && (
                  <>
                    {(c.connection_details || []).length > 0 ? (
                      <div className="crm-card-list">
                        {c.connection_details.map((a: any, i: number) => (
                          <CrmAccCard key={i} status={a.status}
                            top={<><span className="crm-acc-num">{a.connection_id}</span>{a.balance != null && <span className="crm-acc-bal">₹ {Number(a.balance).toLocaleString("en-IN")}</span>}</>}
                            meta={<><span>{a.type}</span>{a.exchange_code && <span>{a.exchange_code}</span>}{a.od_limit != null && <span>OD ₹ {Number(a.od_limit).toLocaleString("en-IN")}</span>}</>}
                          />
                        ))}
                      </div>
                    ) : (c.connection_ids||[]).length > 0 ? (
                      <div className="crm-sect"><div className="crm-sect-title">Connection IDs</div><CrmRow label="Numbers" value={c.connection_ids.join(", ")} /></div>
                    ) : <div className="crm-empty">No account data.</div>}

                    {(c.addon_services || []).length > 0 && (
                      <div className="crm-sect">
                        <div className="crm-sect-title">Deposits / FD / RD</div>
                        <div className="crm-card-list">
                          {c.addon_services.map((d: any, i: number) => (
                            <CrmAccCard key={i} status={d.status}
                              top={<><span className="crm-acc-num">{d.folio_number}</span>{d.maturity_amount != null && <span className="crm-acc-bal">₹ {Number(d.maturity_amount).toLocaleString("en-IN")}</span>}</>}
                              meta={<><span>{d.type}</span>{d.principal_amount != null && <span>Principal ₹ {Number(d.principal_amount).toLocaleString("en-IN")}</span>}{d.interest_rate && <span>{d.interest_rate}% p.a.</span>}{d.maturity_date && <span>Matures {d.maturity_date}</span>}{d.auto_renew && <span>Auto-renew</span>}</>}
                            />
                          ))}
                        </div>
                      </div>
                    )}
                    {(c.static_ip_blocks || []).length > 0 && (
                      <div className="crm-sect">
                        <div className="crm-sect-title">Static IP</div>
                        {c.static_ip_blocks.map((d: any, i: number) => (
                          <div key={i} className="crm-row"><span className="crm-label">{d.static IP_number || d.dp_id}</span><span className="crm-value">{[d.dp_name, d.status].filter(Boolean).join(" · ")}</span></div>
                        ))}
                      </div>
                    )}
                  </>
                )}

                {/* ── CARDS ── */}
                {tab === "cards" && (
                  <>
                    {(c.device_details || []).length > 0 ? (
                      <div className="crm-card-list">
                        {c.device_details.map((cd: any, i: number) => (
                          <CrmAccCard key={i} status={cd.status}
                            top={<><span className="crm-acc-num">{cd.card_number}</span>{cd.outstanding_balance != null && <span className="crm-acc-bal">₹ {Number(cd.outstanding_balance).toLocaleString("en-IN")}</span>}</>}
                            meta={<><span>{[cd.type, cd.variant, cd.network].filter(Boolean).join(" · ")}</span>{cd.expiry && <span>Exp {cd.expiry}</span>}{cd.credit_limit != null && <span>Limit ₹ {Number(cd.credit_limit).toLocaleString("en-IN")}</span>}</>}
                          />
                        ))}
                      </div>
                    ) : <div className="crm-empty">No card data.</div>}

                    {(c.premises_equipment || []).length > 0 && (
                      <div className="crm-sect">
                        <div className="crm-sect-title">Safe Deposit Lockers</div>
                        {c.premises_equipment.map((l: any, i: number) => (
                          <div key={i} className="crm-row"><span className="crm-label">{l.premises_kit_number}</span><span className="crm-value">{[l.service_area, l.size, l.status].filter(Boolean).join(" · ")}</span></div>
                        ))}
                      </div>
                    )}
                  </>
                )}

                {/* ── LOANS ── */}
                {tab === "contracts" && (
                  <>
                    {(c.contracts || []).length > 0 ? (
                      <div className="crm-card-list">
                        {c.contracts.map((l: any, i: number) => (
                          <CrmAccCard key={i} status={l.status}
                            top={<><span className="crm-acc-num">{l.contract_number}</span>{l.outstanding_amount != null && <span className="crm-acc-bal">₹ {Number(l.outstanding_amount).toLocaleString("en-IN")}</span>}</>}
                            meta={<><span>{l.type}</span>{l.instalment_amount != null && <span>instalment ₹ {Number(l.instalment_amount).toLocaleString("en-IN")}</span>}{l.interest_rate && <span>{l.interest_rate}% p.a.</span>}{l.next_instalment_date && <span>Next instalment {l.next_instalment_date}</span>}{l.collateral && <span>Collateral: {l.collateral}</span>}</>}
                          />
                        ))}
                      </div>
                    ) : <div className="crm-empty">No contracts.</div>}

                    {(c.protection_plans || []).length > 0 && (
                      <div className="crm-sect">
                        <div className="crm-sect-title">Insurance</div>
                        <div className="crm-card-list">
                          {c.protection_plans.map((p: any, i: number) => (
                            <CrmAccCard key={i} status={p.status}
                              top={<><span className="crm-acc-num">{p.policy_number}</span>{p.sum_assured != null && <span className="crm-acc-bal">₹ {Number(p.sum_assured).toLocaleString("en-IN")}</span>}</>}
                              meta={<><span>{[p.type, p.insurer].filter(Boolean).join(" · ")}</span>{p.premium_annual != null && <span>₹ {Number(p.premium_annual).toLocaleString("en-IN")} p.a.</span>}{p.maturity_date && <span>Matures {p.maturity_date}</span>}</>}
                            />
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                )}

                {/* ── DIGITAL ── */}
                {tab === "digital" && (
                  <div className="crm-sect">
                    <div className="crm-sect-title">Digital Service</div>
                    <CrmRow label="Internet Service" value={c.self_care_portal} />
                    <CrmRow label="Mobile Service"   value={c.mobile_app_access} />
                    <CrmRow label="SMS Alerts"       value={c.sms_alerts ? "Enabled" : "Disabled"} />
                    <CrmRow label="Email Alerts"     value={c.email_alerts ? "Enabled" : "Disabled"} />
                    <CrmRow label="UPI IDs"          value={c.payment_handles} />
                    <CrmRow label="Products"         value={c.product_types} />
                  </div>
                )}

                {/* ── RISK ── */}
                {tab === "risk" && (
                  <>
                    <div className="crm-sect">
                      <div className="crm-sect-title">Impersonation Risk</div>
                      {risk ? (
                        <>
                          <div className="crm-row">
                            <span className="crm-label">Level</span>
                            <span className={`crm-risk-badge crm-risk-${(risk.level||"none").toLowerCase()}`}>{risk.level}</span>
                          </div>
                          <CrmRow label="Score" value={risk.score != null ? `${risk.score} / 100` : null} />
                          <CrmRow label="Recommendation" value={risk.recommendation} />
                          {(risk.signals || []).map((s: string, i: number) => (
                            <div key={i} className="crm-risk-signal">⚠ {s}</div>
                          ))}
                        </>
                      ) : <div className="crm-empty">No risk data available.</div>}
                    </div>
                    <div className="crm-sect">
                      <div className="crm-sect-title">Match Quality</div>
                      <CrmRow label="Score"           value={data.match_score != null ? `${data.match_score} identifier${data.match_score !== 1 ? "s" : ""}` : null} />
                      <CrmRow label="Matched"         value={data.matched_fields} />
                      <CrmRow label="Unmatched"       value={data.unmatched_fields} />
                    </div>
                  </>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ─── Customer History Panel ───────────────────────────────────────────────────
function CustomerHistoryPanel({ caseId }: { caseId: string }) {
  const [data, setData] = useState<{ cases: any[]; total: number; sender_email: string | null } | null>(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    setData(null);
    setLoading(true);
    api.customerHistory(caseId)
      .then(d => setData(d))
      .catch(() => setData({ cases: [], total: 0, sender_email: null }))
      .finally(() => setLoading(false));
  }, [caseId]);

  const hasCases = (data?.total ?? 0) > 0;

  return (
    <section className="subsection ch-panel">
      <h3 className="ch-heading">
        <History size={13} />
        Customer Case History
        {loading
          ? <span className="ch-badge loading">…</span>
          : hasCases
            ? <span className="ch-badge has">{data!.total}</span>
            : <span className="ch-badge none">First contact</span>
        }
        {hasCases && (
          <button className="ch-toggle" onClick={() => setOpen(o => !o)}>
            {open ? "Hide" : "Show"} <ChevronDown size={11} style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform .15s" }} />
          </button>
        )}
      </h3>

      {!loading && !hasCases && (
        <p className="ch-empty">No prior cases found for this sender — this is their first interaction.</p>
      )}

      {hasCases && open && (
        <div className="ch-list">
          {data!.cases.map(c => (
            <div key={c.case_id} className={`ch-row state-${(c.workflow_state || "").toLowerCase().replace(/_/g, "-")}`}>
              <div className="ch-row-top">
                <span className={`priority ${(c.priority || "low").toLowerCase()}`}>{c.priority}</span>
                <span className="ch-case-id">{c.case_id}</span>
                <span className={`state-badge state-${(c.workflow_state || "").toLowerCase().replace(/_/g, "-")}`}>
                  {(c.workflow_state || "").replace(/_/g, " ")}
                </span>
                <span className="ch-date">{c.created_at ? new Date(c.created_at).toLocaleDateString("en-IN") : ""}</span>
              </div>
              <div className="ch-row-cls">{(c.classification || "").replace(/_/g, " ")}</div>
              {c.summary && <div className="ch-row-summary">{c.summary}</div>}
              {c.resolution_text && (
                <div className="ch-row-resolution">
                  <span className="ch-resolved-label">Resolved: </span>
                  {c.resolution_text.slice(0, 180)}{c.resolution_text.length > 180 ? "…" : ""}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// ─── Similar Cases Insight Panel ─────────────────────────────────────────────
function SimilarCasesInsightPanel({ solutions, synthesis }: {
  solutions: any[];
  synthesis?: CaseDetail["solutions_synthesis"];
}) {
  const [open, setOpen] = useState(false);
  if (!solutions.length && !synthesis) return null;

  return (
    <section className="subsection sci-panel">
      <h3 className="sci-heading">
        <GitCompareArrows size={13} />
        Similar Past Cases
        <span className="sci-badge">{solutions.length}</span>
        <button className="ch-toggle" onClick={() => setOpen(o => !o)}>
          {open ? "Hide" : "Show"} <ChevronDown size={11} style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform .15s" }} />
        </button>
      </h3>

      {synthesis && (
        <div className="sci-synthesis">
          <p className="sci-headline">{synthesis.headline}</p>
          <div className="sci-stats">
            {synthesis.top_classifications.map(tc => (
              <span key={tc.label} className="sci-stat-chip sci-chip-cls">
                {tc.label.replace(/_/g, " ")} <strong>{tc.count}×</strong>
              </span>
            ))}
            {synthesis.top_actions.map(ta => (
              <span key={ta.label} className="sci-stat-chip sci-chip-act">
                {ta.label} <strong>{ta.count}×</strong>
              </span>
            ))}
            {synthesis.top_department && (
              <span className="sci-stat-chip sci-chip-dept">→ {synthesis.top_department}</span>
            )}
          </div>
        </div>
      )}

      {open && solutions.length > 0 && (
        <div className="sci-list">
          {solutions.map((s, i) => (
            <div key={s.case_id ?? i} className="sci-row">
              <div className="sci-row-top">
                <span className="sci-match">{Math.round(s.similarity * 100)}% match</span>
                <span className="sci-cls">{(s.classification || "").replace(/_/g, " ")}</span>
                <span className="sci-case-id">{s.case_id}</span>
                {s.created_at && <span className="sci-date">{new Date(s.created_at).toLocaleDateString("en-IN")}</span>}
              </div>
              {s.summary && <div className="sci-row-summary">{s.summary}</div>}
              {s.resolution_text && (
                <div className="sci-row-resolution">
                  <span className="ch-resolved-label">Resolution: </span>
                  {s.resolution_text.slice(0, 200)}{s.resolution_text.length > 200 ? "…" : ""}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// ─── Panel + Layout helpers ───────────────────────────────────────────────────
function PanelTitle({ title, icon }: { title: string; icon: ReactNode }) {
  return (
    <div className="panel-title">
      {icon}<h2>{title}</h2>
    </div>
  );
}

function MetricGrid({ items }: { items: [string, any, string?][] }) {
  return (
    <div className="metric-grid">
      {items.map(([label, value, variant]) => (
        <div key={label} className={`metric${variant ? ` metric-${variant}` : ""}`}>
          <span>{label}</span>
          <strong>{fmt(value)}</strong>
        </div>
      ))}
    </div>
  );
}

function Info({ label, value }: { label: string; value: any }) {
  return (
    <div className="info">
      <span>{label}</span>
      <strong>{fmt(value)}</strong>
    </div>
  );
}

// ─── Data Table ───────────────────────────────────────────────────────────────
type Col = { key: string; label: string; render?: (v: unknown, row: any) => ReactNode };
function DataTable({ rows, columns }: { rows: any[]; columns: Col[] }) {
  if (!rows.length) return <div className="empty">No records.</div>;
  return (
    <div className="data-table">
      <div className="dt-head">
        {columns.map(c => <span key={c.key}>{c.label}</span>)}
      </div>
      {rows.map((row, i) => (
        <div key={i} className="dt-row">
          {columns.map(c => (
            <span key={c.key}>{c.render ? c.render(row[c.key], row) : fmt(row[c.key])}</span>
          ))}
        </div>
      ))}
    </div>
  );
}

import {
  Activity,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Clock,
  Users,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "./api";

// ─── Colour palettes ──────────────────────────────────────────────────────────
const CLASSIFICATION_COLORS: Record<string, string> = {
  UNAUTHORISED_USE: "#ef4444",
  CUSTOMER_GRIEVANCE: "#3b82f6",
  CONNECTION_FAULT: "#eab308",
  PAYMENT_FAILURE: "#ec4899",
  PORTAL_ACCESS: "#8b5cf6",
  VERIFICATION_QUERY: "#06b6d4",
  REGULATORY: "#14b8a6",
  LEGAL_NOTICE: "#dc2626",
  ESCALATION: "#f59e0b",
  SPAM: "#9ca3af",
  GENERAL_QUERY: "#60a5fa",
  DUPLICATE: "#84cc16",
};

const PRIORITY_COLORS: Record<string, string> = {
  CRITICAL: "#ef4444",
  HIGH: "#f97316",
  MEDIUM: "#eab308",
  LOW: "#22c55e",
};

const WORKFLOW_COLORS: Record<string, string> = {
  NEW: "#3b82f6",
  ASSIGNED: "#8b5cf6",
  ACKNOWLEDGED: "#06b6d4",
  UNDER_REVIEW: "#eab308",
  WAITING_FOR_ACTION: "#f97316",
  ESCALATED: "#ef4444",
  MULTI_DEPARTMENT_REVIEW: "#ec4899",
  RESOLVED: "#22c55e",
  CLOSED: "#9ca3af",
  REOPENED: "#f59e0b",
};

const TOOLTIP_STYLE = {
  background: "var(--surface)",
  border: "1px solid var(--line)",
  borderRadius: 6,
  fontSize: 11,
  color: "var(--ink)",
};
const TOOLTIP_LABEL_STYLE = { color: "var(--ink)", fontSize: 11 };
const TICK_STYLE = { fontSize: 10, fill: "var(--muted)" };

// ─── Layout helpers ───────────────────────────────────────────────────────────
function Grid({ cols = 2, children }: { cols?: number; children: React.ReactNode }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 12 }}>
      {children}
    </div>
  );
}

function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{
      background: "var(--surface)",
      border: "1px solid var(--line)",
      borderRadius: "var(--r-lg)",
      padding: "14px 16px",
      ...style,
    }}>
      {children}
    </div>
  );
}

function CardTitle({ children }: { children: React.ReactNode }) {
  return (
    <p style={{ fontSize: 11.5, fontWeight: 600, color: "var(--ink-mid)", marginBottom: 12, marginTop: 0 }}>
      {children}
    </p>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p style={{
      fontSize: 10, fontWeight: 700, textTransform: "uppercase",
      letterSpacing: ".06em", color: "var(--muted)", margin: "20px 0 8px",
    }}>
      {children}
    </p>
  );
}

function StatCard({ icon: Icon, label, value, sub, accent = "var(--brand)" }: {
  icon: React.ElementType; label: string; value: string | number; sub?: string; accent?: string;
}) {
  return (
    <Card style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
      <div style={{ padding: 8, borderRadius: 8, background: `${accent}18`, color: accent, flexShrink: 0 }}>
        <Icon size={16} />
      </div>
      <div style={{ minWidth: 0 }}>
        <p style={{ fontSize: 9.5, color: "var(--muted)", margin: 0 }}>{label}</p>
        <p style={{ fontSize: 22, fontWeight: 700, color: "var(--ink)", margin: "2px 0 0", lineHeight: 1.1 }}>{value}</p>
        {sub && <p style={{ fontSize: 9.5, color: "var(--faint)", margin: "2px 0 0" }}>{sub}</p>}
      </div>
    </Card>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────
export default function DeptAnalytics({ deptSlug, deptLabel }: { deptSlug: string; deptLabel: string }) {
  const [stats, setStats] = useState<any>(null);
  const [deepDive, setDeepDive] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const [s, dd] = await Promise.all([
          api.departmentStats(deptSlug),
          api.analyticsDepartmentDeepDive(),
        ]);
        if (!cancelled) {
          setStats(s);
          const match = (dd as any[]).find(
            (d: any) => d.department === deptLabel || d.department?.toLowerCase() === deptLabel.toLowerCase()
          ) ?? null;
          setDeepDive(match);
        }
      } catch (e: any) {
        if (!cancelled) setError(e.message ?? "Failed to load analytics");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [deptSlug, deptLabel]);

  if (loading) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: 200, gap: 10, color: "var(--muted)", fontSize: 13 }}>
        <Activity size={18} style={{ opacity: 0.6 }} /> Loading analytics…
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--danger)", padding: 24, fontSize: 13 }}>
        <AlertTriangle size={16} /> {error}
      </div>
    );
  }

  // ── Derived data ──────────────────────────────────────────────────────────

  // classification_breakdown is {LABEL: count} from stats, or [{label, count}] from deep dive
  const classData: { name: string; raw: string; count: number }[] = (() => {
    const cb = stats?.classification_breakdown;
    if (cb && typeof cb === "object" && !Array.isArray(cb)) {
      return Object.entries(cb as Record<string, number>)
        .map(([label, count]) => ({ name: label.replace(/_/g, " "), raw: label, count }));
    }
    const fallback: any[] = deepDive?.classifications ?? [];
    return fallback.map((c: any) => ({ name: (c.label as string).replace(/_/g, " "), raw: c.label as string, count: c.count as number }));
  })().sort((a, b) => b.count - a.count);

  // Priority breakdown
  const priBreakdown: Record<string, number> = deepDive?.priority_breakdown ?? {};
  const priTotal = Object.values(priBreakdown).reduce((s: number, n: any) => s + (n as number), 0) || 1;

  // SLA health (overdue, nearing_breach, healthy)
  const slaHealth = stats?.sla_health ?? {};
  const slaHealthy = Math.max(0, slaHealth.healthy ?? 0);
  const slaNearing = Math.max(0, slaHealth.nearing_breach ?? 0);
  const slaOverdue = Math.max(0, slaHealth.overdue ?? 0);
  const slaTotal = slaHealthy + slaNearing + slaOverdue || 1;

  // workflow_states is {STATE: count}
  const workflowData: { state: string; count: number }[] = Object.entries(
    stats?.workflow_states ?? {}
  ).map(([state, count]) => ({ state: state.replace(/_/g, " "), count: count as number }))
    .filter((d) => d.count > 0)
    .sort((a, b) => b.count - a.count);

  // operator_workloads is {name: {cases, open, escalated}} or {}
  const operators: { operator: string; cases: number; open: number; escalated: number }[] = (() => {
    const ow = stats?.operator_workloads;
    if (!ow || typeof ow !== "object" || Array.isArray(ow)) return [];
    return Object.entries(ow as Record<string, any>).map(([name, v]: [string, any]) => ({
      operator: name,
      cases: v.cases ?? 0,
      open: v.open ?? 0,
      escalated: v.escalated ?? 0,
    }));
  })();

  // KPIs
  const total = deepDive?.total ?? stats?.total_cases ?? 0;
  const open = deepDive?.open ?? 0;
  const resolved = deepDive?.resolved ?? 0;
  const resolutionRate = deepDive?.resolution_rate ?? 0;
  const avgResHours = deepDive?.avg_resolution_hours;

  // Recent resolutions
  const recentResolutions: any[] = deepDive?.recent_resolutions ?? [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>

      {/* ── KPI strip ── */}
      <SectionLabel>Overview</SectionLabel>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10, marginBottom: 4 }}>
        <StatCard icon={BarChart3} label="Total Cases" value={total} accent="var(--brand)" />
        <StatCard icon={Activity} label="Open" value={open} sub={`${resolved} resolved`} accent="#eab308" />
        <StatCard icon={CheckCircle2} label="Resolution Rate" value={`${resolutionRate}%`} accent={resolutionRate >= 70 ? "#22c55e" : resolutionRate >= 40 ? "#eab308" : "#ef4444"} />
        <StatCard icon={Clock} label="Avg Resolution" value={avgResHours != null ? `${avgResHours}h` : "—"} accent="#8b5cf6" />
        <StatCard icon={AlertTriangle} label="SLA Overdue" value={slaOverdue} sub={slaNearing ? `${slaNearing} nearing breach` : undefined} accent={slaOverdue > 0 ? "#ef4444" : "#22c55e"} />
        <StatCard icon={Users} label="Operators" value={operators.length || "—"} sub={operators.length ? `${operators.reduce((s: number, o: any) => s + (o.open ?? 0), 0)} open cases` : undefined} accent="#06b6d4" />
      </div>

      {/* ── Classification + Priority ── */}
      <SectionLabel>Issues & Priority</SectionLabel>
      <Grid cols={2}>
        <Card>
          <CardTitle>Classification Breakdown</CardTitle>
          {classData.length > 0 ? (
            <ResponsiveContainer width="100%" height={210}>
              <BarChart data={classData} margin={{ top: 4, right: 8, bottom: 50, left: -20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
                <XAxis dataKey="name" tick={{ fontSize: 8.5, fill: "var(--muted)" }} angle={-30} textAnchor="end" interval={0} />
                <YAxis tick={TICK_STYLE} />
                <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} />
                <Bar dataKey="count" name="Cases" radius={[3, 3, 0, 0]}>
                  {classData.map((entry) => (
                    <Cell key={entry.raw} fill={CLASSIFICATION_COLORS[entry.raw] ?? "var(--brand-muted)"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div style={{ height: 210, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--faint)", fontSize: 12 }}>
              No data yet
            </div>
          )}
        </Card>

        <Card>
          <CardTitle>Priority Breakdown</CardTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 14, marginTop: 6 }}>
            {["CRITICAL", "HIGH", "MEDIUM", "LOW"].map((p) => {
              const n = (priBreakdown[p] ?? 0) as number;
              const pct = Math.round((n / priTotal) * 100);
              const color = PRIORITY_COLORS[p];
              return (
                <div key={p}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <span style={{ fontSize: 11.5, fontWeight: 600, color }}>{p}</span>
                    <span style={{ fontSize: 11.5, color: "var(--ink)", fontWeight: 600 }}>
                      {n} <span style={{ fontSize: 10, color: "var(--faint)" }}>({pct}%)</span>
                    </span>
                  </div>
                  <div style={{ width: "100%", background: "var(--surface-3)", borderRadius: 4, height: 7 }}>
                    <div style={{ width: `${pct}%`, background: color, height: 7, borderRadius: 4, transition: "width .4s" }} />
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      </Grid>

      {/* ── SLA Health + Workflow State ── */}
      <SectionLabel>SLA Health & Workflow</SectionLabel>
      <Grid cols={2}>
        <Card>
          <CardTitle>SLA Health</CardTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 14, marginTop: 8 }}>
            {[
              { label: "Healthy", value: slaHealthy, color: "#22c55e", Icon: CheckCircle2 },
              { label: "Nearing Breach", value: slaNearing, color: "#eab308", Icon: Clock },
              { label: "Overdue", value: slaOverdue, color: "#ef4444", Icon: AlertTriangle },
            ].map(({ label, value, color, Icon }) => {
              const pct = Math.round((value / slaTotal) * 100);
              return (
                <div key={label}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
                    <span style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, color }}>
                      <Icon size={12} /> {label}
                    </span>
                    <span style={{ fontSize: 12, color: "var(--ink)", fontWeight: 600 }}>
                      {value} <span style={{ fontSize: 10, color: "var(--faint)" }}>({pct}%)</span>
                    </span>
                  </div>
                  <div style={{ width: "100%", background: "var(--surface-3)", borderRadius: 4, height: 6 }}>
                    <div style={{ width: `${pct}%`, background: color, height: 6, borderRadius: 4, transition: "width .4s" }} />
                  </div>
                </div>
              );
            })}
          </div>
        </Card>

        <Card>
          <CardTitle>Workflow State Distribution</CardTitle>
          {workflowData.length > 0 ? (
            <ResponsiveContainer width="100%" height={190}>
              <BarChart data={workflowData} layout="vertical" margin={{ top: 4, right: 8, bottom: 4, left: 120 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
                <XAxis type="number" tick={TICK_STYLE} />
                <YAxis type="category" dataKey="state" tick={{ fontSize: 9.5, fill: "var(--muted)" }} width={120} />
                <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} />
                <Bar dataKey="count" radius={[0, 3, 3, 0]}>
                  {workflowData.map((entry) => (
                    <Cell key={entry.state} fill={WORKFLOW_COLORS[entry.state.replace(/ /g, "_")] ?? "var(--brand-muted)"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div style={{ height: 190, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--faint)", fontSize: 12 }}>
              No workflow data yet
            </div>
          )}
        </Card>
      </Grid>

      {/* ── Operator Workloads ── */}
      {operators.length > 0 && (
        <>
          <SectionLabel>Operator Workloads</SectionLabel>
          <Card>
            <CardTitle>Assigned Operators</CardTitle>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11.5 }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--line)" }}>
                    {["Operator", "Total Cases", "Open", "Escalated"].map((h) => (
                      <th key={h} style={{
                        textAlign: "left", fontSize: 9.5, color: "var(--muted)",
                        padding: "6px 12px 6px 0", fontWeight: 600,
                        textTransform: "uppercase", letterSpacing: ".04em",
                      }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {operators.map((op: any) => (
                    <tr key={op.operator} style={{ borderBottom: "1px solid var(--line)" }}>
                      <td style={{ padding: "7px 12px 7px 0", color: "var(--ink)", fontWeight: 500 }}>{op.operator}</td>
                      <td style={{ padding: "7px 12px 7px 0", color: "var(--ink-mid)" }}>{op.cases}</td>
                      <td style={{ padding: "7px 12px 7px 0", color: "#eab308", fontWeight: 600 }}>{op.open}</td>
                      <td style={{ padding: "7px 12px 7px 0", color: op.escalated > 0 ? "#ef4444" : "var(--faint)", fontWeight: op.escalated > 0 ? 700 : 400 }}>
                        {op.escalated}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}

      {/* ── Recent Resolutions ── */}
      {recentResolutions.length > 0 && (
        <>
          <SectionLabel>Recent Resolutions</SectionLabel>
          <Card>
            <CardTitle>How Cases Were Resolved</CardTitle>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {recentResolutions.map((r: any) => (
                <div key={r.case_id} style={{
                  padding: "10px 12px",
                  background: "var(--surface-2)",
                  borderRadius: "var(--r)",
                  border: "1px solid var(--line)",
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5 }}>
                    <span style={{
                      fontSize: 10.5, fontWeight: 700,
                      color: CLASSIFICATION_COLORS[r.classification] ?? "var(--brand)",
                      padding: "1px 7px",
                      background: "var(--surface)",
                      border: `1px solid ${CLASSIFICATION_COLORS[r.classification] ?? "var(--brand)"}`,
                      borderRadius: 10,
                    }}>
                      {(r.classification as string).replace(/_/g, " ")}
                    </span>
                    <span style={{ fontSize: 10, fontWeight: 700, color: PRIORITY_COLORS[r.priority] ?? "var(--ink-mid)" }}>
                      {r.priority}
                    </span>
                    <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--faint)" }}>
                      {r.resolved_at ? new Date(r.resolved_at).toLocaleDateString() : ""}
                    </span>
                  </div>
                  <p style={{ fontSize: 11.5, color: "var(--ink-mid)", margin: 0, lineHeight: 1.55 }}>
                    {r.resolution_text || <em style={{ color: "var(--faint)" }}>No resolution text</em>}
                  </p>
                </div>
              ))}
            </div>
          </Card>
        </>
      )}

      <div style={{ height: 16 }} />
    </div>
  );
}

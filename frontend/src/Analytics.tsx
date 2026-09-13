import {
  Activity,
  AlertTriangle,
  BarChart3,
  Brain,
  CheckCircle2,
  Clock,
  ShieldAlert,
  TrendingDown,
  Users,
  Zap,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "./api";

// ─── Desk registry ─────────────────────────────────────────────────────────────
const DESKS = [
  { slug: "trust-safety-desk",            label: "Trust & Safety Desk",           short: "Trust",     color: "#B91C1C" },
  { slug: "customer-resolution-desk",      label: "Customer Resolution Desk",     short: "Resolution",     color: "#1D4ED8" },
  { slug: "compliance-desk",            label: "Compliance Desk",           short: "Compliance",       color: "#6D28D9" },
  { slug: "network-support-desk",label: "Network Support Desk",short: "Network",       color: "#047857" },
  { slug: "central-operations-desk",    label: "Central Operations Desk",   short: "Central Ops",      color: "#B45309" },
  { slug: "billing-contracts-desk",                   label: "Billing & Contracts Desk",                  short: "Billing",            color: "#0E7490" },
  { slug: "roaming-international-desk",                      label: "Roaming & International Desk",                       short: "Roaming",      color: "#7C3AED" },
  { slug: "equipment-provisioning-desk",                 label: "Equipment & Provisioning Desk",                  short: "Equipment",            color: "#BE185D" },
] as const;

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

const SENTIMENT_COLORS: Record<string, string> = {
  POSITIVE: "#22c55e",
  NEGATIVE: "#ef4444",
  NEUTRAL: "#9ca3af",
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
      background: "var(--surface)", border: "1px solid var(--line)",
      borderRadius: "var(--r-lg)", padding: "14px 16px", ...style,
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
      letterSpacing: ".06em", color: "var(--muted)", margin: "24px 0 8px",
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

// ─── Department status card ────────────────────────────────────────────────────
function DeptCard({
  desk, data, stats,
}: {
  desk: typeof DESKS[number];
  data: any | null;  // deep-dive entry
  stats: any | null; // departmentStats entry
}) {
  const total = data?.total ?? 0;
  const open = data?.open ?? 0;
  const resolved = data?.resolved ?? 0;
  const rate = data?.resolution_rate ?? 0;
  const critical = data?.priority_breakdown_open?.CRITICAL ?? 0;
  const overdue = Math.max(0, stats?.sla_health?.overdue ?? 0);
  const avgHours = data?.avg_resolution_hours;

  const rateColor = rate >= 70 ? "#22c55e" : rate >= 40 ? "#eab308" : "#ef4444";

  return (
    <div style={{
      background: "var(--surface)",
      border: "1px solid var(--line)",
      borderTop: `3px solid ${desk.color}`,
      borderRadius: "var(--r-lg)",
      padding: "12px 14px",
      display: "flex",
      flexDirection: "column",
      gap: 8,
    }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontSize: 11.5, fontWeight: 700, color: desk.color, lineHeight: 1.3 }}>
          {desk.short}
        </span>
        <div style={{ display: "flex", gap: 4 }}>
          {critical > 0 && (
            <span style={{
              fontSize: 9.5, fontWeight: 700, padding: "1px 6px",
              background: "#fef2f2", color: "#ef4444",
              border: "1px solid #fca5a5", borderRadius: 10,
            }}>
              {critical} CRIT
            </span>
          )}
          {overdue > 0 && (
            <span style={{
              fontSize: 9.5, fontWeight: 700, padding: "1px 6px",
              background: "#fffbeb", color: "#b45309",
              border: "1px solid #fcd34d", borderRadius: 10,
            }}>
              {overdue} OVR
            </span>
          )}
        </div>
      </div>

      {/* Count row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 4 }}>
        {[
          { label: "Total", value: total, color: "var(--ink)" },
          { label: "Open", value: open, color: "#eab308" },
          { label: "Resolved", value: resolved, color: "#22c55e" },
        ].map(({ label, value, color }) => (
          <div key={label} style={{ textAlign: "center" }}>
            <div style={{ fontSize: 18, fontWeight: 700, color, lineHeight: 1 }}>{value}</div>
            <div style={{ fontSize: 9, color: "var(--muted)", marginTop: 2, textTransform: "uppercase", letterSpacing: ".04em" }}>{label}</div>
          </div>
        ))}
      </div>

      {/* Resolution rate bar */}
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
          <span style={{ fontSize: 9.5, color: "var(--muted)" }}>Resolution rate</span>
          <span style={{ fontSize: 9.5, fontWeight: 700, color: rateColor }}>{rate}%</span>
        </div>
        <div style={{ height: 5, background: "var(--surface-3)", borderRadius: 3 }}>
          <div style={{ width: `${rate}%`, height: 5, background: rateColor, borderRadius: 3, transition: "width .4s" }} />
        </div>
      </div>

      {/* Avg resolution */}
      {avgHours != null && (
        <div style={{ fontSize: 9.5, color: "var(--faint)" }}>
          Avg resolution: <span style={{ color: "var(--ink-mid)", fontWeight: 600 }}>{avgHours}h</span>
        </div>
      )}
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────
export default function Analytics() {
  const [volumeTrend, setVolumeTrend] = useState<any[]>([]);
  const [heatmap, setHeatmap] = useState<any>(null);
  const [resolutionTimes, setResolutionTimes] = useState<any>(null);
  const [slaBreach, setSlaBreach] = useState<any>(null);
  const [modelPerf, setModelPerf] = useState<any>(null);
  const [fraudTrend, setFraudTrend] = useState<any[]>([]);
  const [deptPerf, setDeptPerf] = useState<any[]>([]);
  const [deptDeepDive, setDeptDeepDive] = useState<any[]>([]);
  const [deptStats, setDeptStats] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const [vt, hm, rt, sla, mp, ft, dp, dd] = await Promise.all([
          api.analyticsVolumeTrend(14),
          api.analyticsClassificationHeatmap(),
          api.analyticsResolutionTimes(),
          api.analyticsSlaBreachRates(),
          api.analyticsModelPerformance(),
          api.analyticsFraudTrend(14),
          api.analyticsDepartmentPerformance(),
          api.analyticsDepartmentDeepDive(),
        ]);
        setVolumeTrend(vt);
        setHeatmap(hm);
        setResolutionTimes(rt);
        setSlaBreach(sla);
        setModelPerf(mp);
        setFraudTrend(ft);
        setDeptPerf(dp);
        setDeptDeepDive(dd);

        // Fetch per-desk stats in parallel (SLA health, workflow states)
        const statsEntries = await Promise.all(
          DESKS.map(async (d) => {
            try {
              const s = await api.departmentStats(d.slug);
              return [d.slug, s] as [string, any];
            } catch {
              return [d.slug, null] as [string, null];
            }
          })
        );
        setDeptStats(Object.fromEntries(statsEntries));
      } catch (e: any) {
        setError(e.message ?? "Failed to load analytics");
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  if (loading) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: 240, gap: 10, color: "var(--muted)", fontSize: 13 }}>
        <Activity size={18} style={{ opacity: 0.6 }} />
        Loading analytics…
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--danger)", padding: 24, fontSize: 13 }}>
        <AlertTriangle size={16} />
        {error}
      </div>
    );
  }

  // ── Derived data ──────────────────────────────────────────────────────────

  const slaBreachSummary = slaBreach?.summary ?? {};
  const mpFeedback = modelPerf?.feedback ?? {};
  const slaTotal = Math.max(
    1,
    (slaBreachSummary.healthy ?? 0) + (slaBreachSummary.nearing_breach ?? 0) + (slaBreachSummary.breached ?? 0)
  );

  const volumeKeys = Array.from(
    new Set(volumeTrend.flatMap((d) => Object.keys(d).filter((k) => k !== "date" && k !== "TOTAL")))
  ).slice(0, 6);

  const classDistribution = heatmap
    ? Object.entries(heatmap.matrix as Record<string, Record<string, number>>).map(
        ([classification, depts]) => ({
          name: classification.replace(/_/g, " "),
          raw: classification,
          value: Object.values(depts).reduce((a, b) => a + b, 0),
        })
      ).sort((a, b) => b.value - a.value)
    : [];

  const confDist = modelPerf
    ? Object.entries(modelPerf.confidence_distribution as Record<string, number>).map(
        ([bucket, count]) => ({ bucket, count })
      )
    : [];

  const adjModes = modelPerf
    ? Object.entries(modelPerf.adjudication_modes as Record<string, number>).map(
        ([mode, count]) => ({ mode: mode.replace(/_/g, " "), count })
      )
    : [];

  const sentimentDist = modelPerf
    ? Object.entries(modelPerf.sentiment_distribution as Record<string, number>).map(
        ([s, count]) => ({ name: s, value: count })
      )
    : [];
  // Sentiment is only recorded for a subset of cases — surface that coverage so a
  // single-category result (e.g. all NEGATIVE) is not read as "every customer is angry".
  const sentimentAnalyzed = sentimentDist.reduce((s, d) => s + d.value, 0);
  const sentimentTotal = modelPerf?.total_cases ?? 0;

  const resolutionByPriority = resolutionTimes
    ? Object.entries(
        resolutionTimes.by_priority as Record<string, { avg: number; count: number }>
      ).map(([priority, stats]) => ({
        priority,
        avg_hours: Math.round(stats.avg / 60),
        count: stats.count,
      }))
    : [];

  // Per-dept comparison data for charts
  const deptCompare = DESKS.map((d) => {
    const perf = deptPerf.find((p: any) => p.department === d.label);
    const dive = deptDeepDive.find((p: any) => p.department === d.label);
    const stats = deptStats[d.slug];
    return {
      short: d.short,
      color: d.color,
      total: perf?.total ?? dive?.total ?? 0,
      open: perf?.open ?? dive?.open ?? 0,
      resolved: perf?.resolved ?? dive?.resolved ?? 0,
      resolution_rate: perf?.resolution_rate ?? dive?.resolution_rate ?? 0,
      avg_conf: Math.round((perf?.avg_confidence ?? 0) * 100),
      critical: dive?.critical_open ?? 0,
      overdue: Math.max(0, stats?.sla_health?.overdue ?? 0),
      nearing: Math.max(0, stats?.sla_health?.nearing_breach ?? 0),
      healthy: Math.max(0, stats?.sla_health?.healthy ?? 0),
    };
  });

  // Total active cases across all depts
  const totalActive = deptCompare.reduce((s, d) => s + d.open, 0);
  const totalCritical = deptCompare.reduce((s, d) => s + d.critical, 0);
  const totalOverdue = deptCompare.reduce((s, d) => s + d.overdue, 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>

      {/* ── Platform health KPIs ── */}
      <SectionLabel>Platform Health</SectionLabel>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginBottom: 4 }}>
        <StatCard icon={BarChart3}    label="Total Cases"      value={modelPerf?.total_cases ?? 0}                         accent="var(--brand)" />
        <StatCard icon={Zap}          label="Auto-routed"      value={`${modelPerf?.auto_route_rate ?? 0}%`}               sub={`${modelPerf?.auto_routed_count ?? 0} cases`} accent="#22c55e" />
        <StatCard icon={Brain}        label="Avg AI Confidence" value={`${((modelPerf?.average_confidence ?? 0) * 100).toFixed(1)}%`} accent="#8b5cf6" />
        <StatCard icon={AlertTriangle} label="SLA Breached"    value={slaBreachSummary.breached ?? 0}                      sub={`${slaBreachSummary.breach_rate ?? 0}% breach rate`} accent="#ef4444" />
        <StatCard icon={Activity}     label="Active Cases"     value={totalActive}                                         sub="across all desks" accent="#eab308" />
        <StatCard icon={ShieldAlert}  label="Critical Open"    value={totalCritical}                                       sub="needs immediate action" accent="#f97316" />
        <StatCard icon={Clock}        label="SLA Overdue"      value={totalOverdue}                                        sub="across all desks" accent={totalOverdue > 0 ? "#ef4444" : "#22c55e"} />
        <StatCard icon={Users}        label="Human Overrides"  value={mpFeedback.corrections ?? 0}                        sub={`${mpFeedback.correction_rate ?? 0}% correction rate`} accent="#06b6d4" />
      </div>

      {/* ── Department status cards ── */}
      <SectionLabel>Department Status — All 8 Desks</SectionLabel>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
        {DESKS.map((desk) => (
          <DeptCard
            key={desk.slug}
            desk={desk}
            data={deptDeepDive.find((d: any) => d.department === desk.label) ?? null}
            stats={deptStats[desk.slug] ?? null}
          />
        ))}
      </div>

      {/* ── Department comparison ── */}
      <SectionLabel>Department Comparison</SectionLabel>
      <Grid cols={2}>
        <Card>
          <CardTitle>Cases by Department (Total vs Open)</CardTitle>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={deptCompare} margin={{ top: 4, right: 8, bottom: 60, left: -20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis dataKey="short" tick={{ fontSize: 8.5, fill: "var(--muted)" }} angle={-35} textAnchor="end" interval={0} />
              <YAxis tick={TICK_STYLE} />
              <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} itemStyle={{ fontSize: 11 }} />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              <Bar dataKey="total" name="Total" radius={[3, 3, 0, 0]} fill="var(--brand-muted)" />
              <Bar dataKey="open" name="Open" radius={[3, 3, 0, 0]}>
                {deptCompare.map((d) => <Cell key={d.short} fill={d.color} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>

        <Card>
          <CardTitle>Resolution Rate by Department (%)</CardTitle>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={[...deptCompare].sort((a, b) => b.resolution_rate - a.resolution_rate)} margin={{ top: 4, right: 8, bottom: 60, left: -20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis dataKey="short" tick={{ fontSize: 8.5, fill: "var(--muted)" }} angle={-35} textAnchor="end" interval={0} />
              <YAxis tick={TICK_STYLE} domain={[0, 100]} />
              <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} itemStyle={{ fontSize: 11 }} formatter={(v: any) => [`${v}%`, "Resolution Rate"]} />
              <Bar dataKey="resolution_rate" name="Rate %" radius={[3, 3, 0, 0]}>
                {[...deptCompare].sort((a, b) => b.resolution_rate - a.resolution_rate).map((d) => (
                  <Cell key={d.short} fill={d.resolution_rate >= 70 ? "#22c55e" : d.resolution_rate >= 40 ? "#eab308" : "#ef4444"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>
      </Grid>

      {/* ── SLA by department ── */}
      <SectionLabel>SLA Health by Department</SectionLabel>
      <Card>
        <CardTitle>Overdue · Nearing Breach · Healthy — all desks</CardTitle>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {deptCompare.map((d) => {
            const tot = d.overdue + d.nearing + d.healthy || 1;
            return (
              <div key={d.short}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
                  <span style={{ fontSize: 11, fontWeight: 600, color: d.color }}>{d.short}</span>
                  <span style={{ display: "flex", gap: 12, fontSize: 10 }}>
                    <span style={{ color: "#ef4444" }}>Overdue: <strong>{d.overdue}</strong></span>
                    <span style={{ color: "#eab308" }}>Nearing: <strong>{d.nearing}</strong></span>
                    <span style={{ color: "#22c55e" }}>Healthy: <strong>{d.healthy}</strong></span>
                  </span>
                </div>
                <div style={{ display: "flex", height: 8, borderRadius: 4, overflow: "hidden", background: "var(--surface-3)" }}>
                  {d.overdue > 0 && <div style={{ width: `${(d.overdue / tot) * 100}%`, background: "#ef4444" }} />}
                  {d.nearing > 0 && <div style={{ width: `${(d.nearing / tot) * 100}%`, background: "#eab308" }} />}
                  {d.healthy > 0 && <div style={{ width: `${(d.healthy / tot) * 100}%`, background: "#22c55e" }} />}
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      {/* ── Priority breakdown across all depts ── */}
      <SectionLabel>Critical & Priority Breakdown by Department</SectionLabel>
      <Card>
        <CardTitle>Open cases by priority per desk</CardTitle>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart
            data={DESKS.map((desk) => {
              const dive = deptDeepDive.find((d: any) => d.department === desk.label);
              const pbo = dive?.priority_breakdown_open ?? {};
              return {
                short: desk.short,
                CRITICAL: pbo.CRITICAL ?? 0,
                HIGH: pbo.HIGH ?? 0,
                MEDIUM: pbo.MEDIUM ?? 0,
                LOW: pbo.LOW ?? 0,
              };
            })}
            margin={{ top: 4, right: 8, bottom: 60, left: -20 }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
            <XAxis dataKey="short" tick={{ fontSize: 8.5, fill: "var(--muted)" }} angle={-35} textAnchor="end" interval={0} />
            <YAxis tick={TICK_STYLE} />
            <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} itemStyle={{ fontSize: 11 }} />
            <Legend wrapperStyle={{ fontSize: 10 }} />
            <Bar dataKey="CRITICAL" stackId="a" fill={PRIORITY_COLORS.CRITICAL} />
            <Bar dataKey="HIGH"     stackId="a" fill={PRIORITY_COLORS.HIGH} />
            <Bar dataKey="MEDIUM"   stackId="a" fill={PRIORITY_COLORS.MEDIUM} />
            <Bar dataKey="LOW"      stackId="a" fill={PRIORITY_COLORS.LOW} radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </Card>

      {/* ── Volume & fraud trends ── */}
      <SectionLabel>Volume & Risk Trends (14 days)</SectionLabel>
      <Grid cols={2}>
        <Card>
          <CardTitle>Email Volume by Classification</CardTitle>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={volumeTrend} margin={{ top: 4, right: 8, bottom: 4, left: -20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis dataKey="date" tick={TICK_STYLE} tickFormatter={(d) => d.slice(5)} />
              <YAxis tick={TICK_STYLE} />
              <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} itemStyle={{ fontSize: 11 }} />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              {volumeKeys.map((key) => (
                <Line key={key} type="monotone" dataKey={key} stroke={CLASSIFICATION_COLORS[key] ?? "var(--brand-muted)"} dot={false} strokeWidth={2} />
              ))}
              <Line type="monotone" dataKey="TOTAL" stroke="var(--brand)" dot={false} strokeWidth={2} strokeDasharray="4 2" />
            </LineChart>
          </ResponsiveContainer>
        </Card>

        <Card>
          <CardTitle>Fraud Risk Trend</CardTitle>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={fraudTrend} margin={{ top: 4, right: 8, bottom: 4, left: -20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis dataKey="date" tick={TICK_STYLE} tickFormatter={(d) => d.slice(5)} />
              <YAxis tick={TICK_STYLE} />
              <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} itemStyle={{ fontSize: 11 }} />
              <Legend wrapperStyle={{ fontSize: 10 }} />
              <Bar dataKey="count" fill="#ef4444" name="Cases" radius={[3, 3, 0, 0]} />
              <Bar dataKey="critical" fill="#b91c1c" name="Critical" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>
      </Grid>

      {/* ── Classification + Sentiment ── */}
      <SectionLabel>Issue Classification & Sentiment</SectionLabel>
      <Grid cols={2}>
        <Card>
          <CardTitle>Classification Distribution (all desks)</CardTitle>
          <ResponsiveContainer width="100%" height={210}>
            <BarChart data={classDistribution} margin={{ top: 4, right: 8, bottom: 48, left: -20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis dataKey="name" tick={{ fontSize: 8.5, fill: "var(--muted)" }} angle={-30} textAnchor="end" interval={0} />
              <YAxis tick={TICK_STYLE} />
              <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} />
              <Bar dataKey="value" radius={[3, 3, 0, 0]}>
                {classDistribution.map((entry) => (
                  <Cell key={entry.raw} fill={CLASSIFICATION_COLORS[entry.raw] ?? "var(--brand-muted)"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>

        <Card>
          <CardTitle>
            Sentiment Distribution
            {sentimentAnalyzed > 0 && (
              <span style={{ fontWeight: 400, color: "var(--faint)" }}>
                {" "}— {sentimentAnalyzed} of {sentimentTotal} cases with detected sentiment
              </span>
            )}
          </CardTitle>
          {sentimentDist.length > 0 ? (
            <ResponsiveContainer width="100%" height={210}>
              <PieChart>
                <Pie data={sentimentDist} cx="50%" cy="45%" outerRadius={75} dataKey="value"
                  label={({ name, value }: { name?: string; value?: number }) =>
                    `${name ?? ""}: ${value ?? 0}`
                  }
                  labelLine={false}
                >
                  {sentimentDist.map((entry) => (
                    <Cell key={entry.name} fill={SENTIMENT_COLORS[entry.name] ?? "var(--brand-muted)"} />
                  ))}
                </Pie>
                <Tooltip contentStyle={TOOLTIP_STYLE} itemStyle={{ fontSize: 11 }}
                  formatter={(v: any, n: any) => [`${v} cases`, n]} />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <div style={{ height: 210, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--very-muted)", fontSize: 12 }}>
              No sentiment data yet
            </div>
          )}
        </Card>
      </Grid>

      {/* ── Resolution & SLA ── */}
      <SectionLabel>Resolution & SLA Health</SectionLabel>
      <Grid cols={2}>
        <Card>
          <CardTitle>Avg Resolution Time by Priority (hours)</CardTitle>
          <ResponsiveContainer width="100%" height={190}>
            <BarChart data={resolutionByPriority} margin={{ top: 4, right: 8, bottom: 4, left: -20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis dataKey="priority" tick={TICK_STYLE} />
              <YAxis tick={TICK_STYLE} />
              <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} />
              <Bar dataKey="avg_hours" name="Avg hours" radius={[3, 3, 0, 0]}>
                {resolutionByPriority.map((entry) => (
                  <Cell key={entry.priority} fill={PRIORITY_COLORS[entry.priority] ?? "var(--brand-muted)"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>

        <Card>
          <CardTitle>Platform SLA Summary</CardTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 14, marginTop: 8 }}>
            {[
              { label: "Healthy", value: slaBreachSummary.healthy ?? 0, color: "#22c55e", Icon: CheckCircle2 },
              { label: "Nearing Breach", value: slaBreachSummary.nearing_breach ?? 0, color: "#eab308", Icon: Clock },
              { label: "Breached", value: slaBreachSummary.breached ?? 0, color: "#ef4444", Icon: AlertTriangle },
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
      </Grid>

      {/* ── AI model performance ── */}
      <SectionLabel>AI Pipeline Performance</SectionLabel>
      <Grid cols={2}>
        <Card>
          <CardTitle>Confidence Distribution</CardTitle>
          <ResponsiveContainer width="100%" height={190}>
            <BarChart data={confDist} margin={{ top: 4, right: 8, bottom: 4, left: -20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis dataKey="bucket" tick={TICK_STYLE} />
              <YAxis tick={TICK_STYLE} />
              <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} />
              <Bar dataKey="count" radius={[3, 3, 0, 0]}>
                {confDist.map((entry, i) => (
                  <Cell key={i} fill={
                    entry.bucket === "<0.65" ? "#ef4444" :
                    entry.bucket === "0.65–0.75" ? "#f97316" :
                    entry.bucket === "0.75–0.85" ? "#eab308" : "#22c55e"
                  } />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>

        <Card>
          <CardTitle>Adjudication Modes</CardTitle>
          <ResponsiveContainer width="100%" height={190}>
            <BarChart data={adjModes} layout="vertical" margin={{ top: 4, right: 8, bottom: 4, left: 90 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis type="number" tick={TICK_STYLE} />
              <YAxis type="category" dataKey="mode" tick={{ fontSize: 9.5, fill: "var(--muted)" }} width={90} />
              <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={TOOLTIP_LABEL_STYLE} />
              <Bar dataKey="count" fill="var(--brand-mid)" radius={[0, 3, 3, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>
      </Grid>

      {/* ── ML Feedback loop ── */}
      {modelPerf?.feedback && (
        <>
          <SectionLabel>ML Feedback Loop</SectionLabel>
          <Card>
            <p style={{ fontSize: 11.5, fontWeight: 600, color: "var(--ink-mid)", marginBottom: 12, marginTop: 0, display: "flex", alignItems: "center", gap: 6 }}>
              <Brain size={13} style={{ color: "#8b5cf6" }} /> Human Corrections to AI Routing
            </p>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginBottom: 16 }}>
              {[
                { label: "Total Feedback", value: mpFeedback.total_feedback ?? 0, color: "var(--brand)" },
                { label: "Corrections",    value: mpFeedback.corrections ?? 0,    color: "#ef4444" },
                { label: "Agreements",     value: mpFeedback.agreements ?? 0,     color: "#22c55e" },
                { label: "Correction Rate",value: `${mpFeedback.correction_rate ?? 0}%`, color: mpFeedback.correction_rate > 20 ? "#ef4444" : "#22c55e" },
              ].map(({ label, value, color }) => (
                <div key={label} style={{ textAlign: "center", padding: "10px 0", background: "var(--surface-2)", borderRadius: "var(--r)", border: "1px solid var(--line)" }}>
                  <p style={{ fontSize: 22, fontWeight: 700, color, margin: 0 }}>{value}</p>
                  <p style={{ fontSize: 9.5, color: "var(--muted)", margin: "3px 0 0", textTransform: "uppercase", letterSpacing: ".04em" }}>{label}</p>
                </div>
              ))}
            </div>
            {Object.keys(modelPerf.correction_matrix ?? {}).length > 0 && (
              <>
                <p style={{ fontSize: 10, color: "var(--muted)", marginBottom: 8, textTransform: "uppercase", letterSpacing: ".04em" }}>Top Correction Patterns</p>
                <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                  {Object.entries(modelPerf.correction_matrix as Record<string, Record<string, number>>)
                    .flatMap(([from, toMap]) => Object.entries(toMap).map(([to, count]) => ({ from, to, count })))
                    .sort((a, b) => b.count - a.count)
                    .slice(0, 5)
                    .map(({ from, to, count }) => (
                      <div key={`${from}→${to}`} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11, padding: "4px 8px", background: "var(--surface-2)", borderRadius: "var(--r)", border: "1px solid var(--line)" }}>
                        <span style={{ color: "#ef4444", fontWeight: 600 }}>{from.replace(/_/g, " ")}</span>
                        <TrendingDown size={11} style={{ color: "var(--faint)", flexShrink: 0 }} />
                        <span style={{ color: "#22c55e", fontWeight: 600 }}>{to.replace(/_/g, " ")}</span>
                        <span style={{ marginLeft: "auto", color: "var(--faint)", fontSize: 10 }}>×{count}</span>
                      </div>
                    ))}
                </div>
              </>
            )}
          </Card>
        </>
      )}

      <div style={{ height: 16 }} />
    </div>
  );
}

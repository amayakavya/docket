"""
Analytics service — all time-series, distribution, and performance metrics.

Designed to be cheap to call: pure SQL aggregations, no ML inference.
All timestamps stored as UTC; the API layer converts to ISO strings.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import AuditEvent, Case, CaseDepartment, MLFeedback


# ── helpers ─────────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _day_label(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _median(sorted_values: list[float]) -> float:
    """True median of a pre-sorted list — averages the two middle values for even n."""
    n = len(sorted_values)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2


def _resolution_minutes(case: Case) -> float | None:
    """
    Minutes from case creation to resolution.

    Prefers the frozen SLA stamp (created→resolved, written once when the case
    reaches RESOLVED/CLOSED). That value is immune to later edits — a note added
    to a closed case, or a re-close — which would otherwise move updated_at and
    inflate the apparent resolution time. Falls back to resolved_at, then
    updated_at, for legacy rows written before the stamp existed.
    """
    sla = case.sla_metadata or {}
    stamped = sla.get("minutes_to_resolve")
    if isinstance(stamped, (int, float)) and stamped >= 0:
        return float(stamped)

    created = case.created_at
    if created and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if not created:
        return None

    end: datetime | None = None
    resolved_at = sla.get("resolved_at")
    if resolved_at:
        try:
            end = datetime.fromisoformat(resolved_at)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            end = None
    if end is None:
        end = case.updated_at
        if end and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
    if not end:
        return None

    minutes = (end - created).total_seconds() / 60
    return minutes if minutes >= 0 else None


# ── Volume trend ─────────────────────────────────────────────────────────────

def volume_trend(db: Session, days: int = 14) -> list[dict[str, Any]]:
    """Daily email volume broken down by classification for the last *days* days."""
    cutoff = _utcnow() - timedelta(days=days)
    rows = (
        db.query(Case.created_at, Case.classification)
        .filter(Case.created_at >= cutoff)
        .all()
    )

    # Group by day × classification
    daily: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for created_at, classification in rows:
        if created_at:
            label = _day_label(created_at)
            daily[label][classification or "UNKNOWN"] += 1
            daily[label]["TOTAL"] += 1

    # Fill in any missing days with zeros
    result = []
    for i in range(days):
        day = _day_label(_utcnow() - timedelta(days=days - 1 - i))
        entry = {"date": day, **daily.get(day, {})}
        entry.setdefault("TOTAL", 0)
        result.append(entry)

    return result


# ── Classification heatmap (classification × department) ─────────────────────

def classification_heatmap(db: Session) -> dict[str, Any]:
    """Cross-matrix of classification vs primary_department counts."""
    rows = (
        db.query(Case.classification, Case.primary_department, func.count(Case.id))
        .group_by(Case.classification, Case.primary_department)
        .all()
    )

    matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    classifications: set[str] = set()
    departments: set[str] = set()

    for classification, department, count in rows:
        c = classification or "UNKNOWN"
        d = department or "UNKNOWN"
        matrix[c][d] = count
        classifications.add(c)
        departments.add(d)

    return {
        "matrix": {c: dict(d_map) for c, d_map in matrix.items()},
        "classifications": sorted(classifications),
        "departments": sorted(departments),
    }


# ── Resolution time stats ─────────────────────────────────────────────────────

def resolution_time_stats(db: Session) -> dict[str, Any]:
    """Average and median resolution time (minutes) by priority and department."""
    resolved = (
        db.query(Case)
        .filter(Case.workflow_state.in_(["RESOLVED", "CLOSED"]))
        .all()
    )

    by_priority: dict[str, list[float]] = defaultdict(list)
    by_dept: dict[str, list[float]] = defaultdict(list)
    overall: list[float] = []

    for case in resolved:
        minutes = _resolution_minutes(case)
        if minutes is None:
            continue
        by_priority[case.priority or "LOW"].append(minutes)
        by_dept[case.primary_department or "Unknown"].append(minutes)
        overall.append(minutes)

    def _stats(times: list[float]) -> dict[str, Any]:
        if not times:
            return {"avg": 0, "median": 0, "min": 0, "max": 0, "count": 0}
        s = sorted(times)
        n = len(s)
        return {
            "avg": round(sum(s) / n, 1),
            "median": round(_median(s), 1),
            "min": round(s[0], 1),
            "max": round(s[-1], 1),
            "count": n,
        }

    return {
        "overall": _stats(overall),
        "by_priority": {p: _stats(t) for p, t in by_priority.items()},
        "by_department": {d: _stats(t) for d, t in by_dept.items()},
    }


# ── SLA breach analysis ───────────────────────────────────────────────────────

def sla_breach_analysis(db: Session) -> dict[str, Any]:
    """Breach counts and rates across all cases, grouped by department and priority."""
    now = _utcnow()
    all_cases = db.query(Case).all()
    active_cases = [c for c in all_cases if c.workflow_state not in {"RESOLVED", "CLOSED"}]

    total = len(active_cases)
    breached: list[Case] = []
    nearing: list[Case] = []
    healthy: list[Case] = []

    for case in active_cases:
        sla = case.sla_metadata or {}
        due_str = sla.get("resolution_due_at")
        if not due_str:
            # Active case with no SLA deadline — count as healthy
            healthy.append(case)
            continue
        try:
            due = datetime.fromisoformat(due_str)
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            healthy.append(case)
            continue
        minutes_left = (due - now).total_seconds() / 60
        if minutes_left < 0:
            breached.append(case)
        elif minutes_left < 120:
            nearing.append(case)
        else:
            healthy.append(case)

    dept_breach: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    priority_breach: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for case in breached:
        dept_breach[case.primary_department or "Unknown"]["breached"] += 1
    for case in nearing:
        dept_breach[case.primary_department or "Unknown"]["nearing"] += 1

    for case in breached:
        priority_breach[case.priority or "LOW"]["breached"] += 1
    for case in nearing:
        priority_breach[case.priority or "LOW"]["nearing"] += 1

    return {
        "summary": {
            "total_active": total,
            "breached": len(breached),
            "nearing_breach": len(nearing),
            "healthy": len(healthy),
            "breach_rate": round(len(breached) / max(total, 1) * 100, 1),
        },
        "by_department": dict(dept_breach),
        "by_priority": dict(priority_breach),
    }


# ── Model performance metrics ─────────────────────────────────────────────────

def model_performance_metrics(db: Session) -> dict[str, Any]:
    """Confidence distribution, adjudication modes, human override stats."""
    cases = db.query(Case).all()

    conf_buckets = {"<0.65": 0, "0.65–0.75": 0, "0.75–0.85": 0, "0.85–0.95": 0, "≥0.95": 0}
    adj_modes: dict[str, int] = defaultdict(int)
    sentiment_dist: dict[str, int] = defaultdict(int)
    needs_human = 0

    for case in cases:
        score = case.confidence_score or 0.0
        if score < 0.65:
            conf_buckets["<0.65"] += 1
        elif score < 0.75:
            conf_buckets["0.65–0.75"] += 1
        elif score < 0.85:
            conf_buckets["0.75–0.85"] += 1
        elif score < 0.95:
            conf_buckets["0.85–0.95"] += 1
        else:
            conf_buckets["≥0.95"] += 1

        ai = case.ai_analysis or {}
        adj = ai.get("adjudication_mode")
        if adj:
            adj_modes[adj] += 1

        # Sentiment from ai_analysis → bert_analysis → sentiment
        sentiment = ai.get("sentiment") or ai.get("extracted_entities", {})
        if isinstance(sentiment, str):
            sentiment_dist[sentiment] += 1
        elif isinstance(sentiment, dict):
            s = sentiment.get("sentiment")
            if s:
                sentiment_dist[s] += 1

        if case.needs_human_triage:
            needs_human += 1

    # Human override stats from feedback table
    feedback_rows = db.query(MLFeedback).all()
    corrections = len([f for f in feedback_rows if f.original_classification != f.corrected_classification])
    agreements = len([f for f in feedback_rows if f.original_classification == f.corrected_classification])

    correction_matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for fb in feedback_rows:
        if fb.original_classification != fb.corrected_classification:
            correction_matrix[fb.original_classification][fb.corrected_classification] += 1

    total = len(cases)
    avg_conf = (
        sum(c.confidence_score or 0.0 for c in cases) / total
        if total else 0.0
    )

    return {
        "total_cases": total,
        "average_confidence": round(avg_conf, 4),
        "needs_human_triage_count": needs_human,
        "auto_routed_count": total - needs_human,
        "auto_route_rate": round((total - needs_human) / max(total, 1) * 100, 1),
        "confidence_distribution": conf_buckets,
        "adjudication_modes": dict(adj_modes),
        "sentiment_distribution": dict(sentiment_dist),
        "feedback": {
            "total_feedback": len(feedback_rows),
            "corrections": corrections,
            "agreements": agreements,
            "correction_rate": round(corrections / max(len(feedback_rows), 1) * 100, 1),
        },
        "correction_matrix": {k: dict(v) for k, v in correction_matrix.items()},
    }


# ── Fraud risk trend ──────────────────────────────────────────────────────────

def fraud_risk_trend(db: Session, days: int = 14) -> list[dict[str, Any]]:
    """Daily fraud case count + total amount involved for risk tracking."""
    cutoff = _utcnow() - timedelta(days=days)
    fraud_cases = (
        db.query(Case)
        .filter(Case.classification == "UNAUTHORISED_USE", Case.created_at >= cutoff)
        .all()
    )

    daily: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "total_amount": 0.0, "critical": 0})
    for case in fraud_cases:
        if not case.created_at:
            continue
        label = _day_label(case.created_at)
        daily[label]["count"] += 1
        amount = (case.extracted_fields or {}).get("amount_involved") or 0
        try:
            daily[label]["total_amount"] += float(amount)
        except (TypeError, ValueError):
            pass
        if case.priority == "CRITICAL":
            daily[label]["critical"] += 1

    result = []
    for i in range(days):
        day = _day_label(_utcnow() - timedelta(days=days - 1 - i))
        entry = {"date": day, **daily.get(day, {"count": 0, "total_amount": 0.0, "critical": 0})}
        result.append(entry)

    return result


# ── Department performance ────────────────────────────────────────────────────

def department_performance(db: Session) -> list[dict[str, Any]]:
    """Per-department stats: total, open, resolved, avg confidence, SLA health."""
    dept_rows = (
        db.query(CaseDepartment.department)
        .distinct()
        .all()
    )
    departments = [r[0] for r in dept_rows if r[0]]

    result = []
    for dept in departments:
        cd_rows = db.query(CaseDepartment).filter(CaseDepartment.department == dept).all()
        case_ids = [r.case_id for r in cd_rows]
        dept_cases = db.query(Case).filter(Case.case_id.in_(case_ids)).all() if case_ids else []

        open_cases = [c for c in dept_cases if c.workflow_state not in {"RESOLVED", "CLOSED"}]
        resolved = [c for c in dept_cases if c.workflow_state in {"RESOLVED", "CLOSED"}]
        critical = [c for c in open_cases if c.priority == "CRITICAL"]
        avg_conf = (
            sum(c.confidence_score or 0 for c in dept_cases) / len(dept_cases)
            if dept_cases else 0
        )

        result.append({
            "department": dept,
            "total": len(dept_cases),
            "open": len(open_cases),
            "resolved": len(resolved),
            "critical_open": len(critical),
            "avg_confidence": round(avg_conf, 3),
            "resolution_rate": round(len(resolved) / max(len(dept_cases), 1) * 100, 1),
        })

    return sorted(result, key=lambda x: x["total"], reverse=True)


# ── Department deep-dive ──────────────────────────────────────────────────────

def department_deep_dive(db: Session) -> list[dict[str, Any]]:
    """
    Per-department breakdown of issue types, resolution methods, and
    representative resolution texts — powers the department analytics panel.
    """
    dept_rows = db.query(CaseDepartment.department).distinct().all()
    departments = [r[0] for r in dept_rows if r[0]]

    result = []
    for dept in departments:
        cd_rows = db.query(CaseDepartment).filter(CaseDepartment.department == dept).all()
        case_ids = [r.case_id for r in cd_rows]
        if not case_ids:
            continue
        dept_cases = db.query(Case).filter(Case.case_id.in_(case_ids)).all()

        # --- classification breakdown ---
        clf_counts: dict[str, int] = defaultdict(int)
        for c in dept_cases:
            key = c.classification or "Unclassified"
            clf_counts[key] += 1
        classifications = [
            {"label": k, "count": v}
            for k, v in sorted(clf_counts.items(), key=lambda x: -x[1])
        ]

        # --- priority breakdown ---
        # pri_counts spans ALL cases (any state) — used by the "Priority Breakdown"
        # panel which reports the full historical mix.
        # pri_counts_open spans only OPEN cases — used by dashboards that label the
        # chart "open"/"critical needing action", so those labels stay truthful.
        pri_counts: dict[str, int] = defaultdict(int)
        pri_counts_open: dict[str, int] = defaultdict(int)
        for c in dept_cases:
            pri = c.priority or "UNKNOWN"
            pri_counts[pri] += 1
            if c.workflow_state not in {"RESOLVED", "CLOSED"}:
                pri_counts_open[pri] += 1

        # All resolved/closed cases — the basis for counts, rates and timings.
        resolved_all = [c for c in dept_cases if c.workflow_state in {"RESOLVED", "CLOSED"}]

        # --- resolution summaries (up to 5 most recent WITH a written resolution) ---
        # Only cases that carry resolution_text can be shown here, but they must not
        # define the resolved *count* — a case resolved via a specialist action
        # (technical_resolved / compliance_clearance) or plain `resolve` has no text
        # yet is still resolved.
        resolved_with_text = [c for c in resolved_all if c.resolution_text]
        resolved_with_text.sort(key=lambda c: str(c.updated_at or ""), reverse=True)
        resolutions = []
        for c in resolved_with_text[:5]:
            resolutions.append({
                "case_id":        c.case_id,
                "classification": c.classification or "Unclassified",
                "priority":       c.priority,
                "resolution_text": (c.resolution_text or "")[:300],
                "resolved_at":    c.updated_at.isoformat() if c.updated_at else None,
            })

        # --- avg resolution time (hours) — across all resolved cases ---
        resolution_times = []
        for c in resolved_all:
            mins = _resolution_minutes(c)
            if mins is not None:
                hours = mins / 60
                if 0 < hours < 720:  # cap at 30 days to ignore bad data
                    resolution_times.append(hours)
        avg_resolution_hours = (
            round(sum(resolution_times) / len(resolution_times), 1)
            if resolution_times else None
        )

        # --- open vs resolved ---
        open_count     = sum(1 for c in dept_cases if c.workflow_state not in {"RESOLVED", "CLOSED"})
        resolved_count = len(resolved_all)

        result.append({
            "department":           dept,
            "total":                len(dept_cases),
            "open":                 open_count,
            "resolved":             resolved_count,
            "resolution_rate":      round(resolved_count / max(len(dept_cases), 1) * 100, 1),
            "avg_resolution_hours": avg_resolution_hours,
            "classifications":      classifications,
            "priority_breakdown":   dict(pri_counts),
            "priority_breakdown_open": dict(pri_counts_open),
            "critical_open":        pri_counts_open.get("CRITICAL", 0),
            "recent_resolutions":   resolutions,
        })

    return sorted(result, key=lambda x: x["total"], reverse=True)

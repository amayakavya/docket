from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models import Case


SLA_MINUTES = {
    "CRITICAL": {"ack": 15,   "investigation": 60,   "resolution": 240,   "escalation": 30},
    "HIGH":     {"ack": 60,   "investigation": 240,  "resolution": 1440,  "escalation": 120},
    "MEDIUM":   {"ack": 240,  "investigation": 1440, "resolution": 4320,  "escalation": 720},
    "LOW":      {"ack": 1440, "investigation": 2880, "resolution": 10080, "escalation": 1440},
}

# How many minutes before breach to start showing NEARING_BREACH (≈15% of resolution window)
NEARING_BREACH_MINUTES = {
    "CRITICAL": 30,    # 12.5% of 240 min resolution
    "HIGH":     120,   # 8.3%  of 1440
    "MEDIUM":   480,   # 11.1% of 4320  (8 hours warning)
    "LOW":      1440,  # 14.3% of 10080 (24 hours warning)
}


def build_sla_metadata(priority: str, start: datetime | None = None) -> dict[str, Any]:
    start = start or datetime.now(timezone.utc)
    windows = SLA_MINUTES.get(priority, SLA_MINUTES["LOW"])
    return {
        "acknowledgement_due_at": (start + timedelta(minutes=windows["ack"])).isoformat(),
        "investigation_due_at": (start + timedelta(minutes=windows["investigation"])).isoformat(),
        "resolution_due_at": (start + timedelta(minutes=windows["resolution"])).isoformat(),
        "escalation_due_at": (start + timedelta(minutes=windows["escalation"])).isoformat(),
        "sla_profile": priority,
    }


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        # If stored without tzinfo (legacy records), treat as UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# SLA types that are only relevant in early workflow states — skip once past them
_STATE_SKIP: dict[str, set[str]] = {
    "acknowledgement": {"ACKNOWLEDGED", "UNDER_REVIEW", "MULTI_DEPARTMENT_REVIEW",
                        "WAITING_FOR_ACTION", "ESCALATED", "RESOLVED", "CLOSED"},
    # REOPENED is intentionally absent: reopen rebuilds SLA from scratch, so the
    # fresh acknowledgement deadline must be monitored.
    # ESCALATED is intentionally NOT in this set: an escalated case must still be resolved
    # within its escalation window, so the SLA clock keeps running.
    "escalation":      {"RESOLVED", "CLOSED"},
    "investigation":   {"RESOLVED", "CLOSED"},
    "resolution":      {"RESOLVED", "CLOSED"},
}


def sla_alerts_for_case(case: Case) -> list[dict[str, Any]]:
    if case.workflow_state in {"RESOLVED", "CLOSED"}:
        return []
    now = datetime.now(timezone.utc)
    nearing_threshold = NEARING_BREACH_MINUTES.get(case.priority or "LOW", NEARING_BREACH_MINUTES["LOW"])
    alerts = []
    for key, due_value in (case.sla_metadata or {}).items():
        if not key.endswith("_due_at"):
            continue
        sla_type = key.replace("_due_at", "")
        # Skip SLA types that are no longer relevant for the current workflow state
        if case.workflow_state in _STATE_SKIP.get(sla_type, set()):
            continue
        due = _parse(due_value)
        if not due:
            continue
        minutes_left = int((due - now).total_seconds() // 60)
        if minutes_left < 0:
            severity = "OVERDUE"
        elif minutes_left <= nearing_threshold:
            severity = "NEARING_BREACH"
        else:
            continue
        alerts.append(
            {
                "case_id": case.case_id,
                "priority": case.priority,
                "sla_profile": (case.sla_metadata or {}).get("sla_profile", case.priority),
                "sla_type": sla_type,
                "due_at": due_value,
                "minutes_left": minutes_left,
                "severity": severity,
                "department": case.primary_department,
                # Enriched fields for hover/popup
                "classification": case.classification,
                "workflow_state": case.workflow_state,
                "summary": (
                    (case.ai_analysis or {}).get("raw_triage", {}).get("llm_summary")
                    or (case.extracted_fields or {}).get("service_summary")
                ),
                "customer": (
                    (case.extracted_fields or {}).get("customer_name")
                    or (case.customer_metadata or {}).get("sender_email_masked")
                ),
                "amount_involved": (case.extracted_fields or {}).get("amount_involved"),
                "assigned_operator": next(
                    (d.assigned_operator for d in case.departments if d.assigned_operator),
                    None,
                ),
            }
        )
    return alerts


def all_sla_alerts(db: Session) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for case in db.query(Case).all():
        alerts.extend(sla_alerts_for_case(case))
    return sorted(alerts, key=lambda item: (item["severity"] != "OVERDUE", item["minutes_left"]))

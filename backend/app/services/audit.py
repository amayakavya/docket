from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models import AuditEvent, Case, TimelineEvent


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_event(
    db: Session,
    case: Case,
    *,
    actor: str,
    role: str,
    action: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    previous_state: dict[str, Any] | None = None,
    new_state: dict[str, Any] | None = None,
    department: str | None = None,
) -> None:
    metadata = metadata or {}
    previous_state = previous_state or {}
    new_state = new_state or {}
    ts = now_iso()

    db.add(
        AuditEvent(
            case_id=case.case_id,
            actor=actor,
            role=role,
            action=action,
            event_metadata=metadata,
            previous_state=previous_state,
            new_state=new_state,
        )
    )
    db.add(
        TimelineEvent(
            case_id=case.case_id,
            actor=actor,
            department=department,
            event_type=action,
            message=message,
            event_metadata=metadata,
        )
    )

    audit_entry = {
        "timestamp": ts,
        "actor": actor,
        "role": role,
        "action": action,
        "metadata": metadata,
        "previous_state": previous_state,
        "new_state": new_state,
    }
    timeline_entry = {
        "timestamp": ts,
        "actor": actor,
        "department": department,
        "event_type": action,
        "message": message,
        "metadata": metadata,
    }
    case.audit_history = [*list(case.audit_history or []), audit_entry]
    case.unified_timeline = [*list(case.unified_timeline or []), timeline_entry]
    flag_modified(case, "audit_history")
    flag_modified(case, "unified_timeline")

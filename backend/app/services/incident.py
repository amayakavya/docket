from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models import Case, Incident


PATTERNS = {
    "the mobile app_OUTAGE": ["mobile_app", "app down", "mobile app", "server unavailable"],
    "UPI_FAILURE_CLUSTER": ["upi failed", "upi transaction", "upi payment", "upi debit"],
    "PAYMENT_FAILURE_CLUSTER": ["autopay", "card", "wallet"],
    "BRANCH_SERVICE_SURGE": ["service_area manager", "service_area complaint", "queue at service_area"],
    "FRAUD_SPIKE": ["unauthorized", "fraud", "phishing", "stolen"],
}


def detect_pattern(text: str) -> str | None:
    lower = text.lower()
    for key, terms in PATTERNS.items():
        if any(term in lower for term in terms):
            return key
    return None


def attach_incident_if_needed(db: Session, case: Case) -> dict[str, Any] | None:
    pattern = detect_pattern(case.normalized_text)
    if not pattern:
        return None
    peers = db.query(Case).filter(Case.incident_group == pattern).all()
    case.incident_group = pattern
    if len(peers) < 1:
        return {"pattern_key": pattern, "status": "watching", "case_count": 1}

    incident = db.query(Incident).filter(Incident.pattern_key == pattern, Incident.status == "OPEN").one_or_none()
    if not incident:
        incident = Incident(
            incident_id=f"INC-{uuid.uuid4().hex[:8].upper()}",
            title=pattern.replace("_", " ").title(),
            pattern_key=pattern,
            master_case_id=peers[0].case_id if peers else case.case_id,
            case_ids=[],
            incident_metadata={"source": "incident_engine"},
        )
        db.add(incident)
        db.flush()
    ids = sorted({*list(incident.case_ids or []), case.case_id, *(peer.case_id for peer in peers)})
    incident.case_ids = ids
    incident.incident_metadata = {**(incident.incident_metadata or {}), "case_count": len(ids)}
    flag_modified(incident, "case_ids")
    flag_modified(incident, "incident_metadata")
    return {"incident_id": incident.incident_id, "pattern_key": pattern, "case_count": len(ids)}

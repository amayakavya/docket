"""
Case seeder.

Builds a spread of cases across every classification in the taxonomy, using the
generated subscriber fixtures as the correspondents. The previous revision listed
every case by hand; composing them from templates keeps the set consistent with
the taxonomy automatically, so adding a classification no longer means writing
ten more cases to match.

    python seed_cases.py [count]
"""
from __future__ import annotations

import hashlib
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from backend.app.database import SessionLocal
from backend.app.models import AuditEvent, Case, CaseDepartment, TimelineEvent
from backend.app.catalog.taxonomy import _LEAF_INDEX
from backend.app.services.seed_subscribers import subscriber_records

SEED = 91127
PER_CLASSIFICATION = 4

TIER_HOURS = {"CRITICAL": 4, "HIGH": 12, "MEDIUM": 24, "LOW": 48}

# One complaint and one closing line per classification. The body is assembled
# from these plus the subscriber's own details, so every case reads as a real
# piece of correspondence without any of them being one.
BODIES: dict[str, tuple[str, str]] = {
    "UNAUTHORISED_USE": ("I have found usage on my line that is not mine and I never authorised it.",
                         "Please block the line and tell me how this happened."),
    "PORTAL_ACCESS": ("I cannot sign in to the self care portal. It says my account is locked.",
                      "Please restore access so I can pay my bill."),
    "PAYMENT_FAILURE": ("I paid my bill and the money left my account, but the payment is not showing.",
                        "Please apply the payment or return it."),
    "BILL_DISPUTE": ("My bill this month is far higher than my plan and I cannot see why.",
                     "Please send me a line by line breakdown."),
    "CONNECTION_FAULT": ("My connection has been dropping every few minutes since the weekend.",
                         "Please send an engineer or fix it at your end."),
    "INSTALLATION_DELAY": ("My installation was booked for last week and nobody has come.",
                           "Please give me a date you will actually keep."),
    "REGULATORY": ("I have referred this matter to the regulator as I have had no reply from you.",
                   "Please respond within the statutory window."),
    "LEGAL_NOTICE": ("Please treat this as formal notice ahead of legal action over the unresolved fault.",
                     "My advocate will write separately."),
    "VERIFICATION_QUERY": ("My verification documents were rejected without any reason being given.",
                           "Please tell me exactly what you need."),
    "CUSTOMER_GRIEVANCE": ("This is the third time I have written and nobody has called me back.",
                           "I would like this resolved or escalated."),
    "ESCALATION": ("I want this escalated to a manager. The desk has not moved on it in two weeks.",
                   "Please confirm who now owns it."),
    "DUPLICATE": ("Following up on my earlier mail about the same fault, which is still open.",
                  "Please update the existing ticket."),
    "GENERAL_QUERY": ("I would like to know whether my plan can be changed mid cycle.",
                      "Please confirm the process."),
    "CONTRACT_QUERY": ("I want to understand how much of my contract term is left.",
                       "Please send the remaining tenure and any exit charge."),
    "INSTALMENT_ISSUE": ("My device instalment was taken twice this month.",
                         "Please refund the duplicate and correct the schedule."),
    "CONTRACT_CLOSURE": ("I wish to terminate the service at the end of the current cycle.",
                         "Please confirm the final bill."),
    "CONTRACT_ACTIVATION": ("I ordered a new connection and it has still not been activated.",
                            "Please tell me what is holding it up."),
    "ROAMING_ACCOUNT": ("My roaming pack did not work at all while I was travelling.",
                        "Please refund the pack."),
    "INTERNATIONAL_USAGE": ("I have been charged international rates for calls I made at home.",
                            "Please review the usage record."),
    "EQUIPMENT_FAULT": ("The router you supplied restarts every hour and is now unusable.",
                        "Please replace it."),
    "EQUIPMENT_RETURN": ("I returned my equipment after cancelling and am still being charged for it.",
                         "Please close the charge."),
    "SPAM": ("Please remove this address from your marketing list.",
             "I do not wish to receive further promotions."),
}

SUBJECTS: dict[str, str] = {k: k.replace("_", " ").title() for k in BODIES}


def _case(rng: random.Random, classification: str, leaf: dict, who: dict) -> tuple:
    cid = f"SEED-{classification[:6]}-{uuid.uuid4().hex[:8].upper()}"
    desk = leaf["default_department"]
    tier = leaf["sla_tier"]
    now = datetime.now(timezone.utc)

    created = now - timedelta(hours=rng.randrange(4, 400))
    state = rng.choice(["RESOLVED", "RESOLVED", "CLOSED", "UNDER_REVIEW", "ASSIGNED"])
    done = state in ("RESOLVED", "CLOSED")
    updated = created + timedelta(hours=rng.randrange(1, 60)) if done else now - timedelta(hours=2)

    sla_due = created + timedelta(hours=TIER_HOURS[tier])
    breached = sla_due < now and not done
    confidence = round(rng.uniform(0.72, 0.98), 3)
    connection = (who["connection_ids"] or ["-"])[0]

    opening, closing = BODIES[classification]
    body = (f"Dear {desk},\n\n{opening}\n\n"
            f"Connection: {connection}\nCustomer reference: {who['customer_ref']}\n\n"
            f"{closing}\n\nRegards,\n{who['name']}\n{who['mobile_number']}")

    resolution = None
    if done:
        resolution = f"Reviewed by {desk}. Action taken and the subscriber informed."

    case = Case(
        case_id=cid, classification=classification, workflow_state=state,
        primary_department=desk, secondary_departments=[],
        priority=tier, risk_score=round(rng.uniform(0.05, 0.95), 2),
        confidence_score=confidence, source_type="email",
        is_parent=False, parent_case_id=None, child_case_ids=[],
        customer_customer_ref=who["customer_ref"],
        customer_metadata={"customer_name": who["name"], "connection_id": connection,
                           "email": who["email"], "mobile": who["mobile_number"],
                           "customer_ref": who["customer_ref"]},
        email_metadata={"sender": who["email"], "subject": SUBJECTS[classification],
                        "filename": f"email_{cid}.txt"},
        extracted_fields={"connection_id": connection, "customer_name": who["name"]},
        ai_analysis={"primary_classification": classification, "confidence": confidence,
                     "model": "bert+local-llm", "adjudication_mode": "high_confidence_auto",
                     "triage_labels": [{"label": classification, "confidence": confidence,
                                        "source": "bert_ner"}]},
        sla_metadata={"sla_tier": tier, "sla_hours": TIER_HOURS[tier],
                      "due_at": sla_due.isoformat(),
                      "breach_status": "BREACHED" if breached else "HEALTHY",
                      "breached": breached},
        escalation_state={"is_escalated": classification == "ESCALATION"},
        raw_email=body, raw_email_hash=hashlib.sha256(body.encode()).hexdigest()[:96],
        normalized_text=body, resolution_text=resolution,
        needs_human_triage=False, language="en",
        triage_labels=[{"label": classification, "confidence": confidence, "source": "bert_ner"}],
        intake_flags={}, created_at=created, updated_at=updated,
    )

    dept = CaseDepartment(
        case_id=cid, department=desk, department_role="primary",
        routing_confidence=confidence,
        status="RESOLVED" if done else "UNDER_REVIEW",
        assigned_operator="Operator", priority=tier, sla_due_at=sla_due,
        findings={}, internal_notes=[], created_at=created, updated_at=updated,
    )

    events = [AuditEvent(case_id=cid, actor="intake_operator", role="system",
                         action="CASE_CREATED", previous_state={},
                         new_state={"state": "ASSIGNED"},
                         event_metadata={"classification": classification},
                         timestamp=created)]
    timeline = [TimelineEvent(case_id=cid, actor="System", event_type="INTAKE",
                              department=desk,
                              message=f"Case auto-routed to {desk} with {int(confidence*100)}% confidence.",
                              event_metadata={}, timestamp=created)]
    if done:
        events.append(AuditEvent(case_id=cid, actor="Operator", role="operator",
                                 action="CASE_RESOLVED",
                                 previous_state={"state": "UNDER_REVIEW"},
                                 new_state={"state": "RESOLVED"},
                                 event_metadata={"resolution": (resolution or "")[:80]},
                                 timestamp=updated))
        timeline.append(TimelineEvent(case_id=cid, actor="Operator", event_type="RESOLUTION",
                                      department=desk, message=resolution or "",
                                      event_metadata={}, timestamp=updated))
    return case, dept, events, timeline


def build(per_classification: int = PER_CLASSIFICATION) -> list[tuple]:
    rng = random.Random(SEED)
    people = subscriber_records()
    out = []
    for classification, leaf in _LEAF_INDEX.items():
        if classification not in BODIES:
            continue
        for _ in range(per_classification):
            out.append(_case(rng, classification, leaf, rng.choice(people)))
    return out


def main() -> None:
    per = int(sys.argv[1]) if len(sys.argv) > 1 else PER_CLASSIFICATION
    bundles = build(per)
    db = SessionLocal()
    inserted = 0
    try:
        for case, dept, events, timeline in bundles:
            if db.query(Case).filter(Case.case_id == case.case_id).first():
                continue
            db.add(case); db.add(dept)
            for e in events: db.add(e)
            for t in timeline: db.add(t)
            inserted += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"seeded {inserted} cases across {len(BODIES)} classifications")


if __name__ == "__main__":
    main()

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models import Case, CaseDepartment
from .audit import append_event
from .priority import aggregate_case_priority
from .sla import build_sla_metadata


VALID_TRANSITIONS = {
    "NEW": {"TRIAGED"},
    "TRIAGED": {"ASSIGNED", "ESCALATED"},
    "HUMAN_TRIAGE": {"ASSIGNED", "ESCALATED"},        # released by human reviewer
    "ASSIGNED": {"ACKNOWLEDGED", "UNDER_REVIEW", "ESCALATED", "MULTI_DEPARTMENT_REVIEW"},
    "ACKNOWLEDGED": {"UNDER_REVIEW", "ESCALATED", "WAITING_FOR_ACTION", "MULTI_DEPARTMENT_REVIEW"},
    "UNDER_REVIEW": {"MULTI_DEPARTMENT_REVIEW", "WAITING_FOR_ACTION", "ESCALATED", "RESOLVED"},
    "MULTI_DEPARTMENT_REVIEW": {"WAITING_FOR_ACTION", "ESCALATED", "RESOLVED"},
    "WAITING_FOR_ACTION": {"UNDER_REVIEW", "ESCALATED", "RESOLVED"},
    "ESCALATED": {"UNDER_REVIEW", "MULTI_DEPARTMENT_REVIEW", "RESOLVED"},
    "RESOLVED": {"CLOSED", "REOPENED"},
    "CLOSED": {"REOPENED"},
    "REOPENED": {"ASSIGNED", "UNDER_REVIEW"},
}


ACTION_TO_STATE: dict[str, str | None] = {
    # ── common ────────────────────────────────────────────────────────────
    "acknowledge": "ACKNOWLEDGED",
    "assign_operator": None,               # assignment does not rewind case state
    "start_review": "UNDER_REVIEW",
    "escalate": "ESCALATED",
    "request_department": "MULTI_DEPARTMENT_REVIEW",
    "attach_evidence": None,
    "add_note": None,
    "change_priority": None,
    "resolve": "RESOLVED",
    "reopen": "REOPENED",
    "close": "CLOSED",
    "release_triage": "ASSIGNED",          # human triage reviewer releases a case
    # ── Trust & Safety Desk ──────────────────────────────────────
    "suspend_service": "UNDER_REVIEW",       # adds block flag; stays under review
    "request_evidence": "WAITING_FOR_ACTION",
    "flag_usage": "UNDER_REVIEW",    # tags transaction; stays under review
    # ── Customer Resolution Desk ───────────────────────────────
    "offer_resolution": "WAITING_FOR_ACTION",
    "send_communication": None,            # log only, no state change
    # ── Compliance Desk ─────────────────────────────────────
    "file_regulatory_report": "WAITING_FOR_ACTION",
    "compliance_clearance": "RESOLVED",
    # ── Network Support Desk ──────────────────────────
    "raise_field_job": "WAITING_FOR_ACTION",
    "technical_resolved": "RESOLVED",
    # ── Central Operations Desk ─────────────────────────────
    "inter_dept_transfer": None,           # transfer ownership without losing progress
    "override_priority_critical": None,    # priority override only
    "multi_dept_review": "MULTI_DEPARTMENT_REVIEW",
}

COMMON_ACTIONS = {
    "acknowledge",
    "assign_operator",
    "start_review",
    "escalate",
    "resolve",
    "reopen",
    "close",
    "add_note",
    "attach_evidence",
    "change_priority",
}

DEPARTMENT_ACTIONS = {
    # request_department is available to all desks so any operator can forward a
    # misrouted case to the correct division without going through Central Ops.
    "Trust & Safety Desk": {"suspend_service", "request_evidence", "flag_usage", "request_department"},
    "Customer Resolution Desk": {"offer_resolution", "send_communication", "request_department"},
    "Compliance Desk": {"file_regulatory_report", "compliance_clearance", "request_department"},
    "Network Support Desk": {"raise_field_job", "technical_resolved", "request_department"},
    # Central Ops additionally holds inter_dept_transfer (full ownership change)
    # and override/multi-dept actions that other desks don't need.
    "Central Operations Desk": {
        "inter_dept_transfer",
        "override_priority_critical",
        "multi_dept_review",
        "request_department",
    },
}


def is_valid_transition(previous: str, new: str) -> bool:
    if previous == new:
        return True
    return new in VALID_TRANSITIONS.get(previous, set())


def action_block_reason(case: Case, action: str, department: str | None = None) -> str | None:
    if getattr(case, "is_parent", False):
        return "Parent cases are read-only; open a departmental sub-case to act."
    # Hard gate: no department action is allowed while awaiting human triage review
    if case.workflow_state == "HUMAN_TRIAGE" and action != "release_triage":
        return "Case is pending human triage review. It must be released from the triage queue before department operators can act on it."
    operating_department = department or case.primary_department
    allowed_for_desk = COMMON_ACTIONS | DEPARTMENT_ACTIONS.get(operating_department, set())
    if action not in allowed_for_desk:
        return f"{action.replace('_', ' ').title()} is not permitted for {operating_department}."
    if case.workflow_state == "CLOSED" and action not in {"reopen", "add_note", "attach_evidence"}:
        return "Closed cases must be reopened before operational actions."
    if case.workflow_state == "RESOLVED" and action not in {"close", "reopen", "add_note", "attach_evidence"}:
        return "Resolved cases may be closed, reopened, or documented."
    target_state = ACTION_TO_STATE.get(action)
    if target_state and not is_valid_transition(case.workflow_state, target_state):
        return f"Complete a valid prior step before {action.replace('_', ' ')}."
    return None


def _state_only_block_reason(case: Case, action: str) -> str | None:
    """
    Check only workflow-state and parent-child constraints.
    Department restrictions are intentionally NOT applied here — each desk
    has its own hardcoded action list in the frontend, and department
    validation is enforced server-side in validate_action_request when the
    action is actually submitted.  Separating these two concerns means every
    desk sees the correct state-machine buttons without false positives.
    """
    if getattr(case, "is_parent", False):
        return "Parent cases are read-only; open a departmental sub-case to act."
    if case.workflow_state == "HUMAN_TRIAGE" and action != "release_triage":
        return "Pending human triage — must be released before department actions."
    if case.workflow_state == "CLOSED" and action not in {"reopen", "add_note", "attach_evidence"}:
        return "Closed cases must be reopened before operational actions."
    if case.workflow_state == "RESOLVED" and action not in {"close", "reopen", "add_note", "attach_evidence"}:
        return "Resolved cases may be closed, reopened, or documented."
    target_state = ACTION_TO_STATE.get(action)
    if target_state and not is_valid_transition(case.workflow_state, target_state):
        return f"Complete a prior step before '{action.replace('_', ' ')}'."
    return None


def workflow_capabilities(case: Case) -> dict[str, Any]:
    """
    Return available/blocked actions for the UI.
    Only state-machine constraints are applied; department restrictions are
    deferred to validate_action_request so every desk sees accurate buttons.
    """
    blocked: dict[str, str] = {}
    allowed: list[str] = []
    for action in ACTION_TO_STATE:
        reason = _state_only_block_reason(case, action)
        if reason:
            blocked[action] = reason
        else:
            allowed.append(action)
    return {
        "available_actions": allowed,
        "blocked_actions": blocked,
        "valid_next_states": sorted(VALID_TRANSITIONS.get(case.workflow_state, set())),
    }


_VALID_PRIORITIES = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}


def validate_action_request(case: Case, request) -> None:
    reason = action_block_reason(case, request.action, request.department or case.primary_department)
    if reason:
        raise ValueError(reason)
    if request.action == "change_priority" and request.priority not in _VALID_PRIORITIES:
        raise ValueError(f"Invalid priority '{request.priority}'. Must be one of: CRITICAL, HIGH, MEDIUM, LOW.")

    required: dict[str, tuple[str, str]] = {
        "assign_operator":        ("operator",              "Enter an operator or agent ID before assigning."),
        "flag_usage":       ("note",                  "Enter a transaction flag reason before continuing."),
        "offer_resolution":       ("note",                  "Enter resolution details before offering a resolution."),
        "send_communication":     ("note",                  "Enter the communication note before sending."),
        "file_regulatory_report":("regulatory_ref",        "Enter a regulatory filing reference before filing."),
        "raise_field_job":       ("ticket_ref",            "Enter an IT ticket reference before creating the ticket."),
        "request_department":     ("requested_department",  "Select a department before requesting review."),
        "inter_dept_transfer":    ("transfer_to",           "Select a destination department before transferring."),
        "multi_dept_review":      ("requested_department",  "Select a department for multi-department review."),
        "attach_evidence":        ("note",                  "Describe the evidence or log attachment before recording it."),
        "escalate":               ("note",                  "Enter an escalation justification before escalating."),
        "resolve":                ("note",                  "Enter a resolution note before marking as resolved."),
        "close":                  ("note",                  "A resolution summary is required to close a case. Describe the solution applied so similar future cases can be suggested this solution."),
    }
    requirement = required.get(request.action)
    if requirement:
        field, message = requirement
        value = getattr(request, field, None)
        if not value or not str(value).strip():
            raise ValueError(message)


# ── Parent-state derivation ─────────────────────────────────────────────────

def derive_parent_state(child_states: list[str]) -> str:
    """
    Compute the derived workflow state for a parent case from its children.

    Rules (in priority order):
      • All children CLOSED                            → CLOSED
      • All children RESOLVED or CLOSED                → RESOLVED
      • Any child ESCALATED                            → ESCALATED
      • Otherwise                                      → MULTI_DEPARTMENT_REVIEW
    """
    if not child_states:
        return "MULTI_DEPARTMENT_REVIEW"
    if all(s == "CLOSED" for s in child_states):
        return "CLOSED"
    if all(s in {"RESOLVED", "CLOSED"} for s in child_states):
        return "RESOLVED"
    if any(s == "ESCALATED" for s in child_states):
        return "ESCALATED"
    return "MULTI_DEPARTMENT_REVIEW"


def rollup_parent(db: Session, child: Case) -> None:
    """
    After a child state transition, recompute the parent's derived state.
    Writes to DB and appends an audit event if the state actually changed.
    """
    if not child.parent_case_id:
        return

    parent = db.query(Case).filter(Case.case_id == child.parent_case_id).one_or_none()
    if parent is None:
        return

    siblings = db.query(Case).filter(Case.parent_case_id == parent.case_id).all()
    child_states = [c.workflow_state for c in siblings]
    new_state = derive_parent_state(child_states)

    if parent.workflow_state == new_state:
        return                              # nothing to do

    old_state = parent.workflow_state
    parent.workflow_state = new_state

    # Build per-child snapshot for the audit record
    children_snapshot = {c.case_id: c.workflow_state for c in siblings}

    append_event(
        db,
        parent,
        actor="system",
        role="system",
        action="parent_state_rollup",
        message=(
            f"Parent state rolled up: {old_state} → {new_state} "
            f"(triggered by child {child.case_id} → {child.workflow_state})"
        ),
        metadata={
            "triggered_by_child": child.case_id,
            "child_new_state": child.workflow_state,
            "all_child_states": children_snapshot,
        },
        previous_state={"workflow_state": old_state},
        new_state={"workflow_state": new_state},
    )


def ensure_department(db: Session, case: Case, department: str, role: str = "secondary", confidence: float = 0.62) -> CaseDepartment:
    existing = (
        db.query(CaseDepartment)
        .filter(CaseDepartment.case_id == case.case_id, CaseDepartment.department == department)
        .one_or_none()
    )
    if existing:
        return existing
    record = CaseDepartment(
        case_id=case.case_id,
        department=department,
        department_role=role,
        routing_confidence=confidence,
        status="NEW",
    )
    db.add(record)
    db.flush()
    return record


def apply_workflow_action(db: Session, case: Case, request) -> Case:
    action = request.action
    validate_action_request(case, request)
    previous_case_state = case.workflow_state
    target_state = ACTION_TO_STATE[action]
    actor = request.actor
    role = request.role
    department = request.department or case.primary_department

    previous_snapshot = {
        "workflow_state": case.workflow_state,
        "priority": case.priority,
        "department": department,
    }

    department_record = ensure_department(db, case, department)
    now = datetime.now(timezone.utc).isoformat()

    # ── auto-assign on every action that carries an operator ──────────
    # The desk console always sends the operator field; whoever takes
    # the first action is implicitly assigned without a dedicated button.
    if request.operator:
        department_record.assigned_operator = request.operator

    if action == "request_department" and request.requested_department:
        ensure_department(db, case, request.requested_department, role="secondary", confidence=0.61)
        secondaries = list(case.secondary_departments or [])
        if not any(item.get("department") == request.requested_department for item in secondaries):
            secondaries.append({"department": request.requested_department, "confidence": 0.61, "requested_by": department})
            case.secondary_departments = secondaries
            flag_modified(case, "secondary_departments")

    if action == "attach_evidence":
        existing_ev = list(department_record.findings.get("evidence", [])) if department_record.findings else []
        evidence = request.evidence or {"description": request.note, "recorded_by": actor, "timestamp": now}
        department_record.findings = {**(department_record.findings or {}), "evidence": [*existing_ev, evidence]}
        flag_modified(department_record, "findings")

    if action in ("add_note", "send_communication") and request.note:
        note = {
            "timestamp": now,
            "actor": actor,
            "role": role,
            "department": department,
            "note": request.note,
            "customer_visible": action == "send_communication" or request.customer_visible,
        }
        if action == "send_communication":
            note["communication_body"] = request.note
            note["delivery_status"] = "pending_smtp"
            note["recipient_email"] = (case.email_metadata or {}).get("sender")
        department_record.internal_notes = [*list(department_record.internal_notes or []), note]
        flag_modified(department_record, "internal_notes")

    if action == "change_priority" and request.priority:
        if request.priority not in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
            raise ValueError(f"Invalid priority '{request.priority}'. Must be CRITICAL, HIGH, MEDIUM, or LOW.")
        case.priority = request.priority
        # Rebuild SLA from NOW so operators don't see instant OVERDUE when promoting
        # a long-running LOW case to CRITICAL.
        case.sla_metadata = build_sla_metadata(request.priority)
        flag_modified(case, "sla_metadata")
        case.ai_analysis = {
            **(case.ai_analysis or {}),
            "operator_override": {"priority": request.priority, "actor": actor, "timestamp": now, "reason": request.note},
        }
        flag_modified(case, "ai_analysis")

    # ── Fraud desk ──────────────────────────────────────────────────────
    if action == "suspend_service":
        findings = dict(department_record.findings or {})
        findings["service_suspended"] = True
        findings["suspended_by"] = actor
        findings["suspended_at"] = now
        findings["suspension_note"] = request.note or ""
        department_record.findings = findings
        flag_modified(department_record, "findings")

    if action == "flag_usage":
        findings = dict(department_record.findings or {})
        flags = list(findings.get("flagged_transactions", []))
        flags.append({"flagged_by": actor, "timestamp": now, "note": request.note or ""})
        findings["flagged_transactions"] = flags
        department_record.findings = findings
        flag_modified(department_record, "findings")

    if action == "request_evidence":
        findings = dict(department_record.findings or {})
        findings["forensics_requested"] = True
        findings["forensics_requested_by"] = actor
        findings["forensics_requested_at"] = now
        department_record.findings = findings
        flag_modified(department_record, "findings")

    # ── Customer Service desk ───────────────────────────────────────────
    if action == "offer_resolution":
        findings = dict(department_record.findings or {})
        findings["resolution_offered"] = True
        findings["resolution_note"] = request.note or ""
        findings["resolution_offered_by"] = actor
        findings["resolution_offered_at"] = now
        department_record.findings = findings
        flag_modified(department_record, "findings")

    # ── Compliance desk ─────────────────────────────────────────────────
    if action == "file_regulatory_report":
        findings = dict(department_record.findings or {})
        findings["regulatory_report_raised"] = True
        findings["regulatory_ref"] = request.regulatory_ref or ""
        findings["regulatory_note"] = request.note or ""
        findings["reported_by"] = actor
        findings["reported_at"] = now
        department_record.findings = findings
        flag_modified(department_record, "findings")

    # ── Technical desk ───────────────────────────────────────────────────
    if action == "raise_field_job":
        findings = dict(department_record.findings or {})
        findings["it_ticket_created"] = True
        findings["it_ticket_ref"] = request.ticket_ref or f"INC-{now[:10]}-AUTO"
        findings["it_ticket_note"] = request.note or ""
        findings["ticket_created_by"] = actor
        findings["ticket_created_at"] = now
        department_record.findings = findings
        flag_modified(department_record, "findings")

    # ── Central Ops desk ────────────────────────────────────────────────
    if action == "inter_dept_transfer" and request.transfer_to:
        new_dept_record = ensure_department(db, case, request.transfer_to, role="primary", confidence=0.90)
        new_dept_record.status = "ASSIGNED"  # receiving desk starts fresh from ASSIGNED
        case.primary_department = request.transfer_to
        flag_modified(case, "primary_department")
        # Rebuild SLA from now so the new desk gets fresh windows, not inherited stale deadlines.
        case.sla_metadata = build_sla_metadata(case.priority or "LOW")
        flag_modified(case, "sla_metadata")
        # Demote old primary to secondary
        old_primary = db.query(CaseDepartment).filter(
            CaseDepartment.case_id == case.case_id,
            CaseDepartment.department == department,
        ).one_or_none()
        if old_primary and old_primary.department_role == "primary":
            old_primary.department_role = "secondary"

    if action == "override_priority_critical":
        case.priority = "CRITICAL"
        # Rebuild SLA from NOW — using created_at would make all windows immediately OVERDUE.
        case.sla_metadata = build_sla_metadata("CRITICAL")
        flag_modified(case, "sla_metadata")
        case.ai_analysis = {
            **(case.ai_analysis or {}),
            "operator_override": {"priority": "CRITICAL", "actor": actor, "timestamp": now, "reason": request.note or "Central Ops override"},
        }
        flag_modified(case, "ai_analysis")

    if action == "multi_dept_review" and request.requested_department:
        ensure_department(db, case, request.requested_department, role="secondary", confidence=0.80)
        secondaries = list(case.secondary_departments or [])
        if not any(item.get("department") == request.requested_department for item in secondaries):
            secondaries.append({"department": request.requested_department, "confidence": 0.80, "requested_by": department})
            case.secondary_departments = secondaries
            flag_modified(case, "secondary_departments")

    # ── Reopen: reset SLA so the case doesn't immediately show as OVERDUE ──
    if action == "reopen":
        case.sla_metadata = build_sla_metadata(case.priority or "LOW")
        flag_modified(case, "sla_metadata")

    # ── If this is a child and its priority changed, propagate to parent ──
    if action == "change_priority" and case.parent_case_id:
        parent = db.query(Case).filter(Case.case_id == case.parent_case_id).one_or_none()
        if parent:
            siblings = db.query(Case).filter(Case.parent_case_id == parent.case_id).all()
            new_parent_priority = aggregate_case_priority([c.priority for c in siblings])
            if parent.priority != new_parent_priority:
                parent.priority = new_parent_priority
                parent.sla_metadata = build_sla_metadata(new_parent_priority)
                flag_modified(parent, "sla_metadata")

    # ── Close: store resolution text and back-fill historical index ─────
    if action == "close" and request.note:
        case.resolution_text = request.note.strip()
        # Update the historical index so this solution appears in future suggestions.
        try:
            from .historical import index_case as _index_case
            _index_case(db, case)
        except Exception:
            pass  # non-critical — solution still stored on the Case row

    # ── Stamp SLA clock on resolve / close ──────────────────────────────
    # Record exact resolution time and whether each SLA window was met.
    # After this point the clock is frozen — sla_alerts_for_case returns []
    # for RESOLVED/CLOSED, and the UI shows "resolved in X" instead of a countdown.
    # Keyed on the *resulting state*, not the action name, so specialist resolve
    # actions (technical_resolved, compliance_clearance) freeze the clock too.
    if target_state in ("RESOLVED", "CLOSED"):
        resolved_ts = datetime.now(timezone.utc)
        sla_meta = dict(case.sla_metadata or {})
        sla_meta["resolved_at"] = resolved_ts.isoformat()

        # Compute minutes taken from case creation to resolution
        created = case.created_at
        if created and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created:
            minutes_taken = int((resolved_ts - created).total_seconds() / 60)
            sla_meta["minutes_to_resolve"] = minutes_taken

        # Check whether resolution landed within the resolution window
        res_due_raw = sla_meta.get("resolution_due_at")
        if res_due_raw:
            try:
                res_due = datetime.fromisoformat(res_due_raw)
                if res_due.tzinfo is None:
                    res_due = res_due.replace(tzinfo=timezone.utc)
                sla_meta["resolution_sla_met"] = resolved_ts <= res_due
            except (ValueError, TypeError):
                pass

        case.sla_metadata = sla_meta
        flag_modified(case, "sla_metadata")

    # ── State transition ─────────────────────────────────────────────────
    if target_state:
        case.workflow_state = target_state
        department_record.status = target_state

    new_snapshot = {
        "workflow_state": case.workflow_state,
        "priority": case.priority,
        "department": department,
        "operator": department_record.assigned_operator,
    }
    if action == "send_communication":
        _recipient = (case.email_metadata or {}).get("sender") or "customer"
        _tl_message = f"Communication queued for {_recipient}"
        _tl_metadata = {
            "department": department,
            "operator": request.operator,
            "communication_body": request.note,
            "recipient_email": (case.email_metadata or {}).get("sender"),
            "delivery_status": "pending_smtp",
            "customer_visible": True,
        }
    else:
        _tl_message = f"{department} performed {action.replace('_', ' ')}"
        _tl_metadata = {
            "department": department,
            "operator": request.operator,
            "note": request.note,
            "requested_department": request.requested_department,
            "transfer_to": request.transfer_to,
            "ticket_ref": request.ticket_ref,
            "regulatory_ref": request.regulatory_ref,
            "customer_visible": request.customer_visible,
        }

    append_event(
        db,
        case,
        actor=actor,
        role=role,
        action=action,
        message=_tl_message,
        metadata=_tl_metadata,
        previous_state=previous_snapshot,
        new_state=new_snapshot,
        department=department,
    )

    # If this is a child sub-case, roll the new state up to the parent.
    rollup_parent(db, case)

    return case

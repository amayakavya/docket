"""
Orchestration layer — ties together ingestion, AI analysis, routing, SLA, audit,
and the parent-child case architecture for multi-department routing.

Single-department routing: one standalone Case (is_parent=False, parent_case_id=None).
Multi-department routing:  one parent Case (is_parent=True) + one child Case per
                           department (parent_case_id set, each owns one dept's work).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models import Case, CaseDepartment
from .ai_analysis import run_triage
from .audit import append_event
from .customer_validation import run_customer_validation
from .attachment_extraction import extract_attachments, merge_attachment_entities
from .clarification import generate_clarification_request
from .historical import detect_customer_repeat, duplicate_hints, find_customer_followups, index_case
from .incident import attach_incident_if_needed
from .file_metadata import extract_file_metadata
from .ingestion import ingest_email_bytes
from .regulatory import detect_regulatory_breach
from .response_templates import generate_draft_response
from .priority import (
    aggregate_case_priority,
    escalation_state,
    priority_from_triage,
    resolve_department_priority,
)
from .risk_engine import compute_risk_assessment, escalate_priority
from .routing import primary_and_secondary, weighted_routing
from .sla import build_sla_metadata
from .intake_triage import run_intake_triage


# ── Readable ticket-ID generation ───────────────────────────────────────────
_DEPT_CODES: dict[str, str] = {
    "fraud":       "FRDK",  # Trust & Safety Desk
    "customer":    "CUST",  # Customer Resolution Desk
    "compliance":  "COMP",  # Compliance Desk
    "technical":   "TECH",  # Network Support Desk
    "central":     "CENT",  # Central Operations Desk
    "contracts":       "CONTRACT",  # Billing & Contracts Desk
    "nri":         "NRI",   # NRI Services Desk
    "cards":       "CARD",  # Cards & Digital Payments Desk
}

def _dept_code(department: str, needs_triage: bool) -> str:
    if needs_triage:
        return "TRIG"
    dept_lower = department.lower()
    for key, code in _DEPT_CODES.items():
        if key in dept_lower:
            return code
    return "CENT"  # fallback

def make_readable_ticket_id(department: str, needs_triage: bool = False) -> str:
    """Docket-{DEPT}-{YYYYMMDD}-{HHMM}-{4HEX}  e.g. Docket-CUST-20260610-1423-A7F2"""
    now = datetime.now(timezone.utc)
    code = _dept_code(department, needs_triage)
    suffix = uuid.uuid4().hex[:4].upper()
    return f"Docket-{code}-{now.strftime('%Y%m%d')}-{now.strftime('%H%M')}-{suffix}"


# ── Public entry point ──────────────────────────────────────────────────────

def create_case_from_email(
    db: Session,
    *,
    raw_bytes: bytes,
    filename: str,
    actor: str = "intake_operator",
    model: str = "the configured local model",  # always the configured local model, no fallback
) -> Case:
    """
    Process one incoming email — idempotent.

    Returns the parent case (multi-dept) or the standalone case (single-dept).
    Re-ingesting the same email (same SHA-256 hash) returns the existing case
    without creating a duplicate.
    """
    ingested = ingest_email_bytes(raw_bytes, filename)

    # ── Idempotency check: same raw email = same case ────────────────────────
    existing_by_hash = db.query(Case).filter(Case.raw_email_hash == ingested.raw_hash).one_or_none()
    if existing_by_hash:
        return existing_by_hash

    # ── File-level metadata extraction (headers, structure, risk signals) ────
    try:
        _file_meta = extract_file_metadata(raw_bytes, filename)
    except Exception:
        _file_meta = {}

    # ── Smart pre-flight: thread/external provider/sparse detection ───────────────────
    intake = run_intake_triage(
        ingested.normalized_text,
        sender_email=None,   # populated below from triage output
        sender_name=None,
        db=db,
    )

    # For external provider / off-topic / empty emails: create a closed stub case
    if intake.verdict in ("external_bank", "off_topic", "empty"):
        return _create_stub_case(db, ingested, intake, actor=actor, file_meta=_file_meta)

    # ── Attachment extraction — runs before AI so extracted text enriches classification ──
    _attachments: list[dict[str, Any]] = []
    _attachment_entities: dict[str, list[str]] = {}
    if ingested.email_input.attachments:
        try:
            _attachments = extract_attachments(ingested.email_input.raw_bytes)
            _attachment_entities = merge_attachment_entities(_attachments)
        except Exception:
            pass  # never let attachment errors block ingestion

    # Build a plain-text summary of attachment findings to inject into AI context
    _att_text_supplement = ""
    if _attachments:
        parts = []
        for att in _attachments:
            if att.get("text"):
                parts.append(f"[Attachment: {att['filename']}]\n{att['text'][:800]}")
        _att_text_supplement = "\n\n".join(parts)

    # Use the extracted top message as the effective text for AI (thread path)
    effective_normalized = intake.effective_text if intake.thread.is_thread else ingested.normalized_text

    # Append attachment text so AI sees the full picture
    if _att_text_supplement:
        effective_normalized = effective_normalized + "\n\n--- ATTACHMENT CONTENT ---\n" + _att_text_supplement

    ai = run_triage(
        ingested.email_input,
        model=model,
        normalized_text=effective_normalized,
    )
    triage = ai["triage"]
    classification = ai["classification"]
    needs_human_triage = ai.get("needs_human_triage", False)
    language = ai.get("language", "English")
    triage_labels = ai.get("triage_labels", [])

    route_scores = weighted_routing(classification, triage)
    primary_department, secondary_departments = primary_and_secondary(route_scores)
    priority, risk_score = priority_from_triage(triage, classification)
    duplicates = duplicate_hints(db, ingested.normalized_text)

    # ── Same-customer follow-up detection (catches reworded subjects) ────────
    sender_masked = triage.get("sender_email_masked")
    followups = find_customer_followups(
        db, sender_masked, ai.get("embedding"), ingested.raw_hash
    )
    is_followup = len(followups) > 0
    # Merge follow-up case IDs into the duplicate/link set so they cross-link.
    # repeat_flag (built from `duplicates`) then correctly reflects follow-ups too.
    existing_ids = {d["case_id"] for d in duplicates}
    for f in followups:
        if f["case_id"] not in existing_ids:
            duplicates.append(f)
            existing_ids.add(f["case_id"])

    # Build email signals once — shared with all department priority resolvers
    email_signals = _build_email_signals(classification, triage, ai, duplicates)

    # Replace the BET-style ticket ID with a human-readable one that encodes
    # department, date and time — e.g. Docket-CUST-20260610-1423-A7F2
    # `triage` is the same dict stored as ai_analysis["raw_triage"], so this single
    # assignment keeps the persisted JSON consistent — no second write needed.
    triage["ticket_id"] = make_readable_ticket_id(primary_department, needs_triage=needs_human_triage)

    # De-duplicate: if (by extremely rare UUID collision) the ID already exists, return it
    existing = db.query(Case).filter(Case.case_id == triage["ticket_id"]).one_or_none()
    if existing:
        return existing

    # ── Common fields ────────────────────────────────────────────────────────
    bert_entities = ai.get("bert_analysis", {}).get("entities", {})
    bert_primary_amount = ai.get("bert_analysis", {}).get("primary_amount")
    bert_primary_person = ai.get("bert_analysis", {}).get("primary_person")

    customer_metadata = {
        "customer_id": triage.get("customer_id"),
        "sender_email_masked": triage.get("sender_email_masked"),
        "customer_language": triage.get("customer_language"),
        "customer_history_flag": "repeat" if is_followup else triage.get("customer_history_flag", "first_contact"),
        "is_followup": is_followup,
        "followups": followups,
        "case_form_customer": {
            key: triage.get("case_form", {}).get(key)
            for key in ["customer_name", "customer_email", "customer_phone", "customer_segment", "account_type"]
        },
    }
    email_metadata = {
        **triage.get("parsing_metadata", {}),
        "raw_email_hash": ingested.raw_hash,
        "source_filename": filename,
        "file_meta": _file_meta,
    }

    base_extracted = dict(triage.get("case_form", {}))
    if not base_extracted.get("customer_name") and bert_primary_person:
        base_extracted["customer_name"] = bert_primary_person
    if not base_extracted.get("amount_involved") and bert_primary_amount:
        base_extracted["amount_involved"] = bert_primary_amount
    if bert_entities.get("connection_ids"):
        base_extracted.setdefault("connection_ids_detected", bert_entities["connection_ids"])
    if bert_entities.get("payment_handles"):
        base_extracted.setdefault("payment_handles_detected", bert_entities["payment_handles"])

    # ── Customer validation — run for all emails reaching the AI pipeline ───
    # Sparse path already ran this inside intake_triage; proceed path needs it here.
    # Pass raw sender identity so impersonation checks can compare against the customer record.
    _raw_sender_email: str | None = ingested.email_input.sender if (
        ingested.email_input.sender and "@" in ingested.email_input.sender
    ) else None
    _sender_name: str | None = (
        triage.get("case_form", {}).get("customer_name")
        or triage.get("case_form", {}).get("customer_name")
    )

    if intake.verdict == "proceed":
        _cv_result: dict[str, Any] = run_customer_validation(
            ingested.normalized_text, db,
            sender_email=_raw_sender_email,
            sender_name=_sender_name,
        )
    elif intake.customer_match:
        _cv_result = intake.customer_match
    else:
        _cv_result = {}

    # Bump risk_score for high impersonation signals
    _imp_risk = _cv_result.get("impersonation_risk", {})
    _imp_level = _imp_risk.get("level", "UNKNOWN")
    if _imp_level == "CRITICAL":
        risk_score = max(risk_score, 0.9)
    elif _imp_level == "HIGH":
        risk_score = max(risk_score, 0.75)
    elif _imp_level == "MEDIUM":
        risk_score = max(risk_score, 0.55)

    # ── Repeat / new-issue detection ─────────────────────────────────────────
    _matched_customer_ref = (_cv_result.get("matched_customer") or {}).get("customer_ref")
    _repeat_detection = detect_customer_repeat(
        db,
        ingested.normalized_text,
        customer_ref=_matched_customer_ref,
        sender_email_masked=sender_masked,
        exclude_hash=ingested.raw_hash,
    )

    # ── Clarification request ─────────────────────────────────────────────────
    _clarification = generate_clarification_request(
        _cv_result,
        _imp_risk,
        sender_name=_sender_name,
        customer_name=(_cv_result.get("matched_customer") or {}).get("name"),
    )

    # ── Regulatory breach detection ───────────────────────────────────────────
    _reg_breach = detect_regulatory_breach(
        ingested.normalized_text,
        triage_regulatory_flag=bool(triage.get("regulatory_flag")),
        attachment_texts=[a["text"] for a in _attachments if a.get("text")],
    )
    # Override priority and risk_score for regulatory breaches
    if _reg_breach["override_priority"] == "CRITICAL":
        priority = "CRITICAL"
        risk_score = max(risk_score, 0.95)
    elif _reg_breach["override_priority"] == "HIGH":
        priority = "HIGH" if priority not in ("CRITICAL",) else priority
        risk_score = max(risk_score, 0.80)

    # ── Composite risk assessment (Docket-scale within-band ranking) ─────────────
    # Refines risk_score with graduated monetary exposure, customer vulnerability/
    # value segment, repeat/blast-radius — the signals the categorical priority
    # rules ignore. Only ever raises the score, so the safety floors above hold.
    _segment = (
        (_cv_result.get("matched_customer") or {}).get("customer_segment")
        or triage.get("case_form", {}).get("customer_segment")
    )
    _risk_assessment = compute_risk_assessment(
        category=classification,
        signals=email_signals,
        customer_segment=_segment,
        impersonation_level=_imp_level,
        regulatory_override=_reg_breach.get("override_priority"),
        correlated_case_count=len(duplicates) + 1,
    )
    risk_score = max(risk_score, _risk_assessment.normalized)

    # ── Auto-draft response (template only at ingestion — LLM on demand) ──────
    _draft_context: dict[str, Any] = {
        "customer_name": _sender_name or ((_cv_result.get("matched_customer") or {}).get("name")),
        "case_id": triage.get("ticket_id", ""),
        "summary": (ai.get("ai_analysis") or {}).get("routing_explanation", ""),
        "amount_involved": (base_extracted or {}).get("amount_involved")
            or (_attachment_entities.get("amounts") or [None])[0],
        "priority": priority,
        "department": primary_department,
        "sla_metadata": {},  # SLA not yet built at this point — filled at API call time
        "matched_customer": _cv_result.get("matched_customer"),
        "reference_ids": _attachment_entities.get("reference_ids", []),
        "incident_dates": _attachment_entities.get("dates", []),
        "attachment_summary": "; ".join(
            f"{a['filename']}: {a['char_count']} chars extracted"
            for a in _attachments if a.get("char_count", 0) > 0
        ),
        "regulatory_breach": _reg_breach,
    }
    _auto_draft = generate_draft_response(
        classification,
        _draft_context,
        model=model,
        use_llm=False,  # templates at ingestion; LLM on-demand via API
    )

    # ── Build intake_flags from smart intake result ──────────────────────────
    intake_flags: dict[str, Any] = {
        "verdict": intake.verdict,
        "thread_detected": intake.thread.is_thread,
        "thread_depth": intake.thread.depth,
        "top_message_extracted": intake.thread.is_thread,
        "thread_turns": intake.thread.turns,
        "is_external_provider": intake.provider.is_external_provider,
        "is_off_topic": intake.provider.is_off_topic,
        "competitor_provider": intake.provider.competitor_provider,
        "external_confidence": intake.provider.confidence,
        "external_reason": intake.provider.reason,
        "information_density": intake.density.density,
        "meaningful_word_count": intake.density.meaningful_word_count,
        "identifier_count": intake.density.identifier_count,
        "extracted_issue_hint": intake.density.extracted_issue,
        # Customer identity verification
        "customer_matched": bool(_cv_result.get("matched_customer")),
        "sender_validation_run": True,
        "customer_validation_status": _cv_result.get("validation_status", "no_identifiers"),
        "customer_matched_fields": _cv_result.get("matched_fields", []),
        "claimed_names": _cv_result.get("claimed_names", []),
        "cross_conflict": _cv_result.get("cross_conflict", {}),
        "impersonation_risk": _imp_risk,
        # Repeat / history
        "repeat_detection": _repeat_detection,
        # Clarification
        "clarification": _clarification,
        # Regulatory
        "regulatory_breach": _reg_breach,
        # Attachments
        "attachments_extracted": [
            {k: v for k, v in a.items() if k != "text"}  # don't store full text in flags
            for a in _attachments
        ],
        "attachment_entities": _attachment_entities,
        # Pre-generated draft
        "auto_draft": _auto_draft,
        "sparse_customer_match": intake.customer_match,
        "sparse_solutions": intake.historical_solutions,
    }

    ai_analysis_payload = {
        **ai["ai_analysis"],
        "routing_scores": route_scores,
        "duplicate_hints": duplicates,
        "priority_factors": triage.get("priority_ranking", {}).get("score_factors_detected", []),
        "regulatory_breach": _reg_breach,
        "risk_assessment": _risk_assessment.as_dict(),
    }
    now = datetime.now(timezone.utc)

    # ── Routing decision ─────────────────────────────────────────────────────
    is_multi_dept = len(secondary_departments) >= 1

    extra = dict(
        needs_human_triage=needs_human_triage,
        language=language,
        triage_labels=triage_labels,
        intake_flags=intake_flags,
        customer_customer_ref=_matched_customer_ref,
    )

    # Cases needing human triage must NOT be auto-routed to departments.
    # Force single-dept path so the case lands in HUMAN_TRIAGE state, not split.
    if needs_human_triage:
        is_multi_dept = False

    if is_multi_dept:
        case = _create_multi_dept_case(
            db=db,
            triage=triage,
            classification=classification,
            primary_department=primary_department,
            secondary_departments=secondary_departments,
            route_scores=route_scores,
            priority=priority,
            risk_score=risk_score,
            ai_analysis=ai_analysis_payload,
            ai_confidence=ai["confidence_score"],
            ai_embedding=ai.get("embedding"),
            customer_metadata=customer_metadata,
            email_metadata=email_metadata,
            base_extracted=base_extracted,
            ingested=ingested,
            duplicates=duplicates,
            email_signals=email_signals,
            now=now,
            actor=actor,
            **extra,
        )
    else:
        case = _create_single_dept_case(
            db=db,
            triage=triage,
            classification=classification,
            primary_department=primary_department,
            secondary_departments=secondary_departments,
            route_scores=route_scores,
            priority=priority,
            risk_score=risk_score,
            ai_analysis=ai_analysis_payload,
            ai_confidence=ai["confidence_score"],
            ai_embedding=ai.get("embedding"),
            customer_metadata=customer_metadata,
            email_metadata=email_metadata,
            base_extracted=base_extracted,
            ingested=ingested,
            duplicates=duplicates,
            email_signals=email_signals,
            now=now,
            actor=actor,
            **extra,
        )

    # ── Audit smart intake findings ──────────────────────────────────────────
    if intake.thread.is_thread:
        append_event(
            db, case,
            actor="intake_triage", role="system",
            action="thread_detected",
            message=(
                f"Email thread detected (depth {intake.thread.depth}). "
                f"AI classified the top/latest message only."
            ),
            metadata={
                "thread_depth": intake.thread.depth,
                "turns": len(intake.thread.turns),
                "top_message_length": len(intake.thread.top_message),
            },
        )

    if intake.verdict in ("sparse", "empty"):
        append_event(
            db, case,
            actor="intake_triage", role="system",
            action="sparse_email_detected",
            message=(
                f"Low-information email ({intake.density.meaningful_word_count} meaningful words). "
                f"Customer DB match: {'found' if intake_flags.get('customer_matched') else 'not found'}. "
                f"Historical solutions: {len(intake.historical_solutions)} matched."
            ),
            metadata={
                "density": intake.density.density,
                "meaningful_word_count": intake.density.meaningful_word_count,
                "identifier_count": intake.density.identifier_count,
                "extracted_issue_hint": intake.density.extracted_issue,
                "solutions_found": len(intake.historical_solutions),
            },
        )

    # ── Audit the follow-up linkage so operators see the customer history ────
    if is_followup:
        top = followups[0]
        sim = top.get("similarity")
        sim_text = f"{round(sim * 100)}% similar" if sim is not None else "same customer, open case"
        append_event(
            db, case,
            actor="dedupe_engine", role="system",
            action="customer_followup_detected",
            message=(
                f"Follow-up from the same customer detected ({sim_text}). "
                f"Linked to {len(followups)} prior open case(s): "
                f"{', '.join(f['case_id'] for f in followups[:3])}."
            ),
            metadata={
                "is_followup": True,
                "matched_method": top.get("similarity_method"),
                "linked_case_ids": [f["case_id"] for f in followups],
                "sender_email_masked": sender_masked,
            },
        )

    # ── Audit repeat / new-issue detection ───────────────────────────────────
    _rtype = _repeat_detection.get("repeat_type", "first_contact")
    if _rtype == "exact_repeat":
        _prior_id = _repeat_detection.get("same_complaint_case")
        _top_sim = _repeat_detection.get("top_similarity")
        _sim_pct = f"{round(_top_sim * 100)}%" if _top_sim else "high"
        append_event(
            db, case,
            actor="repeat_detector", role="system",
            action="exact_repeat_detected",
            message=(
                f"Customer has already submitted this complaint ({_sim_pct} semantic match "
                f"with {_prior_id}). This appears to be a re-submission — "
                f"check if the prior case is unresolved or the resolution was unsatisfactory."
            ),
            metadata={
                "repeat_type": "exact_repeat",
                "prior_case_id": _prior_id,
                "top_similarity": _top_sim,
                "open_count": _repeat_detection.get("open_count", 0),
                "resolved_count": _repeat_detection.get("resolved_count", 0),
            },
        )
    elif _rtype == "new_issue":
        _open_n = _repeat_detection.get("open_count", 0)
        _all_n = len(_repeat_detection.get("prior_cases", []))
        if _all_n:
            append_event(
                db, case,
                actor="repeat_detector", role="system",
                action="known_customer_new_issue",
                message=(
                    f"Known customer raising a new issue. "
                    f"{_open_n} open case(s) on record; "
                    f"customer context pre-populated from prior history."
                ),
                metadata={
                    "repeat_type": "new_issue",
                    "open_count": _open_n,
                    "resolved_count": _repeat_detection.get("resolved_count", 0),
                    "prior_case_ids": [c["case_id"] for c in _repeat_detection.get("prior_cases", [])[:3]],
                },
            )

    # ── Audit clarification request ───────────────────────────────────────────
    if _clarification.get("needed"):
        append_event(
            db, case,
            actor="clarification_engine", role="system",
            action="clarification_draft_generated",
            message=(
                f"Clarification email drafted: {_clarification.get('reason', '')}. "
                f"Missing fields: {', '.join(_clarification.get('missing_fields', []))}. "
                f"Conflict fields: {', '.join(_clarification.get('conflict_fields', []))}."
            ),
            metadata={
                "subject": _clarification.get("subject"),
                "missing_fields": _clarification.get("missing_fields"),
                "conflict_fields": _clarification.get("conflict_fields"),
            },
        )

    return case


# ── Single-department path (unchanged behaviour) ────────────────────────────

def _create_single_dept_case(
    db: Session,
    *,
    triage: dict,
    classification: str,
    primary_department: str,
    secondary_departments: list,
    route_scores: dict,
    priority: str,
    risk_score: float,
    ai_analysis: dict,
    ai_confidence: float,
    ai_embedding,
    customer_metadata: dict,
    email_metadata: dict,
    base_extracted: dict,
    ingested,
    duplicates: list,
    email_signals: dict,
    now: datetime,
    actor: str,
    needs_human_triage: bool = False,
    language: str = "English",
    triage_labels: list | None = None,
    intake_flags: dict | None = None,
    customer_customer_ref: str | None = None,
) -> Case:
    case = Case(
        case_id=triage["ticket_id"],
        source_type=ingested.source_type,
        classification=classification,
        workflow_state="HUMAN_TRIAGE" if needs_human_triage else "ASSIGNED",
        primary_department="Human Triage Queue" if needs_human_triage else primary_department,
        secondary_departments=secondary_departments,
        customer_metadata=customer_metadata,
        email_metadata=email_metadata,
        extracted_fields=base_extracted,
        ai_analysis=ai_analysis,
        confidence_score=ai_confidence,
        risk_score=risk_score,
        priority=priority,
        sla_metadata=build_sla_metadata(priority),
        escalation_state=escalation_state(priority, triage),
        linked_cases=[hint["case_id"] for hint in duplicates],
        attachments=triage.get("attachments", []),
        raw_email_hash=ingested.raw_hash,
        raw_email_path=str(ingested.raw_path),
        raw_email=ingested.email_input.raw_bytes.decode("utf-8", errors="replace"),
        normalized_text=ingested.normalized_text,
        is_parent=False,
        parent_case_id=None,
        child_case_ids=[],
        audit_history=[],
        unified_timeline=[],
        needs_human_triage=needs_human_triage,
        language=language or "English",
        triage_labels=triage_labels or [],
        intake_flags=intake_flags or {},
        customer_customer_ref=customer_customer_ref,
        created_at=now,
        updated_at=now,
    )
    db.add(case)
    db.flush()

    # ── Resolve per-department priorities and write CaseDepartment records ──
    dept_priorities: list[str] = []

    primary_priority, primary_audit = resolve_department_priority(
        db, primary_department, email_signals, fallback_priority=priority
    )
    db.add(CaseDepartment(
        case_id=case.case_id,
        department=primary_department,
        department_role="primary",
        routing_confidence=route_scores[primary_department],
        status="ASSIGNED",
        priority=primary_priority,
    ))
    dept_priorities.append(primary_priority)
    append_event(
        db, case,
        actor="priority_engine", role="system",
        action="dept_priority_resolved",
        message=f"Priority {primary_priority} assigned to {primary_department}",
        metadata=primary_audit,
        department=primary_department,
    )

    for sec in secondary_departments:
        sec_priority, sec_audit = resolve_department_priority(
            db, sec["department"], email_signals, fallback_priority=priority
        )
        db.add(CaseDepartment(
            case_id=case.case_id,
            department=sec["department"],
            department_role="secondary",
            routing_confidence=sec["confidence"],
            status="SHARED",
            priority=sec_priority,
        ))
        dept_priorities.append(sec_priority)
        append_event(
            db, case,
            actor="priority_engine", role="system",
            action="dept_priority_resolved",
            message=f"Priority {sec_priority} assigned to {sec['department']}",
            metadata=sec_audit,
            department=sec["department"],
        )

    # ── Case.priority = max of all dept priorities (read-only aggregate) ───
    case.priority = aggregate_case_priority(dept_priorities)
    # Lift the band when the composite risk score clears a higher threshold than
    # the categorical rules produced (e.g. a ₹5Cr senior-citizen case the rules
    # only stamped HIGH). Escalation-only — never lowers the rule-based priority.
    case.priority = escalate_priority(case.priority, risk_score)
    # Rebuild SLA with the correct (dept-resolved) priority — the initial build
    # used the raw triage priority which may be lower than what dept rules computed.
    case.sla_metadata = build_sla_metadata(case.priority, start=now)
    flag_modified(case, "sla_metadata")

    _common_audit_events(db, case, triage, classification, primary_department,
                         secondary_departments, ai_analysis, ai_confidence, route_scores, actor)

    incident = attach_incident_if_needed(db, case)
    if incident:
        append_event(db, case, actor="incident_engine", role="system",
                     action="incident_linked",
                     message=f"Case linked to incident {incident['pattern_key']}",
                     metadata=incident)

    index_case(db, case, embedding=ai_embedding)
    return case


# ── Multi-department path: parent + children ────────────────────────────────

def _create_multi_dept_case(
    db: Session,
    *,
    triage: dict,
    classification: str,
    primary_department: str,
    secondary_departments: list,
    route_scores: dict,
    priority: str,
    risk_score: float,
    ai_analysis: dict,
    ai_confidence: float,
    ai_embedding,
    customer_metadata: dict,
    email_metadata: dict,
    base_extracted: dict,
    ingested,
    duplicates: list,
    email_signals: dict,
    now: datetime,
    actor: str,
    needs_human_triage: bool = False,
    language: str = "English",
    triage_labels: list | None = None,
    intake_flags: dict | None = None,
    customer_customer_ref: str | None = None,
) -> Case:
    """
    Create one parent case + one child sub-case per department.

    Parent:  is_parent=True, workflow_state derived from children (read-only).
             Appears in the central console; never in department queues.
    Children: parent_case_id set, each owns exactly one department.
              Appear in department queues; never in the central queue.
    """
    all_depts: list[tuple[str, float]] = [
        (primary_department, route_scores[primary_department]),
        *[(s["department"], s["confidence"]) for s in secondary_departments],
    ]

    # ── 1. Create parent case ────────────────────────────────────────────────
    parent = Case(
        case_id=triage["ticket_id"],
        source_type=ingested.source_type,
        classification=classification,
        # Initial state: will be re-derived as MULTI_DEPARTMENT_REVIEW
        # once children are created and the first rollup fires
        workflow_state="MULTI_DEPARTMENT_REVIEW",
        primary_department=primary_department,
        secondary_departments=secondary_departments,
        customer_metadata=customer_metadata,
        email_metadata=email_metadata,
        extracted_fields=base_extracted,
        ai_analysis={
            **ai_analysis,
            "multi_dept_routing": True,
            "departments_assigned": [d for d, _ in all_depts],
        },
        confidence_score=ai_confidence,
        risk_score=risk_score,
        priority=priority,
        sla_metadata=build_sla_metadata(priority),   # parent SLA = overall tracking
        escalation_state=escalation_state(priority, triage),
        linked_cases=[hint["case_id"] for hint in duplicates],
        attachments=triage.get("attachments", []),
        raw_email_hash=ingested.raw_hash,
        raw_email_path=str(ingested.raw_path),
        raw_email=ingested.email_input.raw_bytes.decode("utf-8", errors="replace"),
        normalized_text=ingested.normalized_text,
        is_parent=True,
        parent_case_id=None,
        child_case_ids=[],          # filled in below
        audit_history=[],
        unified_timeline=[],
        needs_human_triage=needs_human_triage,
        language=language or "English",
        triage_labels=triage_labels or [],
        intake_flags=intake_flags or {},
        customer_customer_ref=customer_customer_ref,
        created_at=now,
        updated_at=now,
    )
    db.add(parent)
    db.flush()

    _common_audit_events(db, parent, triage, classification, primary_department,
                         secondary_departments, ai_analysis, ai_confidence, route_scores, actor)
    append_event(
        db, parent,
        actor="routing_engine", role="system",
        action="multi_dept_routing",
        message=(
            f"Multi-department routing: {len(all_depts)} departments assigned. "
            f"Creating {len(all_depts)} child sub-cases."
        ),
        metadata={"departments": [d for d, _ in all_depts]},
        new_state={"workflow_state": "MULTI_DEPARTMENT_REVIEW"},
    )

    # ── 2. Create one child per department, each with its own priority ───────
    children: list[Case] = []
    for child_num, (dept, conf) in enumerate(all_depts, start=1):
        child = _create_child(
            db=db,
            parent=parent,
            child_num=child_num,
            department=dept,
            routing_confidence=conf,
            email_signals=email_signals,
            fallback_priority=priority,
            now=now,
            actor=actor,
        )
        children.append(child)

    # ── 3. Derive parent priority = max of all children's dept priorities ────
    parent.priority = aggregate_case_priority([c.priority for c in children])
    # Escalate the parent band if the composite risk score clears a higher
    # threshold than the children's categorical priorities (escalation-only).
    parent.priority = escalate_priority(parent.priority, risk_score)
    # Rebuild parent SLA with the aggregated priority (was built from raw triage priority)
    parent.sla_metadata = build_sla_metadata(parent.priority, start=now)
    flag_modified(parent, "sla_metadata")

    # ── 4. Back-fill parent.child_case_ids + cross-link siblings ─────────────
    child_ids = [c.case_id for c in children]
    parent.child_case_ids = child_ids
    flag_modified(parent, "child_case_ids")

    for child in children:
        siblings = [cid for cid in child_ids if cid != child.case_id]
        child.linked_cases = [parent.case_id] + siblings
        flag_modified(child, "linked_cases")

    # ── 5. Incident clustering on parent; propagate group to children ─────────
    incident = attach_incident_if_needed(db, parent)
    if incident:
        append_event(db, parent, actor="incident_engine", role="system",
                     action="incident_linked",
                     message=f"Parent linked to incident {incident['pattern_key']}",
                     metadata=incident)
        if parent.incident_group:
            for child in children:
                child.incident_group = parent.incident_group

    # ── 6. Index parent for historical search ────────────────────────────────
    index_case(db, parent, embedding=ai_embedding)
    # Also index children so dept-level searches surface them
    for child in children:
        index_case(db, child, embedding=ai_embedding)

    return parent


def _create_child(
    db: Session,
    *,
    parent: Case,
    child_num: int,
    department: str,
    routing_confidence: float,
    email_signals: dict,
    fallback_priority: str,
    now: datetime,
    actor: str,
) -> Case:
    """Create one child sub-case for a single department with its own priority."""
    child_id = f"{parent.case_id}-C{child_num}"

    # Resolve department-specific priority before creating the Case record
    dept_priority, priority_audit = resolve_department_priority(
        db, department, email_signals, fallback_priority=fallback_priority
    )

    child = Case(
        case_id=child_id,
        source_type=parent.source_type,
        classification=parent.classification,
        workflow_state="ASSIGNED",
        primary_department=department,
        secondary_departments=[],          # child owns exactly one dept
        customer_metadata=parent.customer_metadata,
        email_metadata=parent.email_metadata,
        extracted_fields=parent.extracted_fields,
        ai_analysis={
            **parent.ai_analysis,
            "parent_case_id": parent.case_id,
            "child_num": child_num,
            "child_department": department,
        },
        confidence_score=parent.confidence_score,
        risk_score=parent.risk_score,
        priority=dept_priority,            # child.priority = its own dept priority
        sla_metadata=build_sla_metadata(dept_priority),
        escalation_state=parent.escalation_state,
        linked_cases=[],           # filled by caller after all children exist
        incident_group=parent.incident_group,
        attachments=parent.attachments,
        raw_email_hash=parent.raw_email_hash,
        raw_email_path=parent.raw_email_path,
        raw_email=parent.raw_email,
        normalized_text=parent.normalized_text,
        is_parent=False,
        parent_case_id=parent.case_id,
        child_case_ids=[],
        audit_history=[],
        unified_timeline=[],
        customer_customer_ref=parent.customer_customer_ref,
        created_at=now,
        updated_at=now,
    )
    db.add(child)
    db.flush()

    db.add(CaseDepartment(
        case_id=child_id,
        department=department,
        department_role="primary",
        routing_confidence=routing_confidence,
        status="ASSIGNED",
        priority=dept_priority,
    ))

    append_event(
        db, child,
        actor="routing_engine", role="system",
        action="child_case_created",
        message=(
            f"Sub-case {child_id} created for {department} "
            f"(child {child_num} of parent {parent.case_id})"
        ),
        metadata={
            "parent_case_id": parent.case_id,
            "child_num": child_num,
            "department": department,
            "routing_confidence": routing_confidence,
        },
    )
    append_event(
        db, child,
        actor=actor, role="intake_operator",
        action="child_assigned",
        message=f"Sub-case assigned to {department}",
        metadata={"department": department},
        new_state={"workflow_state": "ASSIGNED", "primary_department": department},
    )
    # Audit the dept-specific priority decision
    append_event(
        db, child,
        actor="priority_engine", role="system",
        action="dept_priority_resolved",
        message=f"Priority {dept_priority} assigned to {department}",
        metadata=priority_audit,
        department=department,
    )
    return child


# ── Stub case: external provider / off-topic / empty emails ───────────────────────────

def _create_stub_case(
    db: Session,
    ingested: Any,
    intake: Any,
    actor: str = "intake_operator",
    file_meta: dict | None = None,
) -> Case:
    """
    Create a minimal closed case for emails that should not be AI-processed:
      - Non-Docket / competitor provider emails
      - Completely off-topic content
      - Empty / unreadable emails

    The redirect response (if any) is stored as resolution_text so operators
    can see exactly what was sent back to the customer.
    """
    now = datetime.now(timezone.utc)

    verdict = intake.verdict
    if verdict == "external_bank":
        classification = "NON_Docket"
        dept = "Central Operations Desk"
        priority = "LOW"
    elif verdict == "off_topic":
        classification = "OFF_TOPIC"
        dept = "Central Operations Desk"
        priority = "LOW"
    else:  # empty
        classification = "GENERAL_QUERY"
        dept = "Customer Resolution Desk"
        priority = "LOW"

    case_id = make_readable_ticket_id(dept)

    redirect = intake.redirect_response or {}
    resolution = (
        f"Subject: {redirect.get('subject', '')}\n\n{redirect.get('body', '')}"
        if redirect else
        f"Auto-closed: {verdict}"
    )

    intake_flags = {
        "verdict": verdict,
        "thread_detected": intake.thread.is_thread,
        "is_external_provider": intake.provider.is_external_provider,
        "is_off_topic": intake.provider.is_off_topic,
        "competitor_provider": intake.provider.competitor_provider,
        "off_topic_domain": intake.provider.off_topic_domain,
        "external_confidence": intake.provider.confidence,
        "external_reason": intake.provider.reason,
        "information_density": intake.density.density,
        "redirect_subject": redirect.get("subject"),
        "redirect_body": redirect.get("body"),
    }

    case = Case(
        case_id=case_id,
        source_type=ingested.source_type,
        classification=classification,
        workflow_state="CLOSED",
        primary_department=dept,
        secondary_departments=[],
        customer_metadata={},
        email_metadata={"source_filename": "unknown", "raw_email_hash": ingested.raw_hash, "file_meta": file_meta or {}},
        extracted_fields={},
        ai_analysis={
            "classification": classification,
            "verdict": verdict,
            "auto_closed_reason": intake.provider.reason or intake.density.density,
        },
        confidence_score=1.0,
        risk_score=0.0,
        priority=priority,
        sla_metadata=build_sla_metadata(priority),
        escalation_state={},
        linked_cases=[],
        attachments=[],
        raw_email_hash=ingested.raw_hash,
        raw_email_path=str(ingested.raw_path),
        raw_email=ingested.email_input.raw_bytes.decode("utf-8", errors="replace"),
        normalized_text=ingested.normalized_text,
        is_parent=False,
        parent_case_id=None,
        child_case_ids=[],
        audit_history=[],
        unified_timeline=[],
        needs_human_triage=False,
        language="English",
        triage_labels=[],
        intake_flags=intake_flags,
        resolution_text=resolution,
        created_at=now,
        updated_at=now,
    )
    db.add(case)
    db.flush()

    append_event(
        db, case,
        actor="intake_triage", role="system",
        action="auto_closed",
        message=f"Case auto-closed: {verdict}. {intake.provider.reason or ''}",
        metadata=intake_flags,
        new_state={"workflow_state": "CLOSED", "classification": classification},
    )
    db.commit()
    return case


# ── Email signal extraction ─────────────────────────────────────────────────

def _build_email_signals(
    classification: str,
    triage: dict[str, Any],
    ai: dict[str, Any],
    duplicates: list,
) -> dict[str, Any]:
    """
    Derive normalised signal dict from AI/triage output.
    Used by all department priority resolvers for this email.
    """
    text = f"{triage.get('parsed_text', '')} {triage.get('llm_summary', '')}".lower()
    words = [w.strip(".,;:!?()[]\"'") for w in text.split() if len(w.strip(".,;:!?()[]\"'")) > 3]

    # Parse monetary amount from triage case_form or BERT
    amount: float | None = None
    raw_amount = (
        triage.get("case_form", {}).get("amount_involved")
        or ai.get("bert_analysis", {}).get("primary_amount")
    )
    if raw_amount:
        try:
            amount = float(
                str(raw_amount)
                .replace(",", "")
                .replace("₹", "")
                .replace("$", "")
                .replace("INR", "")
                .strip()
            )
        except (ValueError, TypeError):
            pass

    regulatory_terms = {"regulator", "ombudsman", "court", "legal notice", "tribunal", "judgment"}
    regulatory_flag = bool(
        triage.get("regulatory_flag")
        or any(term in text for term in regulatory_terms)
    )

    # triage_system.py does not expose sentiment in its output dict, so derive it here
    # from secondary_tags and text keywords to feed dept priority rules.
    secondary_tags = set(triage.get("secondary_tags") or [])
    _neg_words = {
        "frustrated", "disgusted", "unacceptable", "worst", "terrible", "outrageous",
        "disappointed", "pathetic", "horrible", "shocking", "incompetent", "useless",
        "furious", "angry", "cheated", "fraud", "scam", "mislead", "lied",
        "not resolved", "no response", "multiple times", "still waiting",
        "escalate", "consumer forum", "legal action", "file complaint",
    }
    _pos_words = {"thank you", "thanks", "appreciate", "happy", "satisfied", "resolved", "helpful"}
    if any(w in text for w in _neg_words) or "URGENCY-STATED" in secondary_tags:
        derived_sentiment = "NEGATIVE"
    elif any(w in text for w in _pos_words) or "POSITIVE-FEEDBACK" in secondary_tags:
        derived_sentiment = "POSITIVE"
    else:
        derived_sentiment = "NEUTRAL"

    return {
        "category": classification,
        "sender_type": triage.get("case_form", {}).get("customer_segment", "individual"),
        "keywords": words[:100],
        "monetary_amount_detected": amount,
        "regulatory_flag": regulatory_flag,
        "repeat_flag": len(duplicates) > 0,
        "sentiment": derived_sentiment,
    }


# ── Shared audit helpers ────────────────────────────────────────────────────

def _common_audit_events(
    db: Session,
    case: Case,
    triage: dict,
    classification: str,
    primary_department: str,
    secondary_departments: list,
    ai_analysis: dict,
    ai_confidence: float,
    route_scores: dict,
    actor: str,
) -> None:
    append_event(
        db, case, actor=actor, role="intake_operator",
        action="email_received",
        message="Email received by central operations console",
        metadata={"source_type": case.source_type, "filename": case.email_metadata.get("source_filename")},
    )
    append_event(
        db, case, actor="bert+local-llm:e2b", role="ai_model",
        action="triage_completed",
        message=(
            f"Hybrid triage: {classification} "
            f"[{ai_analysis.get('adjudication_mode', 'unknown')}]"
        ),
        metadata={
            "confidence_score": ai_confidence,
            "bert_classification": ai_analysis.get("bert_classification"),
            "gemma_classification": ai_analysis.get("gemma_classification"),
            "adjudication_mode": ai_analysis.get("adjudication_mode"),
            "routing_scores": route_scores,
        },
    )
    append_event(
        db, case, actor="routing_engine", role="system",
        action="case_routed",
        message=f"Case routed → primary: {primary_department}",
        metadata={
            "primary_department": primary_department,
            "secondary_departments": secondary_departments,
        },
        new_state={"workflow_state": case.workflow_state, "primary_department": primary_department},
    )

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import cast, func, or_, String
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .database import SessionLocal, get_db, init_db
from .models import AuditEvent, Case, CaseDepartment, Incident, IntakeError, MLFeedback, TimelineEvent
from .schemas import (
    DeptQueueMoveRequest, PortalSubmitRequest, PortalSubmitResponse,
    QueueMoveRequest, QueueReorderRequest, TextIngestRequest, TriageReleaseRequest, WorkflowActionRequest,
)
from .security import ROLES, create_access_token, current_user, require_roles
from .services.ai_analysis import DEFAULT_MODEL
from .services.audit import append_event
from .services.historical import index_case, similarity_search
from .services.notification import manager
from .services.orchestration import create_case_from_email
from .services.priority import PRIORITY_RANK
from .services.readiness import system_status
from .services.sla import SLA_MINUTES, all_sla_alerts, build_sla_metadata, sla_alerts_for_case
from .services.risk_engine import aging_contribution
from .services.workflow import apply_workflow_action, workflow_capabilities
from .services.admin_assistant import answer as assistant_answer


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_db()
    rerank_task = asyncio.create_task(_sla_rerank_loop())
    logger.info("Docket operations API ready")
    yield
    rerank_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await rerank_task


app = FastAPI(title="Docket AI/ML Email Segregation Operations Platform", version="1.0.0", lifespan=_lifespan)
logger = logging.getLogger("docket.operations")
MAX_EMAIL_BYTES = 2_000_000

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _sanitize(obj: Any) -> Any:
    """Recursively strip control characters from string values in JSON-safe structures."""
    if isinstance(obj, str):
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", obj)
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(item) for item in obj]
    return obj


def slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")


def department_label_from_slug(db: Session, value: str) -> str:
    target = slug(value)
    for row in db.query(CaseDepartment.department).distinct().all():
        if slug(row[0]) == target:
            return row[0]
    labels = {
        "fraud-risk-monitoring-desk": "Trust & Safety Desk",
        "customer-service-resolution-desk": "Customer Resolution Desk",
        "compliance-regulatory-desk": "Compliance Desk",
        "technical-digital-service-support-desk": "Network Support Desk",
        "central-operations-escalation-desk": "Central Operations Desk",
        "fraud": "Trust & Safety Desk",
        "technical": "Network Support Desk",
        "compliance": "Compliance Desk",
        "customer-service": "Customer Resolution Desk",
        "central-operations": "Central Operations Desk",
    }
    return labels.get(target, value.replace("-", " ").title())


def department_to_dict(dep: CaseDepartment) -> dict[str, Any]:
    return {
        "department": dep.department,
        "department_role": dep.department_role,
        "routing_confidence": dep.routing_confidence,
        "status": dep.status,
        "priority": dep.priority,           # dept-local priority
        "assigned_operator": dep.assigned_operator,
        "sla_due_at": dt(dep.sla_due_at),
        "findings": dep.findings or {},
        "internal_notes": dep.internal_notes or [],
        "updated_at": dt(dep.updated_at),
    }


_RESOLUTION_KEYWORDS: list[tuple[str, list[str]]] = [
    ("Account blocked",       ["block", "suspend", "freeze", "deactivat"]),
    ("Refund processed",      ["refund", "reversal", "credit back", "reimburs"]),
    ("Escalated",             ["escalat", "senior officer", "nodal", "ombudsman"]),
    ("Callback arranged",     ["call back", "callback", "telephon", "called customer"]),
    ("Complaint closed",      ["complet", "resolv", "clos", "redress"]),
    ("Document requested",    ["document", "verification", "proof", "submit"]),
    ("Technical fix applied", ["technical", "system", "glitch", "fix", "restor"]),
    ("instalment / contract adjusted",   ["instalment", "contract", "waiv", "moratorium", "restructur"]),
]

def _synthesise_solutions(suggestions: list[dict]) -> dict[str, Any] | None:
    """Produce a rule-based pattern summary from a list of similar resolved cases."""
    if len(suggestions) < 2:
        return None
    from collections import Counter
    cls_count: Counter = Counter(s["classification"] for s in suggestions)
    dept_count: Counter = Counter(s.get("primary_department") for s in suggestions if s.get("primary_department"))
    action_count: Counter = Counter()
    for s in suggestions:
        text = (s.get("resolution_text") or "").lower()
        for label, patterns in _RESOLUTION_KEYWORDS:
            if any(p in text for p in patterns):
                action_count[label] += 1

    top_cls = cls_count.most_common(3)
    top_actions = action_count.most_common(3)
    top_dept = dept_count.most_common(1)[0][0] if dept_count else None

    # Build a plain-English headline
    n = len(suggestions)
    dominant_cls, dominant_count = top_cls[0]
    headline = (
        f"{n} similar past {'case was' if n == 1 else 'cases were'} resolved. "
        f"Most were classified as {dominant_cls.replace('_', ' ').title()} "
        f"({dominant_count}/{n})."
    )
    if top_actions:
        headline += " Common resolutions: " + "; ".join(f"{lbl} ({cnt}×)" for lbl, cnt in top_actions) + "."

    return {
        "total_similar": n,
        "headline": headline,
        "top_classifications": [{"label": l, "count": c} for l, c in top_cls],
        "top_actions": [{"label": l, "count": c} for l, c in top_actions],
        "top_department": top_dept,
        "avg_similarity": round(sum(s["similarity"] for s in suggestions) / n, 3),
    }


def case_summary(case: Case) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "classification": case.classification,
        "workflow_state": case.workflow_state,
        "priority": case.priority,
        "risk_score": case.risk_score,
        "confidence_score": case.confidence_score,
        "primary_department": case.primary_department,
        "secondary_departments": case.secondary_departments or [],
        "summary": (case.ai_analysis or {}).get("raw_triage", {}).get("llm_summary") or (case.extracted_fields or {}).get("service_summary"),
        "amount_involved": (case.extracted_fields or {}).get("amount_involved"),
        "customer": (case.extracted_fields or {}).get("customer_name") or (case.customer_metadata or {}).get("sender_email_masked"),
        "sla_alerts": sla_alerts_for_case(case),
        "incident_group": case.incident_group,
        "created_at": dt(case.created_at),
        "updated_at": dt(case.updated_at),
        # Parent-child linkage
        "is_parent": bool(case.is_parent),
        "parent_case_id": case.parent_case_id,
        "child_count": len(case.child_case_ids or []),
        # Queue management
        "queue_position": case.queue_position,
        "queue_pinned": bool(case.queue_pinned),
        "resolution_text": case.resolution_text,
        # Human triage & multi-label
        "needs_human_triage": bool(case.needs_human_triage),
        "language": case.language or "English",
        "triage_labels": case.triage_labels or [],
        # Smart intake flags
        "intake_flags": case.intake_flags or {},
        # Customer linkage
        "customer_customer_ref": case.customer_customer_ref,
        # SLA metadata (needed for resolved/closed chip display)
        "sla_metadata": case.sla_metadata or {},
    }


def case_to_dict(case: Case, db: Session | None = None) -> dict[str, Any]:
    d: dict[str, Any] = _sanitize({
        **case_summary(case),
        "source_type": case.source_type,
        "customer_metadata": case.customer_metadata or {},
        "email_metadata": case.email_metadata or {},
        "extracted_fields": case.extracted_fields or {},
        "ai_analysis": case.ai_analysis or {},
        "sla_metadata": case.sla_metadata or {},
        "escalation_state": case.escalation_state or {},
        "linked_cases": case.linked_cases or [],
        "child_case_ids": case.child_case_ids or [],
        "attachments": case.attachments or [],
        "raw_email_hash": case.raw_email_hash,
        "raw_email_path": case.raw_email_path,
        "raw_email": case.raw_email or "",
        "normalized_text": case.normalized_text or "",
        "departments": [department_to_dict(dep) for dep in case.departments],
        "audit_history": case.audit_history or [],
        "unified_timeline": case.unified_timeline or [],
        "intake_flags": case.intake_flags or {},
        **workflow_capabilities(case),
    })
    # If this is a parent, include child summaries (read-only snapshot for the UI)
    if db and case.is_parent and case.child_case_ids:
        children = db.query(Case).filter(Case.case_id.in_(case.child_case_ids)).all()
        d["children"] = [case_summary(c) for c in children]
    # If this is a child, include sibling summaries (read-only) + parent summary
    elif db and case.parent_case_id:
        parent = db.query(Case).filter(Case.case_id == case.parent_case_id).one_or_none()
        if parent:
            d["parent_summary"] = case_summary(parent)
            siblings = [c for c in
                        db.query(Case).filter(Case.parent_case_id == case.parent_case_id).all()
                        if c.case_id != case.case_id]
            d["sibling_cases"] = [case_summary(s) for s in siblings]

    # BERT-powered suggested solutions from similar closed cases
    if db and case.normalized_text:
        try:
            similar_refs = similarity_search(db, case.normalized_text, limit=25, allow_model_load=False)
            closed_ids = {s["case_id"] for s in similar_refs if s["case_id"] != case.case_id}
            if closed_ids:
                closed_cases = {
                    c.case_id: c
                    for c in db.query(Case).filter(
                        Case.case_id.in_(closed_ids),
                        Case.workflow_state.in_(["CLOSED", "RESOLVED"]),
                        Case.resolution_text.isnot(None),
                    ).all()
                    if c.resolution_text and c.resolution_text.strip()
                }
                suggestions = []
                for ref in similar_refs:
                    cid = ref["case_id"]
                    if cid in closed_cases:
                        cc = closed_cases[cid]
                        suggestions.append({
                            "case_id": cid,
                            "classification": ref["classification"],
                            "similarity": ref["similarity"],
                            "similarity_method": ref.get("similarity_method", ""),
                            "resolution_text": cc.resolution_text,
                            "summary": (cc.ai_analysis or {}).get("raw_triage", {}).get("llm_summary"),
                            "primary_department": cc.primary_department,
                            "created_at": dt(cc.created_at),
                            "updated_at": dt(cc.updated_at),
                        })
                d["suggested_solutions"] = suggestions[:10]
                d["solutions_synthesis"] = _synthesise_solutions(suggestions)
            else:
                d["suggested_solutions"] = []
                d["solutions_synthesis"] = None
        except Exception:
            d["suggested_solutions"] = []
            d["solutions_synthesis"] = None
    else:
        d["suggested_solutions"] = []
        d["solutions_synthesis"] = None

    return d


def _write_intake_error(db: Session, exc: Exception, raw: bytes, filename: str, actor: str) -> None:
    """Persist a failed intake to the dead-letter queue."""
    import hashlib, uuid as _uuid, traceback as _tb
    error_id = "ERR-" + _uuid.uuid4().hex[:12].upper()
    raw_hash = hashlib.sha256(raw).hexdigest() if raw else None
    preview = raw[:500].decode("utf-8", errors="replace") if raw else None
    db.add(IntakeError(
        error_id=error_id,
        source_filename=filename,
        raw_email_hash=raw_hash,
        raw_email_preview=preview,
        error_type=type(exc).__name__,
        error_detail=_tb.format_exc()[-2000:],
        actor=actor,
        retryable=True,
        retry_count=0,
        resolved=False,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    ))
    logger.warning("Intake error written to dead-letter queue error_id=%s", error_id)


def _refresh_risk_aging(case: Case, now: datetime) -> bool:
    """
    Re-apply the SLA-aging term to a waiting case's risk_score so it re-floats in
    the queue as it ages. Adds the decay on top of the stored pre-aging composite
    score (ai_analysis.risk_assessment.score) without rebuilding the full signals.
    Returns True if risk_score moved enough to matter.
    """
    base = (case.ai_analysis or {}).get("risk_assessment", {}).get("score")
    if base is None:
        base = (case.risk_score or 0.0) * 100.0
    created = case.created_at
    if created and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if not created:
        return False
    age_min = (now - created).total_seconds() / 60.0
    res_min = SLA_MINUTES.get(case.priority or "LOW", SLA_MINUTES["LOW"])["resolution"]
    new_norm = min(1.0, (base + aging_contribution(age_min, res_min)) / 100.0)
    if abs(new_norm - (case.risk_score or 0.0)) >= 0.005:
        case.risk_score = new_norm
        return True
    return False


async def _sla_rerank_loop() -> None:
    """Every 60 s: re-float aging cases by risk_score, unpin OVERDUE cases, and broadcast."""
    while True:
        await asyncio.sleep(60)
        try:
            db = SessionLocal()
            try:
                active = (
                    db.query(Case)
                    .filter(
                        or_(Case.parent_case_id == None, Case.parent_case_id == ""),  # noqa: E711
                        Case.workflow_state.notin_(["RESOLVED", "CLOSED"]),
                    )
                    .all()
                )
                now = datetime.now(timezone.utc)
                changed = False
                for case in active:
                    if _refresh_risk_aging(case, now):
                        changed = True
                    if case.queue_pinned:
                        alerts = sla_alerts_for_case(case)
                        if any(a["severity"] == "OVERDUE" for a in alerts):
                            case.queue_pinned = False
                            case.queue_position = None
                            changed = True
                if changed:
                    db.commit()
                overdue_exists = any(
                    any(a["severity"] == "OVERDUE" for a in sla_alerts_for_case(c))
                    for c in active
                )
                if overdue_exists or changed:
                    sorted_cases = sorted(active, key=_queue_sort_key)
                    await manager.broadcast(
                        "queue_reranked",
                        [case_summary(c) for c in sorted_cases],
                    )
            finally:
                db.close()
        except Exception as exc:
            logger.error("SLA rerank loop error: %s", exc)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "core": "case triage", "pipeline": "bert+ollama"}


@app.get("/api/v1/system/readiness")
def readiness(db: Session = Depends(get_db), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    result = system_status(probe=False)
    result["database"] = {"status": "ready", "case_count": db.query(Case).count()}
    return result


@app.post("/api/v1/system/readiness/verify")
def verify_readiness(db: Session = Depends(get_db), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    logger.info("Model diagnostics requested by %s", user["username"])
    result = system_status(probe=True)
    result["database"] = {"status": "ready", "case_count": db.query(Case).count()}
    return result


@app.get("/api/v1/auth/dev-token")
def dev_token(username: str = "local_admin", role: str = "admin") -> dict[str, str]:
    if role not in ROLES:
        raise HTTPException(status_code=400, detail=f"Unknown role '{role}'. Valid roles: {', '.join(sorted(ROLES))}")
    return {"access_token": create_access_token(username, role), "token_type": "bearer"}


@app.post("/api/v1/intake/text")
async def ingest_text(
    payload: TextIngestRequest,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(require_roles("intake_operator", "escalation_manager")),
) -> dict[str, Any]:
    import hashlib, uuid as _uuid
    raw = payload.text.encode("utf-8")
    if len(raw) > MAX_EMAIL_BYTES:
        raise HTTPException(status_code=413, detail="Email exceeds the 2 MB intake limit.")
    logger.info("Text intake started filename=%s actor=%s bytes=%d", payload.filename, user["username"], len(raw))
    try:
        case = await run_in_threadpool(
            create_case_from_email,
            db,
            raw_bytes=raw,
            filename=payload.filename,
            actor=user["username"],
        )
    except Exception as exc:
        logger.exception("Text intake AI pipeline failed filename=%s", payload.filename)
        _write_intake_error(db, exc, raw, payload.filename, user["username"])
        db.commit()
        raise HTTPException(
            status_code=503,
            detail="Triage failed. Case saved in the error queue — retry from the Error Queue view.",
        ) from exc
    db.commit()
    db.refresh(case)
    logger.info("Text intake completed case_id=%s classification=%s needs_triage=%s",
                case.case_id, case.classification, case.needs_human_triage)
    await manager.broadcast("case_created", case_summary(case))
    return case_to_dict(case, db)


@app.post("/api/v1/intake/email")
async def ingest_file(
    file: UploadFile = File(...),
    actor: str = Form(default="intake_operator"),
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(require_roles("intake_operator", "escalation_manager")),
) -> dict[str, Any]:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="Uploaded email file is empty.")
    if len(raw) > MAX_EMAIL_BYTES:
        raise HTTPException(status_code=413, detail="Email exceeds the 2 MB intake limit.")
    filename = file.filename or "email.eml"
    logger.info("File intake started filename=%s actor=%s bytes=%d", filename, user["username"], len(raw))
    try:
        case = await run_in_threadpool(
            create_case_from_email,
            db,
            raw_bytes=raw,
            filename=filename,
            actor=user["username"] or actor,
        )
    except Exception as exc:
        logger.exception("File intake AI pipeline failed filename=%s", filename)
        _write_intake_error(db, exc, raw, filename, user["username"] or actor)
        db.commit()
        raise HTTPException(
            status_code=503,
            detail="Triage failed. Case saved in the error queue — retry from the Error Queue view.",
        ) from exc
    db.commit()
    db.refresh(case)
    logger.info("File intake completed case_id=%s classification=%s needs_triage=%s",
                case.case_id, case.classification, case.needs_human_triage)
    await manager.broadcast("case_created", case_summary(case))
    return case_to_dict(case, db)


def _sla_rank(case: Case) -> int:
    """0 = OVERDUE, 1 = NEARING_BREACH, 2 = healthy."""
    alerts = sla_alerts_for_case(case)
    if any(a["severity"] == "OVERDUE" for a in alerts):
        return 0
    if any(a["severity"] == "NEARING_BREACH" for a in alerts):
        return 1
    return 2


def _queue_sort_key(case: Case) -> tuple:
    """
    Sort key for the central intake queue.

    Pinned cases (operator-set positions) come first, ordered by queue_position.
    Unpinned cases auto-rank: OVERDUE SLA → NEARING_BREACH → priority → risk_score → created_at.
    """
    if case.queue_pinned and case.queue_position is not None:
        return (0, case.queue_position, 0, 0, "")
    sla = _sla_rank(case)
    pri = 3 - PRIORITY_RANK.get(case.priority or "LOW", 0)   # lower = more urgent
    return (1, sla, pri, -(case.risk_score or 0), str(case.created_at or ""))


def _apply_queue_move(
    active: list[Case],
    case_id: str,
    direction: str,
) -> tuple[Case, int | None, int, int]:
    """
    Normalize the currently visible queue to 1-N positions, then swap the
    target with its neighbour. This preserves the exact one-step move the
    operator requested, including for legacy rows with NULL queue_position.
    """
    idx = next((i for i, row in enumerate(active) if row.case_id == case_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Case not found in the active queue.")
    if direction == "up" and idx == 0:
        raise HTTPException(status_code=409, detail="Case is already at the top of the queue.")
    if direction == "down" and idx == len(active) - 1:
        raise HTTPException(status_code=409, detail="Case is already at the bottom of the queue.")

    target = active[idx]
    previous_position = target.queue_position
    previous_order = idx + 1
    neighbour_idx = idx - 1 if direction == "up" else idx + 1

    for position, row in enumerate(active, start=1):
        row.queue_position = position

    neighbour = active[neighbour_idx]
    target.queue_position, neighbour.queue_position = neighbour.queue_position, target.queue_position
    target.queue_pinned = True
    neighbour.queue_pinned = True
    return target, previous_position, previous_order, neighbour_idx + 1


@app.get("/api/v1/central/dashboard")
def central_dashboard(db: Session = Depends(get_db), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    # Central console shows parents + standalone cases — never raw children
    cases = (
        db.query(Case)
        .filter(or_(Case.parent_case_id == None, Case.parent_case_id == ""))  # noqa: E711
        .all()
    )
    priority_counts = dict(
        db.query(Case.priority, func.count(Case.id))
        .filter(or_(Case.parent_case_id == None, Case.parent_case_id == ""))  # noqa: E711
        .group_by(Case.priority).all()
    )
    classification_counts = dict(
        db.query(Case.classification, func.count(Case.id))
        .filter(or_(Case.parent_case_id == None, Case.parent_case_id == ""))  # noqa: E711
        .group_by(Case.classification).all()
    )
    department_rows = (
        db.query(CaseDepartment.department, CaseDepartment.status, func.count(CaseDepartment.id))
        .group_by(CaseDepartment.department, CaseDepartment.status)
        .all()
    )
    workloads: dict[str, dict[str, Any]] = {}
    for department, status, count in department_rows:
        workloads.setdefault(department, {"department": department, "total": 0, "statuses": {}})
        workloads[department]["total"] += count
        workloads[department]["statuses"][status] = count
    confidence_values = [case.confidence_score or 0.0 for case in cases]
    avg_confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0
    incidents = db.query(Incident).order_by(Incident.updated_at.desc()).limit(20).all()
    return {
        "counts": {
            "total_cases": len(cases),
            "priority": priority_counts,
            "classification": classification_counts,
            "open_cases": sum(1 for case in cases if case.workflow_state not in {"RESOLVED", "CLOSED"}),
            "critical_unresolved": sum(1 for case in cases if case.priority == "CRITICAL" and case.workflow_state not in {"RESOLVED", "CLOSED"}),
            "needs_human_triage": db.query(Case).filter(Case.needs_human_triage.is_(True), Case.workflow_state == "HUMAN_TRIAGE").count(),
            "error_queue": db.query(IntakeError).filter(IntakeError.resolved.is_(False)).count(),
        },
        "incoming_queue": [
            case_summary(c)
            for c in sorted(
                (c for c in cases if c.workflow_state not in {"RESOLVED", "CLOSED"}),
                key=_queue_sort_key,
            )[:50]
        ],
        "closed_queue": [
            case_summary(c)
            for c in sorted(
                (c for c in cases if c.workflow_state in {"RESOLVED", "CLOSED"}),
                key=lambda c: str(c.updated_at or ""),
                reverse=True,
            )[:50]
        ],
        "classification_stream": [
            {
                "case_id": case.case_id,
                "classification": case.classification or "Unclassified",
                "confidence_score": case.confidence_score,
                "primary_department": case.primary_department,
                "workflow_state": case.workflow_state,
                "priority": case.priority,
                "created_at": dt(case.created_at),
            }
            for case in sorted(cases, key=lambda c: str(c.created_at or ""), reverse=True)[:12]
        ],
        "sla_alerts": all_sla_alerts(db)[:50],
        "escalation_alerts": [case_summary(case) for case in cases if (case.escalation_state or {}).get("is_escalated")][:30],
        "department_workloads": list(workloads.values()),
        "incident_clusters": [
            {
                "incident_id": incident.incident_id,
                "title": incident.title,
                "status": incident.status,
                "case_count": len(incident.case_ids or []),
                "pattern_key": incident.pattern_key,
            }
            for incident in incidents
        ],
        "ai_confidence_metrics": {
            "average_confidence": round(avg_confidence, 3),
            "low_confidence_cases": sum(1 for case in cases if (case.confidence_score or 0.0) < 0.65),
        },
        "operational_heatmap": list(workloads.values()),
    }


@app.get("/api/v1/cases")
def list_cases(db: Session = Depends(get_db), user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    # Exclude child sub-cases from the top-level list
    rows = (
        db.query(Case)
        .filter(or_(Case.parent_case_id == None, Case.parent_case_id == ""))  # noqa: E711
        .order_by(Case.created_at.desc())
        .limit(500)
        .all()
    )
    return [case_summary(c) for c in rows]


# NOTE: /cases/search MUST be registered before /cases/{case_id} so FastAPI
# doesn't swallow "search" as a case_id path parameter.
@app.get("/api/v1/cases/search")
def case_search(
    q: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> list[dict[str, Any]]:
    """
    Keyword search across all cases (including resolved/closed).
    Matches: case_id, customer_customer_ref, customer name, sender email, classification, normalized_text.
    """
    term = q.strip()
    if not term:
        return []
    like = f"%{term}%"
    rows = (
        db.query(Case)
        .filter(
            or_(
                Case.case_id.ilike(like),
                Case.customer_customer_ref.ilike(like),
                Case.classification.ilike(like),
                cast(Case.extracted_fields, String).ilike(like),
                cast(Case.customer_metadata, String).ilike(like),
                cast(Case.email_metadata, String).ilike(like),
                Case.normalized_text.ilike(like),
            )
        )
        .order_by(Case.created_at.desc())
        .limit(60)
        .all()
    )
    return [case_summary(c) for c in rows]


@app.get("/api/v1/cases/{case_id}")
def get_case(case_id: str, audit: bool = False, db: Session = Depends(get_db), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    case = db.query(Case).filter(Case.case_id == case_id).one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if audit:
        append_event(
            db,
            case,
            actor=user["username"],
            role=user["role"],
            action="case_viewed",
            message="Case viewed",
            metadata={"surface": "api"},
        )
        db.commit()
        db.refresh(case)
    return case_to_dict(case, db)


@app.get("/api/v1/cases/{case_id}/children")
def case_children(
    case_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> list[dict[str, Any]]:
    """
    Return child sub-cases for a parent case, each with their own workflow state,
    SLA, and department info.  Siblings can also use this endpoint to discover
    each other: call /children on parent_case_id.
    """
    parent = db.query(Case).filter(Case.case_id == case_id).one_or_none()
    if not parent:
        raise HTTPException(status_code=404, detail="Case not found")
    children = db.query(Case).filter(Case.parent_case_id == case_id).all()
    return [
        {
            **case_summary(child),
            "sla_metadata": child.sla_metadata or {},
            "departments": [department_to_dict(dep) for dep in child.departments],
            "ai_analysis": {
                k: v for k, v in (child.ai_analysis or {}).items()
                if k in ("bert_classification", "bert_confidence", "gemma_classification",
                         "adjudication_mode", "fraud_indicators", "extracted_entities")
            },
            "audit_history": (child.audit_history or [])[-5:],  # last 5 events
        }
        for child in children
    ]


@app.get("/api/v1/departments")
def departments(db: Session = Depends(get_db), user: dict[str, Any] = Depends(current_user)) -> list[dict[str, str]]:
    labels = [row[0] for row in db.query(CaseDepartment.department).distinct().all()]
    if not labels:
        labels = [
            "Trust & Safety Desk",
            "Customer Resolution Desk",
            "Compliance Desk",
            "Network Support Desk",
            "Central Operations Desk",
        ]
    return [{"department": label, "slug": slug(label)} for label in labels]


@app.get("/api/v1/departments/{department}/queue")
def department_queue(department: str, db: Session = Depends(get_db), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    label = department_label_from_slug(db, department)
    rows = (
        db.query(CaseDepartment)
        .filter(CaseDepartment.department == label)
        .order_by(CaseDepartment.updated_at.desc())
        .all()
    )
    # Department queues show child sub-cases and standalone cases — never parent cases
    case_map = {
        c.case_id: c
        for c in db.query(Case).filter(
            Case.case_id.in_([row.case_id for row in rows]),
            Case.is_parent.is_(False),   # exclude parent cases
        ).all()
    } if rows else {}
    items = []
    for row in rows:
        case = case_map.get(row.case_id)
        if not case:
            continue
        items.append(
            {
                **case_summary(case),
                "department_role": row.department_role,
                "department_status": row.status,
                # dept-local priority — may differ from Case.priority (global aggregate)
                "department_priority": row.priority,
                "assigned_operator": row.assigned_operator,
                "routing_confidence": row.routing_confidence,
                "findings": row.findings or {},
                "internal_notes": row.internal_notes or [],
            }
        )
    # Split into active vs closed before sorting.
    # HUMAN_TRIAGE cases are excluded from the department queue — they live in the
    # central triage queue until a reviewer releases them (→ ASSIGNED).
    _terminal = {"RESOLVED", "CLOSED"}
    active_items = [i for i in items if i["workflow_state"] not in _terminal and i["workflow_state"] != "HUMAN_TRIAGE"]
    pending_triage_items = [i for i in items if i["workflow_state"] == "HUMAN_TRIAGE"]
    closed_items = [i for i in items if i["workflow_state"] in _terminal]

    def _dept_item_sort_key(item: dict) -> tuple:
        # Pinned cases keep their operator-set order first
        if item.get("queue_pinned") and item.get("queue_position") is not None:
            return (0, item["queue_position"], 0, 0, "")
        # Auto-rank: OVERDUE SLA → NEARING_BREACH → priority → created_at
        alerts = item.get("sla_alerts") or []
        sla = (0 if any(a.get("severity") == "OVERDUE" for a in alerts)
               else 1 if any(a.get("severity") == "NEARING_BREACH" for a in alerts)
               else 2)
        pri = 3 - PRIORITY_RANK.get(item.get("department_priority") or "LOW", 0)
        return (1, sla, pri, 0, item.get("created_at") or "")

    sorted_active = sorted(active_items, key=_dept_item_sort_key)
    # Sort closed by updated_at descending (most recently closed first)
    sorted_closed = sorted(
        closed_items,
        key=lambda item: item.get("updated_at") or "",
        reverse=True,
    )

    # Department-specific extended counts (over active items only for operational metrics)
    amount_at_risk = sum(float(i.get("amount_involved") or 0) for i in active_items)
    blocked = sum(1 for i in active_items if i.get("findings", {}).get("service_suspended"))
    forensics = sum(1 for i in active_items if i.get("findings", {}).get("forensics_requested"))
    it_tickets = sum(1 for i in active_items if i.get("findings", {}).get("it_ticket_created"))
    reg_reports = sum(1 for i in active_items if i.get("findings", {}).get("regulatory_report_raised"))
    resolutions = sum(1 for i in active_items if i.get("findings", {}).get("resolution_offered"))
    return {
        "department": label,
        "slug": slug(label),
        "items": sorted_active,
        "pending_triage_items": pending_triage_items,
        "closed_items": sorted_closed,
        "counts": {
            "total": len(active_items),
            "total_closed": len(closed_items),
            "pending_triage": len(pending_triage_items),
            # Critical count uses dept-local priority, not global Case.priority
            "critical": sum(1 for item in active_items if item["department_priority"] == "CRITICAL"),
            "overdue": sum(1 for item in active_items if item["sla_alerts"]),
            "unresolved": len(active_items),
            "escalated": sum(1 for item in active_items if item["workflow_state"] == "ESCALATED"),
            "resolved": len(closed_items),
            "under_review": sum(1 for item in active_items if item["workflow_state"] == "UNDER_REVIEW"),
            "waiting": sum(1 for item in active_items if item["workflow_state"] == "WAITING_FOR_ACTION"),
            # Desk-specific aggregates
            "amount_at_risk": round(amount_at_risk, 2),
            "lines_suspended": blocked,
            "forensics_requested": forensics,
            "it_tickets_created": it_tickets,
            "regulatory_reports": reg_reports,
            "resolutions_offered": resolutions,
        },
    }


@app.post("/api/v1/departments/{department}/queue-move")
async def dept_queue_move(
    department: str,
    body: DeptQueueMoveRequest,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """
    Promote or demote a case within a specific department's active queue.

    Fetches the department's active queue in its current sort order, normalises
    queue_position values to 1-N integers on every case in the list, then swaps
    the target case with its neighbour in the requested direction.
    """
    label = department_label_from_slug(db, department)

    # Fetch all CaseDepartment records for this dept and their Case objects
    dept_rows = (
        db.query(CaseDepartment)
        .filter(CaseDepartment.department == label)
        .all()
    )
    case_ids = [r.case_id for r in dept_rows]
    if not case_ids:
        raise HTTPException(status_code=404, detail="No cases found for this department.")

    case_map = {
        c.case_id: c
        for c in db.query(Case).filter(
            Case.case_id.in_(case_ids),
            Case.is_parent.is_(False),
        ).all()
    }
    dept_row_map = {r.case_id: r for r in dept_rows}

    # Build active queue with same sort as department_queue endpoint.
    # Exclude HUMAN_TRIAGE — those cases aren't actionable at the desk yet.
    active: list[Case] = []
    for cid, case in case_map.items():
        if case.workflow_state not in {"RESOLVED", "CLOSED", "HUMAN_TRIAGE"}:
            active.append(case)

    def _dept_move_sort_key(c: Case) -> tuple:
        dept_pri = (dept_row_map.get(c.case_id) or CaseDepartment()).priority or "LOW"
        if c.queue_pinned and c.queue_position is not None:
            return (0, c.queue_position, 0, 0, "")
        alerts = sla_alerts_for_case(c)
        sla = (0 if any(a["severity"] == "OVERDUE" for a in alerts)
               else 1 if any(a["severity"] == "NEARING_BREACH" for a in alerts)
               else 2)
        pri = 3 - PRIORITY_RANK.get(dept_pri, 0)
        return (1, sla, pri, 0, str(c.created_at or ""))

    active.sort(key=_dept_move_sort_key)

    try:
        target, previous_position, previous_order, new_order = _apply_queue_move(active, body.case_id, body.direction)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail="Case not found in this department's active queue.") from exc
        raise

    append_event(
        db, target,
        actor=user["username"], role=user["role"],
        action="queue_move",
        message=f"Queue position moved {body.direction} within {label} by {user['username']}",
        metadata={
            "direction": body.direction,
            "department": label,
            "previous_order": previous_order,
            "new_order": new_order,
            "new_position": target.queue_position,
            "queue_length": len(active),
        },
        previous_state={"queue_position": previous_position, "queue_order": previous_order},
        new_state={"queue_position": target.queue_position, "queue_order": new_order},
    )

    db.commit()
    db.refresh(target)
    await manager.broadcast("case_updated", case_summary(target))
    return {
        "case_id": body.case_id,
        "new_position": target.queue_position,
        "queue_length": len(active),
    }


@app.post("/api/v1/departments/{department}/queue-reorder")
async def dept_queue_reorder(
    department: str,
    body: QueueReorderRequest,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """
    Drag-and-drop reorder: move case_id to absolute new_index in the active queue.
    Normalises all queue_position values so positions stay sequential.
    """
    label = department_label_from_slug(db, department)
    dept_rows = db.query(CaseDepartment).filter(CaseDepartment.department == label).all()
    case_ids = [r.case_id for r in dept_rows]
    if not case_ids:
        raise HTTPException(status_code=404, detail="No cases found for this department.")

    case_map = {
        c.case_id: c
        for c in db.query(Case).filter(
            Case.case_id.in_(case_ids), Case.is_parent.is_(False),
        ).all()
    }
    dept_row_map = {r.case_id: r for r in dept_rows}

    def _dept_reorder_sort_key(c: Case) -> tuple:
        dept_pri = (dept_row_map.get(c.case_id) or CaseDepartment()).priority or "LOW"
        if c.queue_pinned and c.queue_position is not None:
            return (0, c.queue_position, 0, 0, "")
        alerts = sla_alerts_for_case(c)
        sla = (0 if any(a["severity"] == "OVERDUE" for a in alerts)
               else 1 if any(a["severity"] == "NEARING_BREACH" for a in alerts)
               else 2)
        pri = 3 - PRIORITY_RANK.get(dept_pri, 0)
        return (1, sla, pri, 0, str(c.created_at or ""))

    active = sorted(
        [c for c in case_map.values() if c.workflow_state not in {"RESOLVED", "CLOSED", "HUMAN_TRIAGE"}],
        key=_dept_reorder_sort_key,
    )

    target_idx = next((i for i, c in enumerate(active) if c.case_id == body.case_id), None)
    if target_idx is None:
        raise HTTPException(status_code=404, detail="Case not found in active queue.")

    new_idx = max(0, min(body.new_index, len(active) - 1))
    item = active.pop(target_idx)
    active.insert(new_idx, item)

    # Write sequential queue_positions and pin every case in the reordered list
    for pos, case in enumerate(active, start=1):
        case.queue_position = pos
        case.queue_pinned = True

    append_event(
        db, item,
        actor=user["username"], role=user["role"],
        action="queue_move",
        message=f"Queue position drag-reordered to index {new_idx + 1} in {label}",
        metadata={"department": label, "new_index": new_idx, "queue_length": len(active)},
        previous_state={"queue_index": target_idx + 1},
        new_state={"queue_position": item.queue_position},
    )
    db.commit()
    db.refresh(item)
    await manager.broadcast("case_updated", case_summary(item))
    return {"case_id": body.case_id, "new_position": item.queue_position, "queue_length": len(active)}


@app.get("/api/v1/departments/{department}/stats")
def department_stats(department: str, db: Session = Depends(get_db), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """Extended per-department stats: classification breakdown, SLA health, operator workload."""
    label = department_label_from_slug(db, department)
    rows = db.query(CaseDepartment).filter(CaseDepartment.department == label).all()
    case_ids = [r.case_id for r in rows]
    cases = db.query(Case).filter(Case.case_id.in_(case_ids)).all() if case_ids else []

    classification_counts: dict[str, int] = {}
    # Build a lookup: case_id → case object for workload computation
    case_map = {c.case_id: c for c in cases}
    # operator_workloads: {operator: {cases, open, escalated}}
    operator_workloads: dict[str, dict[str, int]] = {}
    for r in rows:
        if not r.assigned_operator:
            continue
        op = r.assigned_operator
        if op not in operator_workloads:
            operator_workloads[op] = {"cases": 0, "open": 0, "escalated": 0}
        operator_workloads[op]["cases"] += 1
        c = case_map.get(r.case_id)
        if c:
            if c.workflow_state not in {"RESOLVED", "CLOSED"}:
                operator_workloads[op]["open"] += 1
            if c.workflow_state == "ESCALATED":
                operator_workloads[op]["escalated"] += 1
    for c in cases:
        classification_counts[c.classification] = classification_counts.get(c.classification, 0) + 1

    active_cases = [c for c in cases if c.workflow_state not in {"RESOLVED", "CLOSED"}]
    sla_data = [alert for c in active_cases for alert in sla_alerts_for_case(c)]
    overdue = sum(1 for a in sla_data if a["severity"] == "OVERDUE")
    nearing = sum(1 for a in sla_data if a["severity"] == "NEARING_BREACH")

    return {
        "department": label,
        "slug": slug(label),
        "total_cases": len(cases),
        "classification_breakdown": classification_counts,
        "operator_workloads": operator_workloads,
        "sla_health": {
            "overdue": overdue,
            "nearing_breach": nearing,
            "healthy": len(active_cases) - overdue - nearing,
        },
        "workflow_states": {
            state: sum(1 for c in cases if c.workflow_state == state)
            for state in ["NEW", "ASSIGNED", "ACKNOWLEDGED", "UNDER_REVIEW", "WAITING_FOR_ACTION",
                          "ESCALATED", "MULTI_DEPARTMENT_REVIEW", "RESOLVED", "CLOSED"]
        },
    }


@app.post("/api/v1/cases/{case_id}/workflow")
async def workflow_action(
    case_id: str,
    payload: WorkflowActionRequest,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    case = db.query(Case).filter(Case.case_id == case_id).one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    payload.actor = payload.actor or user["username"]
    payload.role = payload.role or user["role"]
    try:
        updated = apply_workflow_action(db, case, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(updated)
    logger.info("Workflow action completed case_id=%s action=%s actor=%s", case_id, payload.action, payload.actor)
    await manager.broadcast("case_updated", case_summary(updated))
    return case_to_dict(updated, db)


@app.post("/api/v1/cases/{case_id}/queue-move")
async def queue_move(
    case_id: str,
    body: QueueMoveRequest,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """
    Promote or demote a case in the central intake queue.

    We fetch all active (non-closed) top-level cases in the current queue order,
    normalise their queue_position to sequential integers, then swap the target
    case with its neighbour in the requested direction.
    """
    case = db.query(Case).filter(Case.case_id == case_id).one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    # Build the full ordered active queue (same logic as dashboard)
    all_top_level = (
        db.query(Case)
        .filter(or_(Case.parent_case_id == None, Case.parent_case_id == ""))  # noqa: E711
        .all()
    )
    active = sorted(
        [c for c in all_top_level if c.workflow_state not in {"RESOLVED", "CLOSED"}],
        key=_queue_sort_key,
    )

    try:
        target, previous_position, previous_order, new_order = _apply_queue_move(active, case_id, body.direction)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=409, detail="Case is not in the active queue.") from exc
        raise

    append_event(
        db, target,
        actor=user["username"], role=user["role"],
        action="queue_move",
        message=f"Queue position moved {body.direction} by {user['username']}",
        metadata={
            "direction": body.direction,
            "previous_order": previous_order,
            "new_order": new_order,
            "new_position": target.queue_position,
            "queue_length": len(active),
        },
        previous_state={"queue_position": previous_position, "queue_order": previous_order},
        new_state={"queue_position": target.queue_position, "queue_order": new_order},
    )

    db.commit()
    db.refresh(target)
    await manager.broadcast("case_updated", case_summary(target))
    return case_summary(target)


@app.post("/api/v1/cases/{case_id}/unpin")
async def unpin_case(
    case_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Release an operator-pinned case back to auto SLA/priority ranking."""
    case = db.query(Case).filter(Case.case_id == case_id).one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    case.queue_pinned = False
    case.queue_position = None
    db.commit()
    db.refresh(case)
    await manager.broadcast("case_updated", case_summary(case))
    return case_summary(case)


@app.get("/api/v1/sla/alerts")
def sla_alerts(db: Session = Depends(get_db), user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    return all_sla_alerts(db)


@app.get("/api/v1/incidents")
def incidents(db: Session = Depends(get_db), user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    rows = db.query(Incident).order_by(Incident.updated_at.desc()).all()
    return [
        {
            "incident_id": row.incident_id,
            "title": row.title,
            "status": row.status,
            "pattern_key": row.pattern_key,
            "master_case_id": row.master_case_id,
            "case_ids": row.case_ids or [],
            "metadata": row.incident_metadata or {},
            "created_at": dt(row.created_at),
            "updated_at": dt(row.updated_at),
        }
        for row in rows
    ]


@app.get("/api/v1/historical/search")
def historical_search(q: str, db: Session = Depends(get_db), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"query": q, "matches": similarity_search(db, q)}


@app.post("/api/v1/cases/{case_id}/continuation")
async def create_continuation(
    case_id: str,
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """
    Reopen a resolved/closed case as a brand-new standalone case.
    The new case carries all context from the original (timeline, communications,
    solutions, customer info, email body) as reference — nothing is shared/linked
    at the DB level. Both cases get timeline entries recording the connection.
    """
    from .services.audit import append_event as _ae
    from .services.orchestration import make_readable_ticket_id
    from .services.workflow import ensure_department

    original = db.query(Case).filter(Case.case_id == case_id).one_or_none()
    if not original:
        raise HTTPException(status_code=404, detail="Case not found")
    if original.workflow_state not in {"RESOLVED", "CLOSED"}:
        raise HTTPException(status_code=409, detail="Can only reopen a resolved or closed case.")

    note = (body.get("note") or "").strip() or f"Reopened from {case_id}"
    new_id = make_readable_ticket_id(original.primary_department or "CENT")

    # Pull communications out of the original department records
    orig_dept_records = (
        db.query(CaseDepartment).filter(CaseDepartment.case_id == case_id).all()
    )
    original_communications = []
    for dr in orig_dept_records:
        for n in (dr.internal_notes or []):
            if n.get("communication_body") or n.get("customer_visible"):
                original_communications.append(n)

    # Build the reference block embedded in ai_analysis
    reference_block = {
        "reopened_from": case_id,
        "reopen_note": note,
        "reopen_actor": user["username"],
        "original_classification": original.classification,
        "original_department": original.primary_department,
        "original_resolution": original.resolution_text or "",
        "original_workflow_state": original.workflow_state,
        "original_timeline": original.unified_timeline or [],
        "original_communications": original_communications,
        "original_suggested_solutions": (original.ai_analysis or {}).get("suggested_solutions", []),
        "original_sla_metadata": original.sla_metadata or {},
    }

    new_case = Case(
        case_id=new_id,
        # standalone — no parent_case_id
        classification=original.classification,
        priority=original.priority or "MEDIUM",
        workflow_state="ASSIGNED",
        primary_department=original.primary_department,
        risk_score=original.risk_score,
        confidence_score=original.confidence_score,
        needs_human_triage=False,
        customer_customer_ref=original.customer_customer_ref,
        sla_metadata=build_sla_metadata(original.priority or "MEDIUM"),
        ai_analysis={
            **(original.ai_analysis or {}),
            **reference_block,
        },
        extracted_fields=original.extracted_fields,
        customer_metadata=original.customer_metadata,
        email_metadata=original.email_metadata,
        normalized_text=original.normalized_text,
        raw_email=original.raw_email,
        raw_email_hash=original.raw_email_hash,
        language=original.language or "English",
        intake_flags=original.intake_flags,
        source_type="reopened",
        audit_history=[],
        unified_timeline=[],
    )
    db.add(new_case)

    dept_record = ensure_department(
        db, new_case,
        original.primary_department or "Central Operations Desk",
        role="primary", confidence=1.0,
    )
    dept_record.status = "ASSIGNED"

    # Timeline on the new case: first entry explains the origin
    _ae(db, new_case,
        actor=user["username"], role=user["role"],
        action="reopened_from",
        message=f"Reopened from {case_id} — {note}",
        metadata={
            "original_case_id": case_id,
            "note": note,
            "original_classification": original.classification,
            "original_resolution": original.resolution_text or "",
        },
        department=original.primary_department)

    # Timeline on the original case: records that it was reopened
    _ae(db, original,
        actor=user["username"], role=user["role"],
        action="reopened_as",
        message=f"Reopened as new case {new_id} by {user['username']} — {note}",
        metadata={"new_case_id": new_id, "note": note},
        department=original.primary_department)

    db.commit()
    db.refresh(new_case)
    await manager.broadcast("case_created", case_summary(new_case))
    return case_to_dict(new_case, db)


@app.get("/api/v1/audit")
def audit_events(case_id: str | None = None, db: Session = Depends(get_db), user: dict[str, Any] = Depends(require_roles("auditor", "admin"))) -> list[dict[str, Any]]:
    query = db.query(AuditEvent).order_by(AuditEvent.timestamp.desc())
    if case_id:
        query = query.filter(AuditEvent.case_id == case_id)
    return [
        {
            "case_id": row.case_id,
            "actor": row.actor,
            "role": row.role,
            "action": row.action,
            "metadata": row.event_metadata,
            "previous_state": row.previous_state,
            "new_state": row.new_state,
            "timestamp": dt(row.timestamp),
        }
        for row in query.limit(500).all()
    ]


# ── Request Portal ────────────────────────────────────────────────────────────

_PORTAL_TYPE_MAP: dict[str, dict[str, Any]] = {
    "fraud": {
        "classification": "UNAUTHORISED_USE",
        "suggested_department": "Trust & Safety Desk",
        "label": "Fraud / Unauthorised Transaction",
    },
    "technical": {
        "classification": "CONNECTION_FAULT",
        "suggested_department": "Network Support Desk",
        "label": "Technical / Digital Service Issue",
    },
    "account_access": {
        "classification": "PORTAL_ACCESS",
        "suggested_department": "Network Support Desk",
        "label": "Account Access Problem",
    },
    "compliance": {
        "classification": "REGULATORY",
        "suggested_department": "Compliance Desk",
        "label": "Compliance / Regulatory Matter",
    },
    "grievance": {
        "classification": "CUSTOMER_GRIEVANCE",
        "suggested_department": "Customer Resolution Desk",
        "label": "Service Complaint / Grievance",
    },
    "escalation": {
        "classification": "ESCALATION",
        "suggested_department": "Central Operations Desk",
        "label": "Escalation of Existing Issue",
    },
    "general": {
        "classification": "GENERAL_QUERY",
        "suggested_department": "Customer Resolution Desk",
        "label": "General Enquiry",
    },
}

_URGENCY_PRIORITY: dict[str, str] = {
    "critical": "CRITICAL",
    "high": "HIGH",
    "medium": "MEDIUM",
    "low": "LOW",
}

_URGENCY_RISK: dict[str, float] = {
    "critical": 0.95,
    "high": 0.72,
    "medium": 0.45,
    "low": 0.20,
}


@app.post("/api/v1/portal/submit", response_model=PortalSubmitResponse)
async def portal_submit(
    payload: PortalSubmitRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Public request portal — creates a case directly in the
    Central Operations Desk queue without running the AI pipeline.
    Central Ops reviews the submission and dispatches it to the appropriate desk
    using the existing inter_dept_transfer / multi_dept_review workflow actions.
    """
    import hashlib
    from .services.historical import index_case as _index_case
    from .services.sla import build_sla_metadata as _build_sla
    from .services.orchestration import make_readable_ticket_id

    type_info = _PORTAL_TYPE_MAP[payload.request_type]
    classification = type_info["classification"]
    suggested_department = type_info["suggested_department"]
    type_label = type_info["label"]

    priority = _URGENCY_PRIORITY[payload.urgency]
    risk_score = _URGENCY_RISK[payload.urgency]

    now = datetime.now(timezone.utc)
    case_id = make_readable_ticket_id("Central Operations Desk")

    # Normalised text for historical search / duplicate detection
    normalized_text = (
        f"{type_label} submitted via portal. "
        f"Requester: {payload.requester_name}. "
        f"Description: {payload.description}"
    )
    if payload.reference_number:
        normalized_text += f" Reference: {payload.reference_number}."

    raw_content = (
        f"Portal Request — {type_label}\n"
        f"Name: {payload.requester_name}\n"
        f"Email: {payload.requester_email}\n"
        f"Urgency: {payload.urgency.upper()}\n"
        f"Reference: {payload.reference_number or '—'}\n"
        f"Account: {payload.connection_id or '—'}\n\n"
        f"{payload.description}"
    )
    raw_hash = hashlib.sha256(raw_content.encode()).hexdigest()

    case = Case(
        case_id=case_id,
        source_type="portal",
        classification=classification,
        workflow_state="ASSIGNED",
        primary_department="Central Operations Desk",
        secondary_departments=[],
        customer_metadata={
            "requester_name": payload.requester_name,
            "requester_email": payload.requester_email,
            "connection_id": payload.connection_id,
            "portal_submission": True,
        },
        email_metadata={
            "source": "request_portal",
            "request_type": payload.request_type,
            "type_label": type_label,
            "submitted_at": now.isoformat(),
        },
        extracted_fields={
            "customer_name": payload.requester_name,
            "customer_email": payload.requester_email,
            "request_type": payload.request_type,
            "urgency": payload.urgency,
            "description": payload.description,
            "reference_number": payload.reference_number,
            "connection_id": payload.connection_id,
            "service_summary": f"[Portal] {type_label}: {payload.description[:200]}",
            "suggested_department": suggested_department,
        },
        ai_analysis={
            "model": "portal_manual",
            "classification": classification,
            "confidence_score": 1.0,
            "adjudication_mode": "manual_portal",
            "routing_explanation": (
                f"Manually submitted via request portal as '{type_label}'. "
                f"Suggested routing: {suggested_department}."
            ),
            "suggested_department": suggested_department,
            "portal_submission": True,
        },
        confidence_score=1.0,
        risk_score=risk_score,
        priority=priority,
        sla_metadata=_build_sla(priority, start=now),
        escalation_state={
            "is_escalated": priority == "CRITICAL",
            "reason": ["portal_critical_urgency"] if priority == "CRITICAL" else [],
            "governor": "central_operations",
        },
        linked_cases=[],
        attachments=[],
        raw_email_hash=raw_hash,
        raw_email_path=None,
        raw_email=raw_content,
        normalized_text=normalized_text,
        is_parent=False,
        parent_case_id=None,
        child_case_ids=[],
        audit_history=[],
        unified_timeline=[],
        created_at=now,
        updated_at=now,
    )
    db.add(case)
    db.flush()

    db.add(CaseDepartment(
        case_id=case_id,
        department="Central Operations Desk",
        department_role="primary",
        routing_confidence=1.0,
        status="ASSIGNED",
        priority=priority,
    ))

    append_event(
        db, case,
        actor=payload.requester_email,
        role="portal_requester",
        action="portal_request_submitted",
        message=(
            f"Request submitted via portal: {type_label}. "
            f"Urgency: {payload.urgency.upper()}. "
            f"Suggested routing: {suggested_department}."
        ),
        metadata={
            "request_type": payload.request_type,
            "type_label": type_label,
            "urgency": payload.urgency,
            "suggested_department": suggested_department,
            "reference_number": payload.reference_number,
        },
        new_state={"workflow_state": "ASSIGNED", "primary_department": "Central Operations Desk"},
    )

    db.commit()
    db.refresh(case)
    _index_case(db, case)
    db.commit()

    logger.info(
        "Portal request submitted case_id=%s type=%s urgency=%s priority=%s",
        case_id, payload.request_type, payload.urgency, priority,
    )
    await manager.broadcast("case_created", case_summary(case))

    return PortalSubmitResponse(
        case_id=case_id,
        priority=priority,
        suggested_department=suggested_department,
        message=(
            f"Your request has been received and assigned to Central Operations (Case ID: {case_id}). "
            f"Expected routing to {suggested_department}. "
            f"Please quote your case ID for follow-up."
        ),
    )


# ── Human Triage Queue ────────────────────────────────────────────────────────

@app.get("/api/v1/triage-queue")
def triage_queue(
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> list[dict[str, Any]]:
    """All cases waiting for a human to classify them (needs_human_triage=True, state=HUMAN_TRIAGE)."""
    rows = (
        db.query(Case)
        .filter(Case.needs_human_triage.is_(True), Case.workflow_state == "HUMAN_TRIAGE")
        .order_by(Case.created_at.asc())
        .limit(200)
        .all()
    )
    return [
        {
            **case_summary(c),
            "abstain_reason": (c.ai_analysis or {}).get("abstain_reason"),
            "confidence_score": c.confidence_score,
            "triage_labels": c.triage_labels or [],
            "language": c.language or "English",
        }
        for c in rows
    ]


@app.post("/api/v1/triage-queue/{case_id}/release")
async def release_triage_case(
    case_id: str,
    body: TriageReleaseRequest,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """
    A human reviewer classifies and releases a triage-queue case to the correct department.
    Updates classification, primary_department, needs_human_triage=False, state=ASSIGNED.
    """
    from .services.workflow import ensure_department
    from .services.audit import append_event as _ae

    case = db.query(Case).filter(Case.case_id == case_id).one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if not case.needs_human_triage:
        raise HTTPException(status_code=409, detail="Case is not in the human triage queue.")

    now = datetime.now(timezone.utc).isoformat()

    if body.classification:
        case.classification = body.classification
    case.primary_department = body.department
    case.needs_human_triage = False

    # Collect all departments: primary first, then any additional secondaries
    all_depts = [body.department] + [d for d in (body.departments or []) if d and d != body.department]

    if len(all_depts) > 1:
        # Multi-department release — create a parent/child split just like the AI does
        case.is_parent = True
        case.workflow_state = "MULTI_DEPARTMENT_REVIEW"
        from sqlalchemy.orm.attributes import flag_modified
        secondaries = []
        for extra in all_depts[1:]:
            child = Case(
                case_id=f"{case.case_id}-{extra[:4].upper()}",
                parent_case_id=case.case_id,
                classification=case.classification,
                priority=case.priority,
                workflow_state="ASSIGNED",
                primary_department=extra,
                risk_score=case.risk_score,
                confidence_score=case.confidence_score,
                needs_human_triage=False,
                sla_metadata=case.sla_metadata,
                ai_analysis=case.ai_analysis,
                extracted_fields=case.extracted_fields,
                language=case.language,
                created_at=case.created_at,
                raw_email_hash=f"{case.raw_email_hash}-{extra[:4].upper()}",
                raw_email=case.raw_email,
                normalized_text=case.normalized_text,
            )
            db.add(child)
            child_dept = ensure_department(db, child, extra, role="primary", confidence=1.0)
            child_dept.status = "ASSIGNED"
            secondaries.append({"department": extra, "confidence": 1.0, "released_by": "triage"})
        case.secondary_departments = secondaries
        flag_modified(case, "secondary_departments")
    else:
        case.workflow_state = "ASSIGNED"

    primary_record = ensure_department(db, case, body.department, role="primary", confidence=1.0)
    primary_record.status = "ASSIGNED"

    _ae(
        db, case,
        actor=body.actor or user["username"],
        role=user["role"],
        action="release_triage",
        message=(
            f"Human reviewer released case from triage queue → {', '.join(all_depts)}."
            + (f" Classification set to {body.classification}." if body.classification else "")
            + (f" Note: {body.note}" if body.note else "")
        ),
        metadata={
            "department": body.department,
            "departments": all_depts,
            "classification": body.classification,
            "note": body.note,
        },
        previous_state={"workflow_state": "HUMAN_TRIAGE", "needs_human_triage": True},
        new_state={"workflow_state": case.workflow_state, "primary_department": body.department, "all_departments": all_depts},
    )

    db.commit()
    db.refresh(case)
    await manager.broadcast("case_updated", case_summary(case))
    return case_to_dict(case, db)


# ── Customer Validation ───────────────────────────────────────────────────────

@app.get("/api/v1/cases/{case_id}/customer-lookup")
def customer_lookup(
    case_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Validate identifiers extracted from this case's email against the mock customer DB."""
    from .services.customer_validation import run_customer_validation
    case = db.query(Case).filter(Case.case_id == case_id).one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    text = case.raw_email or case.normalized_text or ""
    sender_email: str | None = (case.email_metadata or {}).get("sender")
    sender_name: str | None = (
        (case.customer_metadata or {}).get("case_form_customer", {}) or {}
    ).get("customer_name")
    result = run_customer_validation(text, db, sender_email=sender_email, sender_name=sender_name)
    # Persist matched customer reference onto the case so customer-history uses it going forward
    matched_customer_ref = (result.get("matched_customer") or {}).get("customer_ref")
    if matched_customer_ref and case.customer_customer_ref != matched_customer_ref:
        case.customer_customer_ref = matched_customer_ref
        db.commit()
    return result


@app.get("/api/v1/cases/{case_id}/customer-history")
def customer_history_for_case(
    case_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """
    Return all previous cases linked to the same customer.
    Primary match: customer_customer_ref (set at intake when customer is identified).
    Fallback: sender email text-search on email_metadata JSON.
    """
    case = db.query(Case).filter(Case.case_id == case_id).one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    matched_by: str = "none"

    if case.customer_customer_ref:
        # Fast, accurate customer reference-based lookup
        past_cases = (
            db.query(Case)
            .filter(Case.case_id != case_id, Case.customer_customer_ref == case.customer_customer_ref)
            .order_by(Case.created_at.desc())
            .limit(30)
            .all()
        )
        matched_by = "customer_ref"
    else:
        # Fall back to sender-email text search on the JSON blob
        sender_email: str | None = (case.email_metadata or {}).get("sender")
        if not sender_email:
            sender_email = (
                (case.customer_metadata or {}).get("case_form_customer", {}) or {}
            ).get("customer_email")

        if not sender_email:
            return {"cases": [], "total": 0, "customer_customer_ref": None, "matched_by": "none"}

        past_cases = (
            db.query(Case)
            .filter(
                Case.case_id != case_id,
                cast(Case.email_metadata, String).like(f'%"{sender_email}"%'),
            )
            .order_by(Case.created_at.desc())
            .limit(30)
            .all()
        )
        matched_by = "email"

    rows = [
        {**case_summary(c), "resolution_text": c.resolution_text, "source_type": c.source_type}
        for c in past_cases
    ]
    return {
        "cases": rows,
        "total": len(rows),
        "customer_customer_ref": case.customer_customer_ref,
        "matched_by": matched_by,
    }


@app.post("/api/v1/cases/{case_id}/summarize-thread")
async def summarize_thread(
    case_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Detect email thread depth and generate a Gemma4 summary. Cached on the case."""
    from .services.thread_summary import detect_thread, summarize_thread as do_summarize
    from .services.audit import append_event as _ae
    case = db.query(Case).filter(Case.case_id == case_id).one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    text = case.raw_email or case.normalized_text or ""
    thread_info = detect_thread(text)

    ai = dict(case.ai_analysis or {})
    # Return cached summary if already generated
    if ai.get("thread_summary") and ai.get("thread_depth"):
        return {
            "is_thread": thread_info["is_thread"],
            "depth": ai["thread_depth"],
            "summary": ai["thread_summary"],
            "cached": True,
        }

    if not thread_info["is_thread"]:
        return {"is_thread": False, "depth": 1, "summary": None, "cached": False}

    summary = await run_in_threadpool(do_summarize, text)
    ai["thread_summary"] = summary
    ai["thread_depth"] = thread_info["depth"]
    case.ai_analysis = ai
    _ae(db, case, actor=user["username"], role=user["role"], action="thread_summarized",
        message=f"Thread summary generated (depth {thread_info['depth']}).",
        metadata={"depth": thread_info["depth"]})
    db.commit()
    return {"is_thread": True, "depth": thread_info["depth"], "summary": summary, "cached": False}


@app.patch("/api/v1/triage-queue/{case_id}/complaint")
async def edit_triage_complaint(
    case_id: str,
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Operator edits the complaint text for a triage-queue case before releasing."""
    from .services.audit import append_event as _ae
    case = db.query(Case).filter(Case.case_id == case_id).one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if not case.needs_human_triage:
        raise HTTPException(status_code=409, detail="Case is not in the triage queue.")
    edited_text = (body.get("complaint_text") or "").strip()
    if not edited_text:
        raise HTTPException(status_code=422, detail="complaint_text is required.")
    ai = dict(case.ai_analysis or {})
    ai["operator_edited_complaint"] = edited_text
    ai["complaint_edited_by"] = user["username"]
    ai["original_normalized_text"] = ai.get("original_normalized_text") or case.normalized_text or ""
    case.ai_analysis = ai
    case.normalized_text = edited_text  # propagates to departmental desks
    _ae(db, case, actor=user["username"], role=user["role"], action="complaint_edited",
        message="Operator edited complaint text in triage queue.",
        metadata={"length": len(edited_text)},
        previous_state={}, new_state={"edited": True})
    db.commit()
    db.refresh(case)
    await manager.broadcast("case_updated", case_summary(case))
    return {"status": "ok", "case_id": case_id, "complaint_text": edited_text}


# ── Error Queue (Dead-Letter) ─────────────────────────────────────────────────

@app.get("/api/v1/errors")
def list_intake_errors(
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(require_roles("intake_operator", "escalation_manager")),
) -> list[dict[str, Any]]:
    rows = (
        db.query(IntakeError)
        .filter(IntakeError.resolved.is_(False))
        .order_by(IntakeError.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "error_id": r.error_id,
            "source_filename": r.source_filename,
            "raw_email_hash": r.raw_email_hash,
            "raw_email_preview": (r.raw_email_preview or "")[:200],
            "error_type": r.error_type,
            "error_detail": r.error_detail,
            "actor": r.actor,
            "retryable": r.retryable,
            "retry_count": r.retry_count,
            "created_at": dt(r.created_at),
        }
        for r in rows
    ]


@app.post("/api/v1/errors/{error_id}/dismiss")
async def dismiss_intake_error(
    error_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(require_roles("intake_operator", "escalation_manager")),
) -> dict[str, str]:
    row = db.query(IntakeError).filter(IntakeError.error_id == error_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Error record not found")
    row.resolved = True
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    await manager.broadcast("error_dismissed", {"error_id": error_id})
    return {"status": "dismissed", "error_id": error_id}


# ── Analytics endpoints ───────────────────────────────────────────────────────

@app.get("/api/v1/analytics/volume-trend")
def analytics_volume_trend(
    days: int = 14,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> list[dict[str, Any]]:
    from .services.analytics import volume_trend
    return volume_trend(db, days=min(days, 90))


@app.get("/api/v1/analytics/classification-heatmap")
def analytics_classification_heatmap(
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    from .services.analytics import classification_heatmap
    return classification_heatmap(db)


@app.get("/api/v1/analytics/resolution-times")
def analytics_resolution_times(
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    from .services.analytics import resolution_time_stats
    return resolution_time_stats(db)


@app.get("/api/v1/analytics/sla-breach-rates")
def analytics_sla_breach_rates(
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    from .services.analytics import sla_breach_analysis
    return sla_breach_analysis(db)


@app.get("/api/v1/analytics/model-performance")
def analytics_model_performance(
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    from .services.analytics import model_performance_metrics
    return model_performance_metrics(db)


@app.get("/api/v1/analytics/fraud-trend")
def analytics_fraud_trend(
    days: int = 14,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> list[dict[str, Any]]:
    from .services.analytics import fraud_risk_trend
    return fraud_risk_trend(db, days=min(days, 90))


@app.get("/api/v1/analytics/department-performance")
def analytics_department_performance(
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> list[dict[str, Any]]:
    from .services.analytics import department_performance
    return department_performance(db)


@app.get("/api/v1/analytics/department-deep-dive")
def analytics_department_deep_dive(
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> list[dict[str, Any]]:
    from .services.analytics import department_deep_dive
    return department_deep_dive(db)


# ── Draft response generation ─────────────────────────────────────────────────

# ── Customer database browser ─────────────────────────────────────────────────

@app.get("/api/v1/customers")
def list_customers(
    segment: str | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = 30,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> list[dict[str, Any]]:
    """Browse the mock customer database with optional filtering."""
    from .models import Subscriber
    query = db.query(Subscriber)
    if segment:
        query = query.filter(Subscriber.customer_segment == segment.upper())
    if status:
        query = query.filter(Subscriber.status == status.upper())
    if q:
        query = query.filter(
            Subscriber.name.ilike(f"%{q}%") |
            Subscriber.customer_ref.ilike(f"%{q}%") |
            Subscriber.mobile_number.ilike(f"%{q}%") |
            Subscriber.email.ilike(f"%{q}%") |
            Subscriber.tax_id.ilike(f"%{q}%") |
            Subscriber.service_area.ilike(f"%{q}%")
        )
    rows = query.limit(min(limit, 100)).all()
    return [_customer_to_dict(c) for c in rows]


@app.get("/api/v1/customers/{customer_ref}")
def get_customer(
    customer_ref: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    from .models import Subscriber
    cust = db.query(Subscriber).filter(Subscriber.customer_ref == customer_ref).one_or_none()
    if not cust:
        raise HTTPException(status_code=404, detail="Customer not found")
    return _customer_to_dict(cust)


def _customer_to_dict(c) -> dict[str, Any]:
    return {
        "customer_ref": c.customer_ref,
        "name": c.name,
        "mobile_number": c.mobile_number,
        "email": getattr(c, "email", None),
        "tax_id": getattr(c, "tax_id", None),
        "id_last4": getattr(c, "id_last4", None),
        "date_of_birth": getattr(c, "date_of_birth", None),
        "service_area": c.service_area,
        "area_code": c.area_code,
        "status": c.status,
        "verification_status": c.verification_status,
        "customer_segment": getattr(c, "customer_segment", "RETAIL"),
        "occupation": getattr(c, "occupation", None),
        "annual_income": getattr(c, "annual_income", None),
        "payment_score": getattr(c, "payment_score", None),
        "address": getattr(c, "address", {}),
        "connection_ids": c.connection_ids or [],
        "device_serials": c.device_serials or [],
        "payment_handles": c.payment_handles or [],
        "connection_details": getattr(c, "connection_details", []) or [],
        "device_details": getattr(c, "device_details", []) or [],
        "contracts": getattr(c, "contracts", []) or [],
        "addon_services": getattr(c, "addon_services", []) or [],
        "static_ip_blocks": getattr(c, "static_ip_blocks", []) or [],
        "premises_equipment": getattr(c, "premises_equipment", []) or [],
        "protection_plans": getattr(c, "protection_plans", []) or [],
        "roaming_profile": getattr(c, "roaming_profile", None),
        "self_care_portal": getattr(c, "self_care_portal", "ACTIVE"),
        "mobile_app_access": getattr(c, "mobile_app_access", "ACTIVE"),
        "account_manager": getattr(c, "account_manager", None),
        "alternate_contact": getattr(c, "alternate_contact", None),
        "product_types": c.product_types or [],
        "self_care_portal_status": getattr(c, "self_care_portal", "ACTIVE"),
        "mobile_app_access_status": getattr(c, "mobile_app_access", "ACTIVE"),
    }


@app.post("/api/v1/cases/{case_id}/draft-response")
def generate_draft_response(
    case_id: str,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    case = db.query(Case).filter(Case.case_id == case_id).one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    from .services.response_templates import generate_draft_response as _gen
    from .services.customer_validation import run_customer_validation
    from .services.regulatory import detect_regulatory_breach

    flags = case.intake_flags or {}
    att_entities = flags.get("attachment_entities") or {}

    # Pull matched customer record (stored at ingestion) or re-validate
    matched_customer = (flags.get("sparse_customer_match") or {}).get("matched_customer")
    if not matched_customer:
        _sender_email = (case.email_metadata or {}).get("sender")
        _sender_name = ((case.customer_metadata or {}).get("case_form_customer") or {}).get("customer_name")
        _cv = run_customer_validation(
            case.raw_email or case.normalized_text or "",
            db,
            sender_email=_sender_email,
            sender_name=_sender_name,
        )
        matched_customer = _cv.get("matched_customer")

    reg_breach = flags.get("regulatory_breach") or detect_regulatory_breach(
        case.normalized_text or "",
        triage_regulatory_flag=bool((case.ai_analysis or {}).get("regulatory_breach")),
    )

    context = {
        "customer_name": (
            (case.extracted_fields or {}).get("customer_name")
            or ((case.customer_metadata or {}).get("case_form_customer") or {}).get("customer_name")
        ),
        "case_id": case.case_id,
        "summary": (case.ai_analysis or {}).get("routing_explanation", ""),
        "amount_involved": (case.extracted_fields or {}).get("amount_involved")
            or (att_entities.get("amounts") or [None])[0],
        "priority": case.priority,
        "department": case.primary_department,
        "sla_metadata": case.sla_metadata or {},
        "matched_customer": matched_customer,
        "reference_ids": att_entities.get("reference_ids", []),
        "incident_dates": att_entities.get("dates", []),
        "attachment_summary": ", ".join(
            f"{a['filename']} ({a.get('char_count', 0)} chars)"
            for a in flags.get("attachments_extracted") or []
            if a.get("char_count", 0) > 0
        ),
        "regulatory_breach": reg_breach,
    }
    return _gen(
        classification=case.classification,
        context=context,
        model=DEFAULT_MODEL,
        use_llm=True,
    )


# ── ML Feedback ───────────────────────────────────────────────────────────────

@app.post("/api/v1/cases/{case_id}/feedback")
def submit_feedback(
    case_id: str,
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    case = db.query(Case).filter(Case.case_id == case_id).one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    corrected = body.get("corrected_classification")
    if not corrected:
        raise HTTPException(status_code=422, detail="corrected_classification is required")
    from .services.feedback import record_feedback
    fb = record_feedback(
        db,
        case_id=case_id,
        original_classification=case.classification,
        corrected_classification=corrected,
        original_confidence=case.confidence_score or 0.0,
        adjudication_mode=(case.ai_analysis or {}).get("adjudication_mode"),
        actor=user.get("username", "operator"),
        note=body.get("note"),
    )
    return {
        "feedback_id": fb.id,
        "case_id": fb.case_id,
        "original_classification": fb.original_classification,
        "corrected_classification": fb.corrected_classification,
        "is_correction": fb.original_classification != fb.corrected_classification,
    }


@app.get("/api/v1/feedback")
def list_feedback(
    case_id: str | None = None,
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(require_roles("escalation_manager")),
) -> list[dict[str, Any]]:
    from .services.feedback import list_feedback as _list
    return _list(db, case_id=case_id)


# ── Admin AI Assistant ────────────────────────────────────────────────────────

@app.post("/api/v1/admin/assistant")
def admin_assistant(
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    message = (body.get("message") or "").strip()
    history = body.get("history") or []
    if not message:
        raise HTTPException(status_code=422, detail="message is required")
    return assistant_answer(db, message, history)


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws/operations")
async def operations_ws(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

"""
Admin AI Assistant — gathers live platform data and answers questions via Ollama.
"""
from __future__ import annotations
import json, re, urllib.request
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models import AuditEvent, Case, CaseDepartment


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ── Data gathering ────────────────────────────────────────────────────────────

def _sla_status(case: Case, now: datetime) -> str:
    """Return OVERDUE / NEARING / OK / NO_SLA for a single active case."""
    sla = case.sla_metadata or {}
    due_str = sla.get("resolution_due_at")
    if not due_str:
        return "NO_SLA"
    try:
        due = datetime.fromisoformat(due_str)
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        mins = (due - now).total_seconds() / 60
        if mins < 0:
            return "OVERDUE"
        if mins < 120:
            return "NEARING"
        return "OK"
    except Exception:
        return "NO_SLA"


def _case_snapshot(c: Case, now: datetime, *, verbose: bool = False) -> dict[str, Any]:
    ef = c.extracted_fields or {}
    cm = c.customer_metadata or {}
    customer = (
        ef.get("customer_name")
        or cm.get("customer_name")
        or cm.get("case_form_customer", {}).get("customer_name")
        or "Unknown"
    )
    snap: dict[str, Any] = {
        "case_id":        c.case_id,
        "classification": c.classification,
        "priority":       c.priority,
        "state":          c.workflow_state,
        "department":     c.primary_department,
        "customer":       customer,
    }
    if c.workflow_state not in ("RESOLVED", "CLOSED"):
        snap["sla_status"] = _sla_status(c, now)
    if verbose:
        if c.resolution_text:
            snap["resolution"] = c.resolution_text[:200]
        if c.normalized_text:
            snap["issue_summary"] = c.normalized_text[:300]
    return snap


def _gather_context(db: Session, message: str = "") -> dict[str, Any]:
    """Pull a rich platform snapshot from the DB, enriched for the given message."""
    now = _utc_now()
    all_cases = db.query(Case).filter(Case.is_parent.is_(False)).all()

    active   = [c for c in all_cases if c.workflow_state not in ("RESOLVED", "CLOSED")]
    resolved = [c for c in all_cases if c.workflow_state in ("RESOLVED", "CLOSED")]

    # ── Platform totals ───────────────────────────────────────────────────────
    priority_counts: dict[str, int] = {}
    state_counts:    dict[str, int] = {}
    cls_counts:      dict[str, int] = {}
    dept_counts:     dict[str, dict] = {}

    for c in all_cases:
        cls_counts[c.classification] = cls_counts.get(c.classification, 0) + 1

    for c in active:
        priority_counts[c.priority] = priority_counts.get(c.priority, 0) + 1
        state_counts[c.workflow_state] = state_counts.get(c.workflow_state, 0) + 1
        d = c.primary_department or "Unknown"
        if d not in dept_counts:
            dept_counts[d] = {"total": 0, "active": 0, "resolved": 0, "critical": 0, "escalated": 0, "overdue_sla": 0}
        dept_counts[d]["total"] += 1
        dept_counts[d]["active"] += 1
        if c.priority == "CRITICAL":
            dept_counts[d]["critical"] += 1
        if c.workflow_state == "ESCALATED":
            dept_counts[d]["escalated"] += 1
        if _sla_status(c, now) == "OVERDUE":
            dept_counts[d]["overdue_sla"] += 1

    for c in resolved:
        d = c.primary_department or "Unknown"
        if d not in dept_counts:
            dept_counts[d] = {"total": 0, "active": 0, "resolved": 0, "critical": 0, "escalated": 0, "overdue_sla": 0}
        dept_counts[d]["total"] += 1
        dept_counts[d]["resolved"] += 1

    # ── SLA health across active cases ────────────────────────────────────────
    overdue = nearing = healthy = 0
    for c in active:
        s = _sla_status(c, now)
        if s == "OVERDUE":
            overdue += 1
        elif s == "NEARING":
            nearing += 1
        else:
            healthy += 1

    # ── Case-specific lookup: pull full detail if a case ID is mentioned ──────
    mentioned_cases: list[dict] = []
    case_id_pattern = re.compile(r'\b(Docket-[A-Z]+-\d{8}-\d{4}-[A-Z0-9]+|SEED-[A-Z]{2}-[A-Z0-9]+)\b', re.IGNORECASE)
    for match in case_id_pattern.findall(message):
        c = db.query(Case).filter(Case.case_id == match.upper()).one_or_none()
        if c:
            snap = _case_snapshot(c, now)
            # Include full issue text and audit trail for mentioned cases
            if c.normalized_text:
                snap["full_issue"] = c.normalized_text[:600]
            events = (
                db.query(AuditEvent)
                .filter(AuditEvent.case_id == c.case_id)
                .order_by(AuditEvent.timestamp.desc())
                .limit(8)
                .all()
            )
            snap["audit_trail"] = [
                {"action": e.action, "actor": e.actor, "ts": str(e.timestamp)[:16], "msg": str(e.event_metadata or "")[:120]}
                for e in events
            ]
            mentioned_cases.append(snap)

    # ── Customer-name lookup ──────────────────────────────────────────────────
    customer_cases: list[dict] = []
    # Naive: check if any word sequence matches a known customer name in active/recent
    msg_lower = message.lower()
    seen_ids: set[str] = {s["case_id"] for s in mentioned_cases}
    for c in all_cases:
        if c.case_id in seen_ids:
            continue
        ef = c.extracted_fields or {}
        cm = c.customer_metadata or {}
        name = (ef.get("customer_name") or cm.get("customer_name") or "").lower()
        if name and name != "unknown" and name in msg_lower:
            customer_cases.append(_case_snapshot(c, now))
            seen_ids.add(c.case_id)

    # ── All active cases (compact — no issue text to save context tokens) ──────
    active_case_list = [_case_snapshot(c, now) for c in sorted(active, key=lambda c: c.priority or "")]

    # ── Recent resolutions (last 8, verbose with resolution text) ────────────
    recent_res = sorted(
        [c for c in resolved if c.resolution_text],
        key=lambda c: c.updated_at or _utc_now(),
        reverse=True,
    )[:8]
    recent_res_list = [_case_snapshot(c, now, verbose=True) for c in recent_res]

    # ── Recent platform activity (last 10 audit events) ───────────────────────
    recent_events = (
        db.query(AuditEvent)
        .order_by(AuditEvent.timestamp.desc())
        .limit(10)
        .all()
    )
    audit_feed = [
        {
            "case_id": e.case_id,
            "action":  e.action,
            "actor":   e.actor,
            "ts":      str(e.timestamp)[:16],
            "msg":     str(e.event_metadata or "")[:100],
        }
        for e in recent_events
    ]

    # ── Human triage queue ────────────────────────────────────────────────────
    triage_queue = [
        {"case_id": c.case_id, "classification": c.classification, "priority": c.priority}
        for c in all_cases
        if c.workflow_state == "HUMAN_TRIAGE"
    ]

    ctx: dict[str, Any] = {
        "snapshot_time":        now.strftime("%d %b %Y, %H:%M UTC"),
        "total_cases":          len(all_cases),
        "active_cases":         len(active),
        "resolved_cases":       len(resolved),
        "resolution_rate_pct":  round(len(resolved) / len(all_cases) * 100, 1) if all_cases else 0,
        "priority_breakdown":   priority_counts,
        "state_breakdown":      state_counts,
        "classification_breakdown": dict(sorted(cls_counts.items(), key=lambda x: -x[1])),
        "department_breakdown": dept_counts,
        "sla": {
            "active_cases_with_sla": overdue + nearing + healthy,
            "overdue":       overdue,
            "nearing_breach": nearing,
            "healthy":       healthy,
        },
        "human_triage_queue":   triage_queue,
        "active_cases_detail":  active_case_list,
        "recent_resolutions":   recent_res_list,
        "recent_activity":      audit_feed,
    }

    if mentioned_cases:
        ctx["SPEcustomer referenceIC_CASES_REQUESTED"] = mentioned_cases
    if customer_cases:
        ctx["CASES_FOR_CUSTOMER"] = customer_cases

    return ctx


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM = """You are ARIA — the Docket Email Operations Platform Intelligence Assistant. You support the Central Operations Head with live data analysis, case intelligence, and operational insights.

**Context you receive:**
- A complete live JSON snapshot of the Docket operations platform (cases, departments, SLA, audit trail)
- The full conversation history
- The operator's question

**How to respond:**
- Always answer directly from the data. Never invent numbers or case details.
- Use **markdown formatting**: bold key figures with **value**, use bullet lists for breakdowns, use `## Section` headers for multi-section reports.
- Lead with the direct answer, then supporting detail.
- For case-specific questions, cite the case ID and the actual issue text.
- For department questions, compare across all desks and highlight outliers.
- For SLA questions, call out overdue and nearing-breach cases by name.
- Proactively surface the most important insight even if not explicitly asked — e.g., if resolution rate is 100%, note it; if all CRITICAL cases are resolved, confirm it.
- For "report" requests, structure with: ## Overview → ## Key Issues → ## Department Status → ## Recommendations
- Suggest 2–3 concise follow-up questions at the end of every response, prefixed exactly with: `**Follow-up suggestions:**`
- Keep responses focused. Use numbers precisely. Avoid filler phrases.
- If asked about something outside the data (for example a regulator's policy), acknowledge the limit and answer from general service knowledge if appropriate.

**Docket Operations context:**
- 8 specialist desks: Fraud & Risk, Customer Service, Compliance, Technical, Central Ops, Contracts & Advances, NRI Service, Cards Management
- SLA tiers: CRITICAL = 4h, HIGH = 24h, MEDIUM = 48h, LOW = 72h
- HUMAN_TRIAGE = cases flagged for human review before routing; ESCALATED = raised to Central Ops
"""


# ── Ollama call ───────────────────────────────────────────────────────────────

def _call_ollama(prompt: str, model: str = "the configured local model", timeout: int = 90) -> str:
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.25, "num_predict": 800, "num_ctx": 8192},
    }).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        return data.get("response", "").strip()
    except Exception as e:
        return f"(Ollama unavailable: {e})"


# ── Public entry point ────────────────────────────────────────────────────────

def answer(
    db: Session,
    message: str,
    history: list[dict[str, str]],
    model: str = "the configured local model",
) -> dict[str, Any]:
    ctx = _gather_context(db, message)

    history_text = ""
    for turn in history[-8:]:
        role = "Operator" if turn.get("role") == "user" else "ARIA"
        history_text += f"{role}: {turn['content']}\n\n"

    prompt = (
        f"{_SYSTEM}\n\n"
        f"=== LIVE PLATFORM DATA (JSON) ===\n{json.dumps(ctx, indent=2)}\n\n"
        f"=== CONVERSATION HISTORY ===\n{history_text}"
        f"Operator: {message}\nARIA:"
    )

    reply = _call_ollama(prompt, model=model, timeout=90)

    return {
        "reply": reply,
        "context_used": {
            "total_cases":     ctx["total_cases"],
            "active_cases":    ctx["active_cases"],
            "snapshot_time":   ctx["snapshot_time"],
            "case_ids_looked_up": [c["case_id"] for c in ctx.get("SPEcustomer referenceIC_CASES_REQUESTED", [])],
        },
    }

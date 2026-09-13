"""
priority.py — department-localised priority resolution.

Design
------
* resolve_department_priority()  — consults DepartmentPriorityRule rows for a
  specific department and returns the first-matching priority.  This is the
  primary computation path; it is called once per CaseDepartment record.

* priority_from_triage()         — legacy AI-derived global score.  Now used
  only as a fallback when no dept rule matches, and for escalation_state().

* aggregate_case_priority()      — reads all per-dept priorities for a case
  and returns the maximum.  This is the only thing allowed to write Case.priority.

* seed_department_priority_rules() — idempotent startup seeder.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# ── Priority ordering ───────────────────────────────────────────────────────

PRIORITY_RANK: dict[str, int] = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}

# ── Dept label → canonical code ─────────────────────────────────────────────

DEPT_LABEL_TO_CODE: dict[str, str] = {
    "Trust & Safety Desk": "FRAUD_AND_RISK",
    "Customer Resolution Desk": "CUSTOMER_GRIEVANCES",
    "Compliance Desk": "COMPLIANCE",
    "Network Support Desk": "IT_AND_SYSTEMS",
    "Central Operations Desk": "OPERATIONS",
}


def dept_label_to_code(label: str) -> str:
    if label in DEPT_LABEL_TO_CODE:
        return DEPT_LABEL_TO_CODE[label]
    # Best-effort normalisation for future departments
    return "".join(ch.upper() if ch.isalnum() else "_" for ch in label).strip("_")


# ── Legacy helpers (kept for backward compat + escalation_state) ────────────

def priority_from_triage(triage_result: dict[str, Any], classification: str) -> tuple[str, float]:
    """
    AI-derived global priority score — now used only as pipeline fallback and
    to seed escalation_state.  Do NOT call this to write CaseDepartment.priority.
    """
    score = float(triage_result.get("priority_score") or 0)
    if classification == "UNAUTHORISED_USE":
        score = max(score, 70)
    if classification in {"REGULATORY", "LEGAL_NOTICE"}:
        score = max(score, 72)
    priority = "LOW"
    if score >= 85:
        priority = "CRITICAL"
    elif score >= 60:
        priority = "HIGH"
    elif score >= 30:
        priority = "MEDIUM"
    return priority, min(score / 100, 1.0)


def escalation_state(priority: str, triage_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "is_escalated": priority == "CRITICAL" or bool(triage_result.get("override_applied")),
        "reason": triage_result.get("override_reasons") or [],
        "governor": "central_operations",
    }


# ── Aggregation ─────────────────────────────────────────────────────────────

def aggregate_case_priority(priorities: list[str]) -> str:
    """
    Return the highest priority from a list of per-department priorities.
    This is the only function that should write Case.priority.
    """
    if not priorities:
        return "LOW"
    return max(priorities, key=lambda p: PRIORITY_RANK.get(p, 0))


# ── Rule matching ────────────────────────────────────────────────────────────

def _rule_matches(rule: Any, signals: dict[str, Any]) -> bool:
    """
    Return True only if ALL non-null rule conditions are satisfied (AND logic).
    A rule with no conditions set is a catch-all and always matches.
    """
    if rule.signal_category and signals.get("category") != rule.signal_category:
        return False
    if rule.sender_type and signals.get("sender_type") != rule.sender_type:
        return False
    if rule.regulatory_flag is not None:
        if bool(signals.get("regulatory_flag")) != bool(rule.regulatory_flag):
            return False
    if rule.repeat_flag is not None:
        if bool(signals.get("repeat_flag")) != bool(rule.repeat_flag):
            return False
    if rule.sentiment and signals.get("sentiment") != rule.sentiment:
        return False
    if rule.monetary_threshold is not None:
        amount = signals.get("monetary_amount_detected")
        if amount is None or float(amount) < float(rule.monetary_threshold):
            return False
    if rule.signal_keywords:
        text_blob = " ".join(str(k) for k in (signals.get("keywords") or [])).lower()
        if not any(kw.lower() in text_blob for kw in rule.signal_keywords):
            return False
    return True


# ── Public resolution function ───────────────────────────────────────────────

def resolve_department_priority(
    db: "Session",
    department_label: str,
    email_signals: dict[str, Any],
    fallback_priority: str = "LOW",
) -> tuple[str, dict[str, Any]]:
    """
    Evaluate this department's active rules against email_signals.

    Rules run in rule_order (ascending); first match wins.

    Returns
    -------
    (priority_string, audit_detail_dict)
        audit_detail_dict contains everything needed for the audit log:
        department_code, rule matched, signals evaluated, priority result.
    """
    from ..models import DepartmentPriorityRule

    dept_code = dept_label_to_code(department_label)
    rules = (
        db.query(DepartmentPriorityRule)
        .filter(
            DepartmentPriorityRule.department_code == dept_code,
            DepartmentPriorityRule.is_active.is_(True),
        )
        .order_by(DepartmentPriorityRule.rule_order)
        .all()
    )

    # Compact signal snapshot for audit (drop verbose keyword list)
    signal_snapshot = {k: v for k, v in email_signals.items() if k != "keywords"}
    keyword_sample = (email_signals.get("keywords") or [])[:10]

    for rule in rules:
        if _rule_matches(rule, email_signals):
            return rule.priority_result, {
                "department_code": dept_code,
                "department_label": department_label,
                "rule_id": rule.id,
                "rule_name": rule.rule_name,
                "signals_evaluated": signal_snapshot,
                "keywords_sample": keyword_sample,
                "rule_matched": True,
                "priority_result": rule.priority_result,
            }

    # No rule matched — fall back to global AI priority
    return fallback_priority, {
        "department_code": dept_code,
        "department_label": department_label,
        "rule_id": None,
        "rule_name": "fallback_global_ai",
        "signals_evaluated": signal_snapshot,
        "keywords_sample": keyword_sample,
        "rule_matched": False,
        "priority_result": fallback_priority,
    }


# ── Default seed data ────────────────────────────────────────────────────────

_SEED_RULES: list[dict[str, Any]] = [
    # ── FRAUD_AND_RISK ───────────────────────────────────────────────────
    {"dept": "FRAUD_AND_RISK", "name": "fraud_category_critical",   "order": 10,  "signal_category": "UNAUTHORISED_USE",            "result": "CRITICAL"},
    {"dept": "FRAUD_AND_RISK", "name": "fraud_keywords_critical",   "order": 20,  "keywords": ["unauthorized", "fraud", "hacked", "stolen", "phishing", "otp"], "result": "CRITICAL"},
    {"dept": "FRAUD_AND_RISK", "name": "high_amount_critical",      "order": 30,  "monetary_threshold": 100000.0,        "result": "CRITICAL"},
    {"dept": "FRAUD_AND_RISK", "name": "regulatory_high",           "order": 40,  "regulatory_flag": True,               "result": "HIGH"},
    {"dept": "FRAUD_AND_RISK", "name": "repeat_high",               "order": 50,  "repeat_flag": True,                   "result": "HIGH"},
    {"dept": "FRAUD_AND_RISK", "name": "default_high",              "order": 999,                                        "result": "HIGH"},

    # ── COMPLIANCE ───────────────────────────────────────────────────────
    {"dept": "COMPLIANCE", "name": "regulatory_category_critical",  "order": 10,  "signal_category": "REGULATORY",       "result": "CRITICAL"},
    {"dept": "COMPLIANCE", "name": "legal_notice_critical",         "order": 20,  "signal_category": "LEGAL_NOTICE",     "result": "CRITICAL"},
    {"dept": "COMPLIANCE", "name": "regulatory_flag_critical",      "order": 30,  "regulatory_flag": True,               "result": "CRITICAL"},
    {"dept": "COMPLIANCE", "name": "fraud_high",                    "order": 40,  "signal_category": "UNAUTHORISED_USE",            "result": "HIGH"},
    {"dept": "COMPLIANCE", "name": "regulatory_keywords_high",      "order": 50,  "keywords": ["regulator", "court", "ombudsman", "tribunal"], "result": "HIGH"},
    {"dept": "COMPLIANCE", "name": "default_medium",                "order": 999,                                        "result": "MEDIUM"},

    # ── CUSTOMER_GRIEVANCES ──────────────────────────────────────────────
    {"dept": "CUSTOMER_GRIEVANCES", "name": "escalation_high",      "order": 10,  "signal_category": "ESCALATION",     "result": "HIGH"},
    {"dept": "CUSTOMER_GRIEVANCES", "name": "repeat_high",          "order": 20,  "repeat_flag": True,                 "result": "HIGH"},
    {"dept": "CUSTOMER_GRIEVANCES", "name": "negative_medium",      "order": 30,  "sentiment": "NEGATIVE",             "result": "MEDIUM"},
    {"dept": "CUSTOMER_GRIEVANCES", "name": "fraud_medium",         "order": 40,  "signal_category": "UNAUTHORISED_USE",          "result": "MEDIUM"},
    {"dept": "CUSTOMER_GRIEVANCES", "name": "general_query_low",    "order": 50,  "signal_category": "GENERAL_QUERY",  "result": "LOW"},
    {"dept": "CUSTOMER_GRIEVANCES", "name": "default_low",          "order": 999,                                      "result": "LOW"},

    # ── IT_AND_SYSTEMS ───────────────────────────────────────────────────
    {"dept": "IT_AND_SYSTEMS", "name": "account_access_critical",   "order": 10,  "signal_category": "PORTAL_ACCESS",     "result": "CRITICAL"},
    {"dept": "IT_AND_SYSTEMS", "name": "fraud_high",                "order": 20,  "signal_category": "UNAUTHORISED_USE",              "result": "HIGH"},
    {"dept": "IT_AND_SYSTEMS", "name": "technical_high",            "order": 30,  "signal_category": "CONNECTION_FAULT",    "result": "HIGH"},
    {"dept": "IT_AND_SYSTEMS", "name": "txn_failure_high",          "order": 40,  "signal_category": "PAYMENT_FAILURE","result": "HIGH"},
    {"dept": "IT_AND_SYSTEMS", "name": "default_medium",            "order": 999,                                          "result": "MEDIUM"},

    # ── OPERATIONS ───────────────────────────────────────────────────────
    {"dept": "OPERATIONS", "name": "escalation_critical",           "order": 10,  "signal_category": "ESCALATION",     "result": "CRITICAL"},
    {"dept": "OPERATIONS", "name": "fraud_high",                    "order": 20,  "signal_category": "UNAUTHORISED_USE",          "result": "HIGH"},
    {"dept": "OPERATIONS", "name": "regulatory_high",               "order": 30,  "regulatory_flag": True,             "result": "HIGH"},
    {"dept": "OPERATIONS", "name": "default_medium",                "order": 999,                                      "result": "MEDIUM"},
]


def seed_department_priority_rules(db: "Session") -> int:
    """
    Insert default priority rules if the table is empty.
    Idempotent — safe to call on every startup.
    Returns the number of rows inserted (0 if already seeded).
    """
    from ..models import DepartmentPriorityRule

    if db.query(DepartmentPriorityRule).count() > 0:
        return 0

    count = 0
    for r in _SEED_RULES:
        db.add(DepartmentPriorityRule(
            department_code=r["dept"],
            rule_name=r["name"],
            rule_order=r["order"],
            signal_category=r.get("signal_category"),
            signal_keywords=r.get("keywords"),
            monetary_threshold=r.get("monetary_threshold"),
            regulatory_flag=r.get("regulatory_flag"),
            repeat_flag=r.get("repeat_flag"),
            sender_type=r.get("sender_type"),
            sentiment=r.get("sentiment"),
            priority_result=r["result"],
            is_active=True,
        ))
        count += 1
    db.commit()
    return count

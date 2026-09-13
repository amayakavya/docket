"""
Regulatory breach detector.

Distinguishes between a customer *mentioning* a regulatory body (common, low risk)
versus actively filing or explicitly threatening to file a formal complaint — which
triggers statutory SLA overrides and mandatory escalation paths under the regulator/consumer
protection law.

Detection types (ordered by severity):
  COURT_ORDER          — court injunction / writ / contempt — respond within 24 h
  OMBUDSMAN_COMPLAINT  — sector ombudsman case already filed — 30-day mandate
  LEGAL_NOTICE         — formal legal / advocate notice served — 30 to 60 days
  CONSUMER_FORUM       — consumer court or tribunal — 21-day statutory reply
  REGULATOR_PORTAL     — complaint lodged on the regulator's portal — 30-day mandate
  INFORMATION_REQUEST  — statutory request for records — 30-day statutory reply
  MEDIA_ESCALATION     — press / social media threat — 4 h operational SLA
  OMBUDSMAN_THREAT     — explicit intent to file with the ombudsman — early warning
  REGULATORY_MENTION   — passing mention of a regulator or court with no clear escalation

Which bodies exist, and the windows they impose, differ by sector and country.
They are listed once here and nowhere else, so a deployment adjusts them in one file.
"""
from __future__ import annotations

import re
from typing import Any


# ── Pattern groups ────────────────────────────────────────────────────────────

# Each entry: (pattern, breach_type, severity, sla_override_hours)
# Evaluated in order; first match wins.

_BREACH_PATTERNS: list[tuple[re.Pattern[str], str, str, int]] = [

    # ── Court orders (most urgent) ────────────────────────────────────────────
    (re.compile(
        r"\b(?:court\s+order|high\s+court|supreme\s+court|district\s+court|"
        r"injunction|stay\s+order|writ\s+petition|writ\s+of\s+mandamus|"
        r"contempt\s+of\s+court|execution\s+order|decree|judgment\s+debt)\b",
        re.I,
    ), "COURT_ORDER", "CRITICAL", 24),

    # ── Ombudsman — active filing ─────────────────────────────────────────────
    (re.compile(
        r"\b(?:(?:already|have|has|had)\s+(?:filed|lodged|submitted|registered)"
        r"\s+(?:a\s+)?(?:complaint|grievance)\s+(?:with|at|to)\s+"
        r"(?:the\s+)?(?:sector\s+)?ombudsman|"
        r"ombudsman\s+(?:case|complaint|reference|docket)\s+(?:no\.?|number|#)\s*[\w\d]+|"
        r"ombudsman\s+case\b)",
        re.I,
    ), "OMBUDSMAN_COMPLAINT", "CRITICAL", 720),

    # ── Legal notice — formal service ─────────────────────────────────────────
    (re.compile(
        r"\b(?:legal\s+notice|advocate(?:\'s)?\s+notice|notice\s+under\s+section|"
        r"statutory\s+notice|demand\s+notice|notice\s+under\s+(?:negotiable|contract|"
        r"consumer|service)\s+act|sent\s+you\s+a\s+legal\s+notice|"
        r"issued\s+(?:a\s+)?legal\s+notice|we\s+are\s+(?:issuing|sending)\s+(?:a\s+)?(?:legal|formal)\s+notice)\b",
        re.I,
    ), "LEGAL_NOTICE", "HIGH", 720),

    # ── Consumer forum / tribunal ─────────────────────────────────────────────
    (re.compile(
        r"\b(?:consumer\s+(?:court|forum|commission)|state\s+consumer|"
        r"district\s+consumer\s+(?:redressal|disputes)|consumer\s+protection\s+act|"
        r"filed?\s+(?:in|with|at)\s+consumer\s+court|consumer\s+grievance\s+forum)\b",
        re.I,
    ), "CONSUMER_FORUM", "HIGH", 504),

    # ── Regulator's own complaints portal ─────────────────────────────────────
    (re.compile(
        r"\b(?:regulator(?:'s)?\s+portal|online\s+grievance\s+portal|"
        r"grievance\s+portal|regulator\s+complaint|"
        r"lodged?\s+(?:with|on)\s+(?:the\s+)?regulator)\b",
        re.I,
    ), "REGULATOR_PORTAL", "HIGH", 720),

    # ── Statutory request for records ─────────────────────────────────────────
    (re.compile(
        r"\b(?:right\s+to\s+information|statutory\s+(?:information|records)\s+request|"
        r"information\s+officer|records\s+request\s+under\s+statute|"
        r"formal\s+request\s+for\s+(?:records|documents)\s+under)\b",
        re.I,
    ), "INFORMATION_REQUEST", "MEDIUM", 720),

    # ── Media / social media escalation ──────────────────────────────────────
    (re.compile(
        r"\b(?:will\s+(?:post|share|tweet|publish|contact|approach|expose)\s+"
        r"(?:this|it|my\s+(?:story|case|complaint))\s+(?:on|to|in)\s+"
        r"(?:twitter|facebook|instagram|youtube|whatsapp|reddit|linkedin|"
        r"news|media|newspaper|tv|press)|"
        r"social\s+media\s+(?:campaign|post|exposure|viral)|"
        r"press\s+conference|media\s+attention|tv\s+channel|"
        r"contact(?:ed|ing)?\s+(?:the\s+)?(?:press|media|journalist|reporter|news))\b",
        re.I,
    ), "MEDIA_ESCALATION", "HIGH", 4),

    # ── Ombudsman — explicit threat ────────────────────────────────────────────
    (re.compile(
        r"\b(?:"
        # "will file/lodge/... [a] [complaint] [with] [the] <ombudsman>"
        r"will\s+(?:file|lodge|submit|approach|go\s+to|escalate\s+to|refer\s+to)"
        r"\s+(?:a\s+)?(?:complaint\s+)?(?:with\s+)?(?:the\s+)?"
        r"(?:sector\s+ombudsman|"
        r"ombudsman\s+for\s+(?:the\s+)?(?:sector|service))|"
        # "file/lodge a complaint with the ombudsman"
        r"(?:file|lodge)\s+(?:a\s+)?(?:complaint\s+)?with\s+(?:the\s+)?"
        r"(?:telecom\s+ombudsman|sector\s+ombudsman|ombudsman)|"
        # forced/am going to approach ombudsman
        r"forced\s+to\s+(?:approach|go\s+to|file\s+(?:a\s+complaint\s+)?with)\s+(?:the\s+)?(?:\w+\s+)*ombudsman|"
        r"(?:am\s+going|going)\s+to\s+(?:approach|file\s+(?:a\s+)?(?:complaint\s+)?with)\s+(?:the\s+)?"
        r"(?:sector\s+ombudsman|ombudsman)|"
        r"escalate\s+to\s+(?:the\s+)?ombudsman|"
        r"regulator\s+grievance\s+(?:portal|mechanism)"
        r")\b",
        re.I,
    ), "OMBUDSMAN_THREAT", "HIGH", 720),

    # ── Passing regulatory mention (informational) ─────────────────────────────
    (re.compile(
        r"\b(?:the\s+regulator|regulatory\s+authority|licensing\s+authority|"
        r"sector\s+ombudsman|ombudsman|consumer\s+court|legal\s+action|"
        r"police\s+complaint|cyber\s+crime\s+(?:cell|portal))\b",
        re.I,
    ), "REGULATORY_MENTION", "LOW", 0),
]

# Additional signals that raise severity when combined with a breach type
_URGENCY_BOOSTERS = re.compile(
    r"\b(?:immediately|urgent|urgent(?:ly)?|legal\s+action|within\s+\d+\s+(?:hours?|days?)|"
    r"last\s+warning|final\s+notice|no\s+choice\s+but|forced\s+to|had\s+no\s+option)\b",
    re.I,
)

# Compliance SLA mapping (override hours per type)
_COMPLIANCE_SLA: dict[str, int] = {
    "COURT_ORDER": 24,
    "OMBUDSMAN_COMPLAINT": 720,   # 30 days
    "LEGAL_NOTICE": 720,
    "CONSUMER_FORUM": 504,        # 21 days
    "REGULATOR_PORTAL": 720,
    "REGULATOR_PORTAL": 720,
    "INFORMATION_REQUEST": 720,
    "MEDIA_ESCALATION": 4,
    "OMBUDSMAN_THREAT": 720,
    "REGULATORY_MENTION": 0,      # no SLA override
}

# Escalation paths per type
_ESCALATION_PATH: dict[str, str] = {
    "COURT_ORDER":           "Legal & Compliance → MD Office → External Legal Counsel (immediate)",
    "OMBUDSMAN_COMPLAINT":   "Compliance Desk → Nodal Officer → Ombudsman Liaison",
    "LEGAL_NOTICE":          "Legal & Compliance → Regional Manager",
    "CONSUMER_FORUM":        "Customer Grievance Cell → Legal → Consumer Forum Coordinator",
    "REGULATOR_PORTAL":            "Nodal Officer → Customer Service Compliance Desk",
    "REGULATOR_PORTAL":        "Compliance → Regulator Liaison",
    "INFORMATION_REQUEST":           "Records Officer → Disclosure Desk",
    "MEDIA_ESCALATION":      "Corporate Communications → PR Desk → Regional Head (4-hour response)",
    "OMBUDSMAN_THREAT":      "Compliance Desk → Nodal Officer (pre-emptive response)",
    "REGULATORY_MENTION":    "Standard customer service pipeline",
}


def detect_regulatory_breach(
    text: str,
    *,
    triage_regulatory_flag: bool = False,
    attachment_texts: list[str] | None = None,
) -> dict[str, Any]:
    """
    Scan email text (and any extracted attachment text) for regulatory / legal signals.

    Returns:
        detected:             bool
        breach_type:          str   — e.g. "OMBUDSMAN_COMPLAINT"
        severity:             str   — "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
        compliance_sla_hours: int   — statutory response window (0 = no override)
        escalation_path:      str
        all_signals:          list[str]  — all matched types in priority order
        urgency_boosted:      bool  — urgency language found alongside breach
        override_priority:    str | None — suggested case priority override
        details:              str   — operator-facing explanation
    """
    combined = text
    if attachment_texts:
        combined = text + "\n" + "\n".join(attachment_texts)

    matched_types: list[str] = []
    top_type: str | None = None
    top_severity: str = "LOW"
    top_sla: int = 0

    for pattern, breach_type, severity, sla_h in _BREACH_PATTERNS:
        if pattern.search(combined):
            matched_types.append(breach_type)
            if top_type is None:
                top_type = breach_type
                top_severity = severity
                top_sla = sla_h

    has_urgency = bool(_URGENCY_BOOSTERS.search(combined))

    # If only a REGULATORY_MENTION but triage already flagged it, keep as MEDIUM
    if top_type == "REGULATORY_MENTION" and triage_regulatory_flag:
        top_severity = "MEDIUM"

    # Urgency language with HIGH severity → elevate to CRITICAL for anything except MENTION
    if has_urgency and top_severity == "HIGH" and top_type not in ("REGULATORY_MENTION",):
        top_severity = "CRITICAL"

    detected = top_type is not None and top_type != "REGULATORY_MENTION"

    priority_override: str | None = None
    if top_severity == "CRITICAL":
        priority_override = "CRITICAL"
    elif top_severity == "HIGH":
        priority_override = "HIGH"

    # Build a human-readable explanation for the operator
    details = ""
    if top_type == "COURT_ORDER":
        details = "A court order, writ, or injunction has been referenced. Legal counsel must be notified immediately."
    elif top_type == "OMBUDSMAN_COMPLAINT":
        details = "Customer has already filed a complaint with the Service Ombudsman. the regulator mandates resolution within 30 days of receipt."
    elif top_type == "LEGAL_NOTICE":
        details = "A formal legal/advocate notice has been issued or referenced. Statutory reply window applies."
    elif top_type == "CONSUMER_FORUM":
        details = "The customer has approached, or referenced, a consumer court. 21-day statutory reply period."
    elif top_type == "REGULATOR_PORTAL":
        details = "Complaint filed or referenced on the regulator's portal. 30-day mandated resolution window."
    elif top_type == "REGULATOR_PORTAL":
        details = "A regulator portal complaint was referenced. 30-day mandate."
    elif top_type == "INFORMATION_REQUEST":
        details = "A statutory records request was filed or threatened. 30-day response window."
    elif top_type == "MEDIA_ESCALATION":
        details = "Customer has threatened or initiated media/social-media exposure. Respond within 4 hours to prevent reputational damage."
    elif top_type == "OMBUDSMAN_THREAT":
        details = "Customer explicitly intends to file with the Service Ombudsman. Pre-emptive resolution within the standard SLA avoids formal escalation."
    elif top_type == "REGULATORY_MENTION":
        details = "A regulator or court was mentioned but no active filing or explicit threat was detected."

    return {
        "detected": detected,
        "breach_type": top_type or "NONE",
        "severity": top_severity if top_type else "NONE",
        "compliance_sla_hours": top_sla,
        "escalation_path": _ESCALATION_PATH.get(top_type or "", "Standard pipeline"),
        "all_signals": matched_types,
        "urgency_boosted": has_urgency,
        "override_priority": priority_override,
        "details": details,
    }

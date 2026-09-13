from __future__ import annotations

from typing import Any

from ..catalog.taxonomy import keyword_match, primary_type_to_classification


# Gemma's Type E means "regulatory bodies OR formal legal/court notices" (see
# triage_system prompt). The taxonomy maps E → REGULATORY first, leaving
# LEGAL_NOTICE unreachable via the letter — which would silently downgrade a
# court/legal notice from CRITICAL to HIGH. These high-precision signals split
# the two apart again. Kept deliberately narrow to avoid substring false hits.
_LEGAL_NOTICE_SIGNALS = (
    "legal notice", "court case", "court order", "consumer forum", "ombudsman",
    "lawsuit", "arbitration", "summons", "writ petition", "advocate",
)


def canonical_classification(triage_result: dict[str, Any]) -> str:
    """
    Map a triage result to a canonical classification label using taxonomy.

    Priority order:
    1. Direct primary_type → taxonomy label mapping
    2. Keyword matching on parsed text
    3. Taxonomy fallback based on secondary tags
    4. Default: CUSTOMER_GRIEVANCE
    """
    primary = triage_result.get("primary_type")
    tags = set(triage_result.get("secondary_tags") or [])
    text = " ".join([
        triage_result.get("parsed_text", ""),
        triage_result.get("llm_summary", ""),
        triage_result.get("issue_summary", ""),
    ]).lower()

    # 1. Direct taxonomy mapping from primary type letter
    if primary:
        mapped = primary_type_to_classification(primary)
        # Type E collapses regulatory + legal/court notices onto REGULATORY; a
        # genuine court/legal notice must stay LEGAL_NOTICE (CRITICAL), not be
        # downgraded to a general regulatory matter (HIGH).
        if mapped == "REGULATORY" and any(sig in text for sig in _LEGAL_NOTICE_SIGNALS):
            return "LEGAL_NOTICE"
        if mapped:
            return mapped

    # 2. Tag-based overrides (high-specificity signals)
    if "ACCOUNT-ACCESS" in tags:
        return "PORTAL_ACCESS"

    # 3. Text-based keyword matching via taxonomy
    keyword_cls = keyword_match(text)
    if keyword_cls:
        return keyword_cls

    # 4. Database match state
    if triage_result.get("database_matching_state") in {"EXACT_MATCH", "PARTIAL_MATCH"}:
        return "DUPLICATE"

    # 5. Default
    return "CUSTOMER_GRIEVANCE"


def confidence_from_triage(
    triage_result: dict[str, Any],
    raw_model_confidence: float | None = None,
) -> tuple[float, str | None]:
    """
    Return (confidence, fallback_reason).

    Uses the model's own confidence output when available.
    Falls back to rule-based estimate only when the model didn't provide one.
    """
    fallback_reason: str | None = None
    audit_text = " ".join(
        str(item.get("action", "")) + " " + str(item.get("detail", ""))
        for item in triage_result.get("audit_log", [])
    )

    model_used_fallback = "fallback" in audit_text.lower() or "llm_failure" in audit_text.lower()

    if model_used_fallback:
        fallback_reason = "model_unavailable_or_malformed_output"
        return 0.52, fallback_reason

    # Use the real model-reported confidence when available
    if raw_model_confidence is not None:
        try:
            conf = float(raw_model_confidence)
            conf = max(0.0, min(1.0, conf))
            return round(conf, 4), None
        except (TypeError, ValueError):
            pass

    # Model ran successfully but didn't report confidence — use moderate default
    if triage_result.get("requires_human_review"):
        return 0.72, None

    return 0.82, None

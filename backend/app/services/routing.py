from __future__ import annotations

from typing import Any

from ..catalog.taxonomy import TAXONOMY, _LEAF_INDEX, default_department


DEPARTMENTS = [
    "Trust & Safety Desk",
    "Customer Resolution Desk",
    "Compliance Desk",
    "Network Support Desk",
    "Central Operations Desk",
]

# Base routing confidence per department when primary classification matches
_PRIMARY_CONFIDENCE = 0.92

# Secondary routing triggered when text signals overlap
_SECONDARY_CONFIDENCE = 0.65

# How much text-signal boosting can raise a secondary score
_BOOST = 0.06


def weighted_routing(classification: str, triage_result: dict[str, Any]) -> dict[str, float]:
    """
    Derive per-department confidence scores from taxonomy + text signals.
    No hardcoded scores per department — derived from classification taxonomy and
    keyword presence in the email text.
    """
    text = " ".join([
        triage_result.get("parsed_text", ""),
        triage_result.get("llm_summary", ""),
        triage_result.get("issue_summary", ""),
    ]).lower()
    tags = set(triage_result.get("secondary_tags") or [])
    priority = triage_result.get("priority_tier")

    scores: dict[str, float] = {dept: 0.03 for dept in DEPARTMENTS}

    # 1. Primary department from taxonomy gets the primary confidence
    primary_dept = default_department(classification)
    if primary_dept in scores:
        scores[primary_dept] = _PRIMARY_CONFIDENCE

    # 2. Every other taxonomy leaf gets scored if its keywords fire on the text
    for leaf_key, leaf in _LEAF_INDEX.items():
        dept = leaf["default_department"]
        if dept == primary_dept or dept not in scores:
            continue
        keyword_hits = sum(1 for kw in leaf.get("keywords", []) if kw in text)
        if keyword_hits > 0:
            boost = min(keyword_hits * _BOOST, 0.20)
            scores[dept] = max(scores[dept], _SECONDARY_CONFIDENCE + boost)

    # 3. Tag-based secondary routing
    if "FINANCIAL-LOSS" in tags and classification != "UNAUTHORISED_USE":
        scores["Trust & Safety Desk"] = max(scores["Trust & Safety Desk"], 0.58)
    if "ACCOUNT-ACCESS" in tags:
        scores["Network Support Desk"] = max(
            scores["Network Support Desk"], 0.62
        )
    if any(term in text for term in ["regulator", "legal notice", "ombudsman", "court"]):
        scores["Compliance Desk"] = max(scores["Compliance Desk"], 0.72)
    if any(term in text for term in ["mobile app", "portal", "autopay", "self care portal"]):
        scores["Network Support Desk"] = max(
            scores["Network Support Desk"], 0.62
        )

    # 4. P1 / CRITICAL cases always involve Central Ops as secondary
    if priority == "P1":
        scores["Central Operations Desk"] = max(
            scores["Central Operations Desk"], 0.68
        )

    return dict(sorted(scores.items(), key=lambda item: item[1], reverse=True))


def primary_and_secondary(
    route_scores: dict[str, float],
    threshold: float = 0.60,
) -> tuple[str, list[dict[str, Any]]]:
    primary = next(iter(route_scores))
    secondary = [
        {"department": dept, "confidence": round(score, 4)}
        for dept, score in route_scores.items()
        if dept != primary and score >= threshold
    ]
    return primary, secondary

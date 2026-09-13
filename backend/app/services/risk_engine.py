"""
risk_engine.py — composite, explainable risk scoring for Docket-scale prioritisation.

Why this exists
---------------
The categorical priority rules (priority.py) bucket every case into one of four
bands. At Docket volume those bands flood — thousands of cases land in CRITICAL/HIGH
with no way to order them. This engine produces a continuous 0–100 score with a
transparent factor breakdown that the queue uses to rank-order cases *within* a
band. It does not replace the rule engine; it sharpens it.

Design
------
* compute_risk_assessment()  — pure function. Takes the same normalised signals
  the priority rules already consume, plus customer segment, aging, and
  blast-radius context, and returns a RiskAssessment with:
      - score          0–100 composite
      - normalized     score / 100  (written to Case.risk_score, 0–1 float)
      - band           CRITICAL/HIGH/MEDIUM/LOW derived from score
      - factors        list of {name, contribution, detail} — fully auditable

Every term is additive and capped, so the contribution of each factor is visible
and explainable in the audit trail — no opaque model, no black box.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# ── Category severity — the dominant base term ──────────────────────────────
# A desk triages on *what kind* of problem this is first; everything else modulates.
_CATEGORY_BASE: dict[str, float] = {
    "LEGAL_NOTICE": 58.0,
    "UNAUTHORISED_USE": 55.0,
    "REGULATORY": 52.0,
    "PORTAL_ACCESS": 50.0,
    "ESCALATION": 45.0,
    "PAYMENT_FAILURE": 40.0,
    "DISPUTE": 36.0,
    "CONNECTION_FAULT": 30.0,
    "COMPLAINT": 28.0,
    "GRIEVANCE": 28.0,
    "SERVICE_REQUEST": 18.0,
    "GENERAL_QUERY": 12.0,
}
_CATEGORY_DEFAULT = 20.0

# ── Customer segment weighting ──────────────────────────────────────────────
# Two distinct concerns: vulnerability (consumer-protection duty) and commercial value.
_VULNERABLE_SEGMENTS = {"SENIOR_CITIZEN", "PENSIONER"}
_SEGMENT_VALUE_UPLIFT: dict[str, float] = {
    "HNI": 8.0,
    "PRIORITY": 8.0,
    "CORPORATE": 7.0,
    "NRI": 5.0,
    "AGRI": 3.0,
}

# ── Band thresholds on the composite score ──────────────────────────────────
_BAND_THRESHOLDS = (("CRITICAL", 80.0), ("HIGH", 60.0), ("MEDIUM", 35.0), ("LOW", 0.0))

# Per-factor caps so no single term can dominate unfairly.
_MONETARY_CAP = 20.0
_AGING_CAP = 15.0
_BLAST_CAP = 12.0

_PRIORITY_RANK: dict[str, int] = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}


def risk_band(normalized: float) -> str:
    """Map a normalised (0–1) risk score onto a priority band."""
    return _band_for(max(0.0, min(100.0, normalized * 100.0)))


def escalate_priority(current_priority: str, normalized_risk: float) -> str:
    """
    Lift a rule-derived priority when the composite risk band is strictly higher.
    Only ever escalates — never downgrades — so the categorical rules remain a floor.
    """
    band = risk_band(normalized_risk)
    if _PRIORITY_RANK.get(band, 0) > _PRIORITY_RANK.get(current_priority or "LOW", 0):
        return band
    return current_priority


def aging_contribution(case_age_minutes: float | None, sla_resolution_minutes: float | None) -> float:
    """
    SLA decay term, factored out so the rerank loop can re-float waiting cases
    without rebuilding the full signal set. Zero until half the resolution window
    has elapsed, then ramps to the cap at the deadline.
    """
    if not case_age_minutes or not sla_resolution_minutes or sla_resolution_minutes <= 0:
        return 0.0
    ratio = case_age_minutes / sla_resolution_minutes
    if ratio <= 0.5:
        return 0.0
    return min(_AGING_CAP, _AGING_CAP * (ratio - 0.5) / 0.5)


@dataclass
class RiskAssessment:
    score: float
    normalized: float
    band: str
    factors: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "normalized": round(self.normalized, 4),
            "band": self.band,
            "factors": self.factors,
            "engine": "composite_v1",
        }


def _band_for(score: float) -> str:
    for band, threshold in _BAND_THRESHOLDS:
        if score >= threshold:
            return band
    return "LOW"


def _normalize_segment(segment: str | None) -> str:
    if not segment:
        return ""
    return "".join(ch.upper() if ch.isalnum() else "_" for ch in segment).strip("_")


def _monetary_contribution(amount: float | None) -> tuple[float, str]:
    """
    Graduated, log-scaled exposure — not a single threshold. ₹1L ≈ +8,
    ₹10L ≈ +13, ₹1Cr ≈ +18, ₹10Cr+ saturates at the cap. This is what lets a
    ₹5Cr dispute outrank a ₹2,000 one even when both are the same category.
    """
    if not amount or amount <= 0:
        return 0.0, "no monetary exposure detected"
    contribution = min(_MONETARY_CAP, 4.0 * math.log10(amount / 1000.0 + 1.0))
    contribution = max(0.0, contribution)
    return contribution, f"₹{amount:,.0f} at risk"


def compute_risk_assessment(
    *,
    category: str,
    signals: dict[str, Any],
    customer_segment: str | None = None,
    impersonation_level: str | None = None,
    regulatory_override: str | None = None,
    case_age_minutes: float | None = None,
    sla_resolution_minutes: float | None = None,
    correlated_case_count: int = 0,
) -> RiskAssessment:
    """
    Combine weighted, capped factors into a 0–100 composite risk score.

    `signals` is the same dict priority.resolve_department_priority() consumes:
    expects keys monetary_amount_detected, regulatory_flag, repeat_flag, sentiment.
    """
    factors: list[dict[str, Any]] = []

    def add(name: str, contribution: float, detail: str) -> None:
        if contribution:
            factors.append({"name": name, "contribution": round(contribution, 1), "detail": detail})

    # 1) Category severity — base term.
    base = _CATEGORY_BASE.get((category or "").upper(), _CATEGORY_DEFAULT)
    add("category_severity", base, f"{category or 'UNCLASSIFIED'} base severity")
    score = base

    # 2) Monetary exposure — graduated.
    mon, mon_detail = _monetary_contribution(signals.get("monetary_amount_detected"))
    add("monetary_exposure", mon, mon_detail)
    score += mon

    # 3) Regulatory exposure.
    if signals.get("regulatory_flag"):
        add("regulatory_exposure", 10.0, "regulator, legal or ombudsman reference present")
        score += 10.0
    if (regulatory_override or "").upper() == "CRITICAL":
        add("regulatory_breach", 12.0, "confirmed regulatory breach (critical)")
        score += 12.0
    elif (regulatory_override or "").upper() == "HIGH":
        add("regulatory_breach", 6.0, "confirmed regulatory breach (high)")
        score += 6.0

    # 4) Impersonation / identity risk.
    _imp = (impersonation_level or "").upper()
    _imp_weight = {"CRITICAL": 14.0, "HIGH": 9.0, "MEDIUM": 5.0}.get(_imp, 0.0)
    if _imp_weight:
        add("impersonation_risk", _imp_weight, f"impersonation signal: {_imp}")
        score += _imp_weight

    # 5) Customer vulnerability — consumer-protection duty of care.
    seg = _normalize_segment(customer_segment)
    if seg in _VULNERABLE_SEGMENTS:
        add("customer_vulnerability", 10.0, f"vulnerable segment: {seg}")
        score += 10.0
    # 6) Customer commercial value.
    value_uplift = _SEGMENT_VALUE_UPLIFT.get(seg, 0.0)
    if value_uplift:
        add("customer_value", value_uplift, f"value segment: {seg}")
        score += value_uplift

    # 7) Repeat contact / unresolved history.
    if signals.get("repeat_flag"):
        add("repeat_contact", 8.0, "repeat or follow-up on an unresolved issue")
        score += 8.0

    # 8) Sentiment.
    if signals.get("sentiment") == "NEGATIVE":
        add("negative_sentiment", 5.0, "distressed / escalation-prone tone")
        score += 5.0

    # 9) Blast-radius — many customers hit by the same issue is systemic.
    if correlated_case_count > 1:
        blast = min(_BLAST_CAP, 3.0 * math.log2(correlated_case_count))
        add("blast_radius", blast, f"{correlated_case_count} correlated cases (systemic signal)")
        score += blast

    # 10) Aging / SLA decay — surfaces cases that have been waiting.
    decay = aging_contribution(case_age_minutes, sla_resolution_minutes)
    if decay:
        ratio = case_age_minutes / sla_resolution_minutes
        add("sla_aging", decay, f"{ratio:.0%} of resolution window elapsed")
        score += decay

    score = max(0.0, min(100.0, score))
    return RiskAssessment(
        score=score,
        normalized=score / 100.0,
        band=_band_for(score),
        factors=factors,
    )

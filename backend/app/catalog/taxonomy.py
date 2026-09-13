"""
Service-desk taxonomy for a connectivity provider.

Category -> subcategory -> canonical classification label. Every leaf carries the
desk that owns it by default and the tier its clock runs on. This is the single
source of truth for classification labels and routing; nothing else hardcodes a
desk name.

DESKS is the registry of owning teams. A leaf names a desk by key rather than by
label, so a desk can be renamed in one place without touching every leaf.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

TAXONOMY_VERSION = "1.0"

DESKS: dict[str, str] = {
    "trust":       "Trust & Safety Desk",
    "network":     "Network Support Desk",
    "billing":     "Billing & Contracts Desk",
    "compliance":  "Compliance Desk",
    "resolution":  "Customer Resolution Desk",
    "central":     "Central Operations Desk",
    "roaming":     "Roaming & International Desk",
    "equipment":   "Equipment & Provisioning Desk",
}

TAXONOMY: dict[str, dict[str, Any]] = {
    "ACCOUNT_SECURITY": {
        "label": "Account Security",
        "subcategories": {
            "UNAUTHORISED_USE": {
                "label": "Unauthorised Use / Account Takeover",
                "desk": "trust",
                "sla_tier": "CRITICAL",
                "primary_types": {"F", "G"},
                "keywords": [
                    "unauthorized", "unauthorised", "sim swap", "phishing", "scam",
                    "otp shared", "account hacked", "identity theft", "without my consent",
                    "someone else is using", "fraudulent",
                ],
                "bert_description": (
                    "unauthorised access stolen line sim swap phishing scam account hacked "
                    "OTP shared usage without consent number ported without permission"
                ),
            },
            "PORTAL_ACCESS": {
                "label": "Portal Access / Login Failure",
                "desk": "network",
                "sla_tier": "HIGH",
                "primary_types": set(),
                "keywords": [
                    "account locked", "cannot login", "self care blocked",
                    "unable to access", "forgot password", "locked out", "login failed",
                ],
                "bert_description": (
                    "cannot log in to the self care portal password reset account locked "
                    "app will not open authentication failure session expired"
                ),
            },
        },
    },
    "BILLING": {
        "label": "Billing & Payments",
        "subcategories": {
            "PAYMENT_FAILURE": {
                "label": "Payment Failed / Not Credited",
                "desk": "billing",
                "sla_tier": "HIGH",
                "primary_types": {"A"},
                "keywords": [
                    "payment failed", "amount debited not credited", "double charged",
                    "autopay failed", "refund not received", "paid twice",
                    "recharge failed", "money deducted",
                ],
                "bert_description": (
                    "bill payment failed amount taken but not applied duplicate charge "
                    "refund pending autopay declined recharge did not reflect"
                ),
            },
            "BILL_DISPUTE": {
                "label": "Bill Dispute / Unexpected Charge",
                "desk": "billing",
                "sla_tier": "MEDIUM",
                "primary_types": set(),
                "keywords": [
                    "bill is wrong", "overcharged", "charge i did not", "unexpected charge",
                    "tariff changed", "plan price increased", "billed for a service",
                ],
                "bert_description": (
                    "monthly bill higher than expected charged for something not used "
                    "tariff increase disputed line item proration query"
                ),
            },
        },
    },
    "SERVICE_FAULT": {
        "label": "Service Faults",
        "subcategories": {
            "CONNECTION_FAULT": {
                "label": "Connection Down / Degraded",
                "desk": "network",
                "sla_tier": "HIGH",
                "primary_types": {"B"},
                "keywords": [
                    "no internet", "connection down", "line is dead", "packet loss",
                    "slow speed", "keeps dropping", "outage", "no signal",
                    "latency", "buffering",
                ],
                "bert_description": (
                    "internet not working line down intermittent drops slow speeds "
                    "high latency packet loss no signal outage in my area"
                ),
            },
            "INSTALLATION_DELAY": {
                "label": "Installation / Activation Delay",
                "desk": "equipment",
                "sla_tier": "MEDIUM",
                "primary_types": set(),
                "keywords": [
                    "installation pending", "engineer did not", "not activated",
                    "waiting for installation", "appointment missed", "new connection delay",
                ],
                "bert_description": (
                    "new connection not installed technician did not arrive activation "
                    "pending for days appointment missed provisioning stuck"
                ),
            },
        },
    },
    "COMPLIANCE": {
        "label": "Compliance & Legal",
        "subcategories": {
            "REGULATORY": {
                "label": "Regulatory Reference",
                "desk": "compliance",
                "sla_tier": "CRITICAL",
                "primary_types": {"E"},
                "keywords": [
                    "regulator", "ombudsman", "consumer forum", "regulatory complaint",
                    "escalating to the authority", "statutory",
                ],
                "bert_description": (
                    "complaint referred to the regulator ombudsman consumer forum "
                    "statutory obligation regulatory deadline authority directive"
                ),
            },
            "LEGAL_NOTICE": {
                "label": "Legal Notice",
                "desk": "compliance",
                "sla_tier": "CRITICAL",
                "primary_types": set(),
                "keywords": [
                    "legal notice", "court", "summons", "advocate", "litigation",
                    "sue", "legal action",
                ],
                "bert_description": (
                    "legal notice served court summons advocate letter litigation "
                    "threat of legal action solicitor correspondence"
                ),
            },
            "VERIFICATION_QUERY": {
                "label": "Identity Verification",
                "desk": "compliance",
                "sla_tier": "MEDIUM",
                "primary_types": set(),
                "keywords": [
                    "verification pending", "document rejected", "address proof",
                    "identity proof", "re-verification", "documents not accepted",
                ],
                "bert_description": (
                    "identity verification pending documents rejected address proof "
                    "resubmission re-verification of the connection holder"
                ),
            },
        },
    },
    "CUSTOMER_SERVICE": {
        "label": "Customer Service",
        "subcategories": {
            "CUSTOMER_GRIEVANCE": {
                "label": "Grievance / Dissatisfaction",
                "desk": "resolution",
                "sla_tier": "HIGH",
                "primary_types": {"C"},
                "keywords": [
                    "complaint", "grievance", "no response", "third time", "disappointed",
                    "poor service", "nobody called back", "still unresolved",
                ],
                "bert_description": (
                    "repeated complaint no response from support unresolved for weeks "
                    "dissatisfied with the service promised callback never came"
                ),
            },
            "ESCALATION": {
                "label": "Escalation",
                "desk": "central",
                "sla_tier": "CRITICAL",
                "primary_types": {"D"},
                "keywords": [
                    "escalate", "escalation", "speak to a manager", "senior management",
                    "chief executive", "final reminder",
                ],
                "bert_description": (
                    "escalating to senior management demand for a manager final "
                    "reminder before further action executive office complaint"
                ),
            },
            "DUPLICATE": {
                "label": "Duplicate / Follow-up",
                "desk": "central",
                "sla_tier": "LOW",
                "primary_types": set(),
                "keywords": [
                    "as per my previous", "following up", "reminder", "already raised",
                    "same issue as", "duplicate",
                ],
                "bert_description": (
                    "follow up on an existing ticket reminder about a previous mail "
                    "duplicate of a case already open reference to an earlier reply"
                ),
            },
            "GENERAL_QUERY": {
                "label": "General Query",
                "desk": "resolution",
                "sla_tier": "LOW",
                "primary_types": {"H"},
                "keywords": [
                    "how do i", "what is the", "please tell me", "information about",
                    "would like to know", "enquiry",
                ],
                "bert_description": (
                    "general question about a plan or a process request for information "
                    "how to change something enquiry with no fault reported"
                ),
            },
        },
    },
    "CONTRACTS": {
        "label": "Contracts & Plans",
        "subcategories": {
            "CONTRACT_QUERY": {
                "label": "Contract / Plan Query",
                "desk": "billing",
                "sla_tier": "MEDIUM",
                "primary_types": set(),
                "keywords": [
                    "contract terms", "lock in", "plan change", "upgrade my plan",
                    "downgrade", "tenure", "instalment plan",
                ],
                "bert_description": (
                    "question about contract terms lock in period plan upgrade or "
                    "downgrade instalment tenure remaining on the agreement"
                ),
            },
            "INSTALMENT_ISSUE": {
                "label": "Instalment / Arrears Issue",
                "desk": "billing",
                "sla_tier": "HIGH",
                "primary_types": set(),
                "keywords": [
                    "instalment not deducted", "arrears", "in arrears", "missed instalment",
                    "instalment debited twice", "payment plan",
                ],
                "bert_description": (
                    "device instalment missed or taken twice account in arrears "
                    "payment plan not honoured dues shown incorrectly"
                ),
            },
            "CONTRACT_CLOSURE": {
                "label": "Termination / Closure",
                "desk": "billing",
                "sla_tier": "MEDIUM",
                "primary_types": set(),
                "keywords": [
                    "cancel my connection", "terminate", "closure request", "disconnect",
                    "final bill", "early exit", "port out",
                ],
                "bert_description": (
                    "request to terminate the service closure of the account final "
                    "bill after disconnection early exit charge port out request"
                ),
            },
            "CONTRACT_ACTIVATION": {
                "label": "New Contract / Activation",
                "desk": "equipment",
                "sla_tier": "MEDIUM",
                "primary_types": set(),
                "keywords": [
                    "new connection", "activate my plan", "order placed", "sign up",
                    "not yet activated", "awaiting activation",
                ],
                "bert_description": (
                    "new order placed awaiting activation plan purchased but not live "
                    "sign up incomplete provisioning of a new service"
                ),
            },
        },
    },
    "ROAMING": {
        "label": "Roaming & International",
        "subcategories": {
            "ROAMING_ACCOUNT": {
                "label": "Roaming Service",
                "desk": "roaming",
                "sla_tier": "MEDIUM",
                "primary_types": set(),
                "keywords": [
                    "roaming", "abroad", "overseas", "travelling to", "international pack",
                    "no service abroad",
                ],
                "bert_description": (
                    "roaming pack not working while abroad overseas usage service "
                    "unavailable in another country international bundle activation"
                ),
            },
            "INTERNATIONAL_USAGE": {
                "label": "International Usage Charge",
                "desk": "roaming",
                "sla_tier": "HIGH",
                "primary_types": set(),
                "keywords": [
                    "international charges", "roaming bill", "charged abroad",
                    "premium rate", "idd charges", "long distance",
                ],
                "bert_description": (
                    "unexpected international charges high roaming bill premium rate "
                    "numbers long distance usage billed at the wrong rate"
                ),
            },
        },
    },
    "EQUIPMENT": {
        "label": "Equipment",
        "subcategories": {
            "EQUIPMENT_FAULT": {
                "label": "Faulty Equipment",
                "desk": "equipment",
                "sla_tier": "HIGH",
                "primary_types": set(),
                "keywords": [
                    "router not working", "set top box", "device faulty", "ont",
                    "replacement device", "hardware failure", "keeps restarting",
                ],
                "bert_description": (
                    "router or set top box failed device will not power on hardware "
                    "replacement required equipment keeps restarting"
                ),
            },
            "EQUIPMENT_RETURN": {
                "label": "Equipment Return / Recovery",
                "desk": "equipment",
                "sla_tier": "LOW",
                "primary_types": set(),
                "keywords": [
                    "return the router", "collect the equipment", "pickup not arranged",
                    "equipment charge", "device not collected",
                ],
                "bert_description": (
                    "returning equipment after cancellation collection not arranged "
                    "charged for a device already returned pickup pending"
                ),
            },
        },
    },
    "NOISE": {
        "label": "Noise",
        "subcategories": {
            "SPAM": {
                "label": "Spam / Marketing",
                "desk": "resolution",
                "sla_tier": "LOW",
                "primary_types": {"I"},
                "keywords": [
                    "unsubscribe", "promotional", "newsletter", "advertisement",
                    "marketing email", "no reply",
                ],
                "bert_description": (
                    "promotional mail marketing newsletter advertisement automated "
                    "no reply message nothing requiring action"
                ),
            },
        },
    },
}

# Every leaf names its desk by key. Expand to the label once, here, so the rest of
# the system keeps reading "default_department" exactly as before.
for _cat in TAXONOMY.values():
    for _leaf in _cat["subcategories"].values():
        _leaf["default_department"] = DESKS[_leaf["desk"]]

# ---------------------------------------------------------------------------
# Flattened leaf index for fast lookup
# ---------------------------------------------------------------------------
_LEAF_INDEX: dict[str, dict[str, Any]] = {}
for _cat_key, _cat in TAXONOMY.items():
    for _leaf_key, _leaf in _cat["subcategories"].items():
        _LEAF_INDEX[_leaf_key] = {**_leaf, "category": _cat_key, "category_label": _cat["label"]}


def get_leaf(classification: str) -> dict[str, Any] | None:
    return _LEAF_INDEX.get(classification)


def all_classifications() -> list[str]:
    return list(_LEAF_INDEX.keys())


def default_department(classification: str) -> str:
    leaf = _LEAF_INDEX.get(classification)
    return leaf["default_department"] if leaf else "Central Operations & Escalation Desk"


def sla_tier(classification: str) -> str:
    leaf = _LEAF_INDEX.get(classification)
    return leaf["sla_tier"] if leaf else "MEDIUM"


def bert_descriptions() -> dict[str, str]:
    """Return {classification: bert_description} for zero-shot classification."""
    return {k: v["bert_description"] for k, v in _LEAF_INDEX.items()}


def primary_type_to_classification(primary_type: str) -> str | None:
    """Map gemma primary_type letter to canonical classification label."""
    for leaf_key, leaf in _LEAF_INDEX.items():
        if primary_type in leaf.get("primary_types", set()):
            return leaf_key
    return None


@lru_cache(maxsize=None)
def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Whole-word/phrase matcher for a taxonomy keyword.

    Word boundaries stop short keywords matching inside longer words — e.g. "sue"
    must not fire on "issue"/"pursue", "fir" must not fire on "confirm"/"first".
    Multi-word phrases ("legal notice", "amount debited not credited") match on
    their internal whitespace as written.
    """
    return re.compile(r"\b" + re.escape(keyword) + r"\b")


def keyword_hits(text_lower: str, keywords: list[str]) -> int:
    """Count how many of *keywords* appear as whole words/phrases in lowercased text."""
    return sum(1 for kw in keywords if _keyword_pattern(kw).search(text_lower))


def keyword_match(text: str) -> str | None:
    """Return highest-priority matching classification based on keyword hits, or None.

    Selection: the most-severe SLA tier wins first; within the same tier the leaf
    with the most keyword hits wins (leaf key breaks any remaining tie, for
    determinism).
    """
    lower = text.lower()
    # Priority: CRITICAL > HIGH > MEDIUM > LOW
    tier_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    best: tuple[int, int, str] | None = None  # (rank, -hits, leaf_key) — smallest wins
    for leaf_key, leaf in _LEAF_INDEX.items():
        hits = keyword_hits(lower, leaf.get("keywords", []))
        if hits == 0:
            continue
        rank = tier_rank.get(leaf.get("sla_tier", "LOW"), 3)
        candidate = (rank, -hits, leaf_key)
        if best is None or candidate < best:
            best = candidate
    return best[2] if best else None

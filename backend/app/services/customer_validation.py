"""
Customer validation with impersonation detection.

Impersonation vectors handled:
  1. Cross-customer conflict     — identifiers in the email resolve to different customer records
  2. Sender email mismatch       — sender's address differs from the customer's registered email
  3. Name inconsistency          — name claimed in email doesn't match matched customer record
  4. Mobile-in-body mismatch     — phone number stated in body ≠ registered mobile
  5. Weak single-identifier match — only card last-4 supplied (easily obtained from receipts)
  6. Email domain switch         — same local-part but different domain (e.g. gmail vs protonmail)

Result shape (new fields vs old):
  impersonation_risk: {
      level:          "NONE" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" | "UNKNOWN"
      score:          int 0-100
      signals:        list[str]       — human-readable reasons
      recommendation: str
  }
  cross_conflict:     {has_conflict, has_strong_conflict, conflicting_customer_count, ...}
  claimed_names:      list[str]       — names extracted from email body
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from ..models import Subscriber


# ── Regex extractors ──────────────────────────────────────────────────────────

_ACCOUNT_RE = re.compile(r"\b(\d[\d\s\-]{9,14}\d)\b")
_CUSTOMER_REF_RE     = re.compile(
    r"\b(?:customer_ref|customer\s*id|customer_ref\s*no\.?|customer_ref_no)\s*[:\-#]?\s*(\d[\d\s]{8,12}\d)\b", re.I
)
_CARD_RE    = re.compile(r"\b(\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4})\b")
_UPI_RE     = re.compile(
    r"\b([\w.\-]+@(?:okbank|okaxis|okicici|ybl|upi|paytm|gpay))\b", re.I
)
_MOBILE_RE  = re.compile(r"(?<!\d)(\+?91[\s\-]?)?([6-9]\d{9})(?!\d)")
_EXCHANGE_CODE_RE    = re.compile(r"\b(EX-\d{3})\b", re.I)

# Name extraction from email body — multiple heuristic patterns
_NAME_PATTERNS: list[re.Pattern[str]] = [
    # Signature block: "Regards,\nRajesh Kumar"
    re.compile(
        r"(?:regards|sincerely|thanks|warm regards|best regards|yours truly|thank you)[,.]?\s*\n+\s*"
        r"([A-Z][a-z]+(?: [A-Z][a-z]+){0,3})",
        re.I,
    ),
    # "My name is X" / "My good name is X"
    re.compile(r"my\s+(?:good\s+)?name\s+is\s+([A-Z][a-z]+(?: [A-Z][a-z]+){0,3})", re.I),
    # "I am X" — exclude common false positives like "I am writing/calling/facing"
    re.compile(
        r"\bI\s+am\s+([A-Z][a-z]+(?: [A-Z][a-z]+){1,3})\b"
        r"(?!\s+(?:writing|calling|facing|unable|not|from|a\b|an\b|the\b))",
        re.I,
    ),
    # "This is X" / "This is X writing/calling"
    re.compile(
        r"this\s+is\s+([A-Z][a-z]+(?: [A-Z][a-z]+){0,3})"
        r"(?:\s+(?:writing|calling|here|from|speaking))?",
        re.I,
    ),
    # Standalone full-name line (first + at least one more word, properly capitalised)
    re.compile(r"^([A-Z][a-z]+(?: [A-Z][a-z]+){1,3})\s*$", re.M),
]


# ── String utilities ──────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    """Strip spaces, dashes, dots from a numeric string."""
    return re.sub(r"[\s\-\.]", "", s)


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j] + (ca != cb), prev[j + 1] + 1, curr[j] + 1))
        prev = curr
    return prev[-1]


def _email_domain(email: str) -> str:
    return email.split("@", 1)[1].lower().strip() if "@" in email else ""


def _email_local(email: str) -> str:
    return email.split("@", 1)[0].lower().strip() if "@" in email else email.lower().strip()


def _name_similarity(a: str, b: str) -> float:
    """Jaccard token overlap between two name strings, 0.0–1.0."""
    a_tok = set(a.lower().split())
    b_tok = set(b.lower().split())
    if not a_tok or not b_tok:
        return 0.0
    return len(a_tok & b_tok) / len(a_tok | b_tok)


# ── Identifier extraction ─────────────────────────────────────────────────────

def extract_identifiers(text: str) -> dict[str, list[str]]:
    """Pull candidate identifiers from raw email text, even if malformed."""
    found: dict[str, list[str]] = {
        "connection_ids": [],
        "customer_refs": [],
        "device_serials": [],
        "payment_handles": [],
        "mobile_numbers": [],
        "exchange_code_codes": [],
    }

    for m in _CUSTOMER_REF_RE.finditer(text):
        v = _clean(m.group(1))
        if v not in found["customer_refs"]:
            found["customer_refs"].append(v)

    for m in _CARD_RE.finditer(text):
        v = _clean(m.group(0))
        if len(v) == 16 and v not in found["device_serials"]:
            found["device_serials"].append(v)

    for m in _ACCOUNT_RE.finditer(text):
        v = _clean(m.group(1))
        if 9 <= len(v) <= 18 and v not in found["connection_ids"]:
            found["connection_ids"].append(v)

    for m in _UPI_RE.finditer(text):
        v = m.group(0).lower().strip()
        if v not in found["payment_handles"]:
            found["payment_handles"].append(v)

    for m in _MOBILE_RE.finditer(text):
        v = m.group(2)
        if v not in found["mobile_numbers"]:
            found["mobile_numbers"].append(v)

    for m in _EXCHANGE_CODE_RE.finditer(text):
        v = m.group(1).upper()
        if v not in found["exchange_code_codes"]:
            found["exchange_code_codes"].append(v)

    return found


_NON_NAME_WORDS = frozenset({
    "and", "or", "but", "for", "the", "that", "this", "with", "from",
    "have", "has", "was", "were", "are", "not", "can", "will", "would",
    "could", "should", "may", "might", "your", "our", "my", "is", "am",
    "been", "being", "also", "just", "very", "here", "there", "which",
    "who", "what", "when", "where", "how", "why", "about", "regarding",
})


def extract_claimed_names(text: str) -> list[str]:
    """Extract names the sender claims to be, from signature / self-introduction patterns."""
    seen: dict[str, None] = {}
    for pat in _NAME_PATTERNS:
        for m in pat.finditer(text):
            raw = m.group(1).strip()
            # Truncate to the leading run of properly-capitalised, non-stopword words.
            # This handles cases like "Anita Verma writing regarding..." where the regex
            # greedily captures trailing lowercase words (re.I makes [A-Z] match lowercase).
            cap_words: list[str] = []
            for w in raw.split():
                if w[0].isupper() and w.lower() not in _NON_NAME_WORDS:
                    cap_words.append(w)
                else:
                    break
            if len(cap_words) < 2 or len(" ".join(cap_words)) < 5:
                continue
            if len(cap_words) > 4:
                cap_words = cap_words[:4]
            seen[" ".join(cap_words)] = None
    return list(seen.keys())


# ── Identifier matching helpers ───────────────────────────────────────────────

def _match_account(candidate: str, stored_list: list[str]) -> bool:
    """Exact or Levenshtein-1 match for same-length connection ids."""
    c = _clean(candidate)
    for stored in stored_list:
        s = _clean(stored)
        if c == s:
            return True
        if len(c) == len(s) and _levenshtein(c, s) <= 1:
            return True
    return False


def _card_match_quality(candidate: str, stored_list: list[str]) -> str | None:
    """
    Returns 'full' for a complete 16-digit match, 'last4' when only the
    final four digits match, or None for no match.
    Last-4 only is a weak signal — receipts and phishing expose these.
    """
    c = _clean(candidate)
    for stored in stored_list:
        s = _clean(stored)
        if c == s:
            return "full"
        if c[-4:] == s[-4:]:
            return "last4"
    return None


# ── Cross-customer conflict detection ─────────────────────────────────────────

def _detect_cross_customer_conflict(
    extracted: dict[str, list[str]],
    customers: list[Subscriber],
) -> dict[str, Any]:
    """
    Check whether different identifiers from the email match different customer records.

    A conflict means someone is presenting a mix of identifiers that cannot
    all belong to one person — a hallmark of impersonation attempts.

    Returns:
        has_conflict              — any two identifiers map to different customers
        has_strong_conflict       — at least one strong identifier (customer reference/account) is in conflict
        conflicting_customer_count
        customer_ref_to_identifiers        — {customer_ref: [identifier_keys]} for each matched customer
        identifier_to_customer_ref         — {"type:value": customer_ref} reverse map
    """
    id_to_customer_ref: dict[str, str] = {}

    for cust in customers:
        customer_ref = cust.customer_ref

        for val in extracted.get("customer_refs", []):
            if _clean(val) == _clean(cust.customer_ref or ""):
                id_to_customer_ref.setdefault(f"customer_ref:{val}", customer_ref)

        for val in extracted.get("connection_ids", []):
            if _match_account(val, cust.connection_ids or []):
                id_to_customer_ref.setdefault(f"account:{val}", customer_ref)

        for val in extracted.get("device_serials", []):
            quality = _card_match_quality(val, cust.device_serials or [])
            if quality == "full":
                id_to_customer_ref.setdefault(f"card:{val}", customer_ref)
            # last4-only matches are NOT used for conflict detection (too noisy)

        for val in extracted.get("payment_handles", []):
            if val.lower() in [u.lower() for u in (cust.payment_handles or [])]:
                id_to_customer_ref.setdefault(f"upi:{val}", customer_ref)

        for val in extracted.get("mobile_numbers", []):
            if _clean(val) == _clean(cust.mobile_number or ""):
                id_to_customer_ref.setdefault(f"mobile:{val}", customer_ref)

    # Group by customer
    customer_ref_to_ids: dict[str, list[str]] = {}
    for id_key, customer_ref in id_to_customer_ref.items():
        customer_ref_to_ids.setdefault(customer_ref, []).append(id_key)

    conflict = len(customer_ref_to_ids) > 1
    strong_id_types = {"customer_ref", "account"}
    has_strong = conflict and any(
        id_key.split(":")[0] in strong_id_types for id_key in id_to_customer_ref
    )

    return {
        "has_conflict": conflict,
        "has_strong_conflict": has_strong,
        "conflicting_customer_count": len(customer_ref_to_ids),
        "customer_ref_to_identifiers": customer_ref_to_ids,
        "identifier_to_customer_ref": id_to_customer_ref,
    }


# ── Impersonation risk assessment ─────────────────────────────────────────────

def _assess_impersonation_risk(
    *,
    matched_customer: Subscriber | None,
    extracted: dict[str, list[str]],
    sender_email: str | None,
    sender_name: str | None,
    claimed_names: list[str],
    cross_conflict: dict[str, Any],
    match_score: int,
    matched_fields: list[str],
    card_match_qualities: dict[str, str],  # card_value → "full" | "last4"
) -> dict[str, Any]:
    """
    Aggregate all impersonation signals into a structured risk assessment.

    Score thresholds:
        0-10  → NONE     — consistent, proceed normally
        11-30 → LOW      — minor uncertainty
        31-55 → MEDIUM   — verify before acting on sensitive requests
        56-75 → HIGH     — escalate; do not act without extra verification
        76+   → CRITICAL — reject; flag for fraud investigation
    """
    if matched_customer is None:
        return {
            "level": "UNKNOWN",
            "score": 0,
            "signals": ["No customer record matched — impersonation risk cannot be assessed"],
            "recommendation": "Request a valid connection id or customer reference to verify identity",
        }

    score = 0
    signals: list[str] = []

    # ── 1. Cross-customer identifier conflict ─────────────────────────────────
    if cross_conflict["has_strong_conflict"]:
        score += 50
        n = cross_conflict["conflicting_customer_count"]
        signals.append(
            f"CRITICAL: Strong identifiers (customer reference/account) in this email match {n} different "
            "customer records — likely impersonation or deliberate data mixing"
        )
    elif cross_conflict["has_conflict"]:
        score += 30
        n = cross_conflict["conflicting_customer_count"]
        signals.append(
            f"Identifiers match {n} different customer records — possible impersonation; verify identity"
        )

    # ── 2. Sender email vs registered email ──────────────────────────────────
    registered_email = (matched_customer.email or "").strip().lower()
    if sender_email:
        s_email = sender_email.strip().lower()
        if registered_email:
            if s_email == registered_email:
                score -= 15
                signals.append("Sender email exactly matches registered customer email ✓")
            elif _email_local(s_email) == _email_local(registered_email):
                # Same local part, different domain — suspicious (e.g. raj@gmail vs raj@protonmail)
                score += 20
                signals.append(
                    f"Sender email local part matches registered but domain differs "
                    f"(registered domain: {_email_domain(registered_email)}) — "
                    "possible domain-switch impersonation"
                )
            elif _email_domain(s_email) == _email_domain(registered_email):
                # Same provider, different name
                score += 10
                signals.append(
                    f"Sender is on the same email provider as the registered address "
                    f"but the local part differs (registered: {_email_local(registered_email)}***)"
                )
            else:
                # Completely different email
                score += 25
                signals.append(
                    f"Sender email domain ({_email_domain(s_email)}) does not match registered "
                    f"customer email domain ({_email_domain(registered_email)}) — verify identity"
                )
        else:
            score += 5
            signals.append("No email address on file for this customer — sender identity cannot be email-verified")

    # ── 3. Name consistency ───────────────────────────────────────────────────
    customer_name = matched_customer.name or ""
    all_claimed = [n for n in ([sender_name] if sender_name else []) + claimed_names if n]
    if all_claimed:
        sims = [_name_similarity(n, customer_name) for n in all_claimed]
        best_sim = max(sims)
        best_name = all_claimed[sims.index(best_sim)]
        if best_sim >= 0.7:
            score -= 10
            signals.append(f"Claimed name matches customer record well ({best_sim:.0%} overlap) ✓")
        elif best_sim >= 0.4:
            score += 10
            signals.append(
                f"Claimed name partially matches customer record ({best_sim:.0%} overlap) — verify"
            )
        elif best_sim > 0.0:
            score += 20
            signals.append(
                f"Claimed name '{best_name}' has low overlap with customer record '{customer_name}' "
                f"({best_sim:.0%}) — suspicious"
            )
        else:
            score += 30
            signals.append(
                f"Claimed name '{best_name}' does not match customer record name '{customer_name}' "
                "— high impersonation risk"
            )

    # ── 4. Mobile number in body vs registered ────────────────────────────────
    body_mobiles = extracted.get("mobile_numbers", [])
    registered_mobile = _clean(matched_customer.mobile_number or "")
    if body_mobiles and registered_mobile:
        cleaned_body = [_clean(m) for m in body_mobiles]
        # Accept last-10 suffix match to handle +91 prefix variants
        last10 = registered_mobile[-10:]
        if registered_mobile in cleaned_body or any(m.endswith(last10) for m in cleaned_body):
            score -= 5
            signals.append("Mobile number in email body matches registered number ✓")
        else:
            score += 15
            signals.append(
                "Mobile number stated in email body does not match registered mobile — verify"
            )

    # ── 5. Card match quality ─────────────────────────────────────────────────
    only_last4_cards = [
        v for v, q in card_match_qualities.items() if q == "last4"
    ]
    full_card_matches = [v for v, q in card_match_qualities.items() if q == "full"]
    if only_last4_cards and not full_card_matches:
        score += 20
        signals.append(
            "Match relies on card last-4 digits only — these are visible on receipts and known to "
            "phishing attackers; request additional strong identifier"
        )

    # ── 6. Identifier strength ────────────────────────────────────────────────
    strong_fields = [f for f in matched_fields if f in ("customer_ref", "connection_id")]
    if not strong_fields and matched_fields:
        score += 10
        signals.append(
            "No strong identifier (customer reference or connection id) matched — identity rests on weak signals only"
        )

    # ── 7. Multi-field verification bonus ────────────────────────────────────
    if match_score >= 6 and len(matched_fields) >= 3:
        score -= 15
        signals.append(f"Strong multi-identifier verification ({len(matched_fields)} fields matched) ✓")
    elif match_score >= 3 and len(matched_fields) >= 2:
        score -= 5
        signals.append(f"Moderate multi-identifier verification ({len(matched_fields)} fields matched) ✓")

    score = max(0, min(100, score))

    if score <= 10:
        level = "NONE"
        recommendation = "Identity consistent — proceed with standard processing"
    elif score <= 30:
        level = "LOW"
        recommendation = "Minor uncertainty — standard processing; operator should be aware"
    elif score <= 55:
        level = "MEDIUM"
        recommendation = (
            "Notable discrepancy detected — verify identity via OTP to registered mobile "
            "before acting on any account change or sensitive request"
        )
    elif score <= 75:
        level = "HIGH"
        recommendation = (
            "Strong impersonation signals — escalate to fraud/risk team; "
            "do not process account changes without in-service_area or OTP verification"
        )
    else:
        level = "CRITICAL"
        recommendation = (
            "Near-certain impersonation or deliberate data conflict — reject request; "
            "flag case for immediate fraud investigation and do not disclose any account information"
        )

    return {
        "level": level,
        "score": score,
        "signals": signals,
        "recommendation": recommendation,
    }


# ── Core matching + scoring ───────────────────────────────────────────────────

def validate_against_db(
    extracted: dict[str, list[str]],
    db: Session,
    *,
    sender_email: str | None = None,
    sender_name: str | None = None,
    claimed_names: list[str] | None = None,
) -> dict[str, Any]:
    """
    Match extracted identifiers against customer records, then assess impersonation risk.
    """
    customers: list[Subscriber] = db.query(Subscriber).all()
    claimed_names = claimed_names or []

    best_score = 0
    best_customer: Subscriber | None = None
    best_fields: list[str] = []
    best_card_qualities: dict[str, str] = {}

    for cust in customers:
        score = 0
        hit_fields: list[str] = []
        card_qualities: dict[str, str] = {}

        for customer_ref in extracted.get("customer_refs", []):
            if _clean(customer_ref) == _clean(cust.customer_ref or ""):
                score += 3
                hit_fields.append("customer_ref")

        for acc in extracted.get("connection_ids", []):
            if _match_account(acc, cust.connection_ids or []):
                score += 3
                hit_fields.append("connection_id")

        for card in extracted.get("device_serials", []):
            quality = _card_match_quality(card, cust.device_serials or [])
            if quality == "full":
                score += 2
                hit_fields.append("card_number")
                card_qualities[card] = "full"
            elif quality == "last4":
                score += 1          # half-credit for last-4 only
                hit_fields.append("card_number")
                card_qualities[card] = "last4"

        for upi in extracted.get("payment_handles", []):
            if upi.lower() in [u.lower() for u in (cust.payment_handles or [])]:
                score += 2
                hit_fields.append("upi_id")

        for mob in extracted.get("mobile_numbers", []):
            if _clean(mob) == _clean(cust.mobile_number or ""):
                score += 2
                hit_fields.append("mobile_number")

        if score > best_score:
            best_score = score
            best_customer = cust
            best_fields = list(set(hit_fields))
            best_card_qualities = card_qualities

    matched_customer = best_customer if (best_customer and best_score >= 2) else None
    matched_fields = best_fields if matched_customer else []

    # Determine unmatched extracted field types
    all_found = any(v for v in extracted.values())
    unmatched_fields: list[str] = []
    if not matched_customer and all_found:
        unmatched_fields = [k for k, v in extracted.items() if v]
    elif matched_customer:
        for field, values in extracted.items():
            if values and field.rstrip("s") not in " ".join(matched_fields):
                unmatched_fields.append(field)

    validation_status = (
        "full"    if matched_customer and len(matched_fields) >= 2
        else "partial" if matched_customer
        else "none"
    )

    # ── Cross-customer conflict ───────────────────────────────────────────────
    cross_conflict = _detect_cross_customer_conflict(extracted, customers)

    # ── Impersonation risk ────────────────────────────────────────────────────
    impersonation_risk = _assess_impersonation_risk(
        matched_customer=matched_customer,
        extracted=extracted,
        sender_email=sender_email,
        sender_name=sender_name,
        claimed_names=claimed_names,
        cross_conflict=cross_conflict,
        match_score=best_score,
        matched_fields=matched_fields,
        card_match_qualities=best_card_qualities,
    )

    customer_record: dict[str, Any] | None = None
    if matched_customer:
        customer_record = {
            "customer_ref":          matched_customer.customer_ref,
            "name":                matched_customer.name,
            "mobile_number":       matched_customer.mobile_number,
            "email":               getattr(matched_customer, "email", None),
            "tax_id":          getattr(matched_customer, "tax_id", None),
            "id_last4":      getattr(matched_customer, "id_last4", None),
            "date_of_birth":       getattr(matched_customer, "date_of_birth", None),
            "service_area":              matched_customer.service_area,
            "area_code":         matched_customer.area_code,
            "status":              matched_customer.status,
            "verification_status":          matched_customer.verification_status,
            "customer_segment":    getattr(matched_customer, "customer_segment", "RETAIL"),
            "occupation":          getattr(matched_customer, "occupation", None),
            "annual_income":       getattr(matched_customer, "annual_income", None),
            "payment_score":        getattr(matched_customer, "payment_score", None),
            "address":             getattr(matched_customer, "address", {}),
            "connection_ids":     matched_customer.connection_ids or [],
            "device_serials":        matched_customer.device_serials or [],
            "payment_handles":             matched_customer.payment_handles or [],
            "connection_details":     getattr(matched_customer, "connection_details", []) or [],
            "device_details":        getattr(matched_customer, "device_details", []) or [],
            "contracts":       getattr(matched_customer, "contracts", []) or [],
            "addon_services":    getattr(matched_customer, "addon_services", []) or [],
            "static_ip_blocks":      getattr(matched_customer, "static_ip_blocks", []) or [],
            "premises_equipment":      getattr(matched_customer, "premises_equipment", []) or [],
            "protection_plans":  getattr(matched_customer, "protection_plans", []) or [],
            "roaming_profile":         getattr(matched_customer, "roaming_profile", None),
            "self_care_portal":    getattr(matched_customer, "self_care_portal", "ACTIVE"),
            "mobile_app_access":      getattr(matched_customer, "mobile_app_access", "ACTIVE"),
            "account_manager": getattr(matched_customer, "account_manager", None),
            "alternate_contact":             getattr(matched_customer, "alternate_contact", None),
            "product_types":       matched_customer.product_types or [],
        }

    return {
        "extracted":           extracted,
        "validation_status":   validation_status,
        "matched_fields":      matched_fields,
        "unmatched_fields":    unmatched_fields,
        "matched_customer":    customer_record,
        "match_score":         best_score,
        "claimed_names":       claimed_names,
        "cross_conflict":      cross_conflict,
        "impersonation_risk":  impersonation_risk,
    }


# ── Public entry points ───────────────────────────────────────────────────────

def run_customer_validation(
    text: str,
    db: Session,
    *,
    sender_email: str | None = None,
    sender_name: str | None = None,
) -> dict[str, Any]:
    """
    Full customer validation pipeline:
      1. Extract identifiers and claimed names from email text
      2. Match against customer DB
      3. Detect cross-customer conflicts
      4. Assess impersonation risk using sender email + claimed name + identifier consistency
    """
    extracted = extract_identifiers(text)
    claimed_names = extract_claimed_names(text)
    has_any = any(v for v in extracted.values())

    if not has_any:
        # No numeric identifiers found; still try name-only match if sender_name is provided
        # but mark it as name_match validation only
        return {
            "extracted": extracted,
            "validation_status": "no_identifiers",
            "matched_fields": [],
            "unmatched_fields": [],
            "matched_customer": None,
            "match_score": 0,
            "claimed_names": claimed_names,
            "cross_conflict": {
                "has_conflict": False,
                "has_strong_conflict": False,
                "conflicting_customer_count": 0,
                "customer_ref_to_identifiers": {},
                "identifier_to_customer_ref": {},
            },
            "impersonation_risk": {
                "level": "UNKNOWN",
                "score": 0,
                "signals": ["No identifiers found in email — identity cannot be verified"],
                "recommendation": "Request connection id or customer reference from the sender",
            },
        }

    return validate_against_db(
        extracted,
        db,
        sender_email=sender_email,
        sender_name=sender_name,
        claimed_names=claimed_names,
    )

"""
Smart Intake — pre-flight processing layer that runs before AI classification.

Handles three edge cases that the standard pipeline cannot:

  1. EMAIL THREADS  — multi-turn conversation chains.
     Extracts only the latest/top message so AI classifies the *current* issue,
     not the whole history. Thread context is preserved for display.

  2. NON-CUSTOMER EMAILS — competitor provider or completely off-topic content.
     Detected via keyword scoring before any ML inference runs.
     Returns a structured rejection notice instead of a classification.

  3. SPARSE EMAILS  — insufficient information (< SPARSE_WORD_THRESHOLD words
     of meaningful content, no clear issue stated).
     Falls back to:  customer DB match → case history pull →
     BERT historical search → ranked solution suggestions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session


# ── Constants ─────────────────────────────────────────────────────────────────

SPARSE_WORD_THRESHOLD = 25   # fewer meaningful words → sparse path
EMPTY_WORD_THRESHOLD  = 8    # fewer than this → basically empty

# Competitor and off-topic keyword sets.
#
# These are fictional names. A deployment replaces them with the providers it
# actually competes with, and with the organisations its customers most often
# write to by mistake. Nothing else in the module hardcodes a name.
_COMPETITOR_PROVIDERS: list[tuple[str, str]] = [
    # (regex_pattern, display_name)
    (r"\bmeridian\s*(?:fibre|telecom)?\b", "Meridian Fibre"),
    (r"\bcoastline\s*(?:net|broadband)?\b", "Coastline Broadband"),
    (r"\bpinewood\s*(?:link|telecom)?\b",   "Pinewood Link"),
    (r"\bharbour\s*net\b",                 "HarbourNet"),
    (r"\bvertex\s*(?:mobile|telecom)\b",    "Vertex Mobile"),
    (r"\bkestrel\s*(?:connect|fibre)\b",    "Kestrel Connect"),
    (r"\bnorthwind\s*(?:cable|broadband)\b","Northwind Cable"),
    (r"\bsolstice\s*(?:wireless|mobile)\b", "Solstice Wireless"),
]

_OFF_TOPIC_PATTERNS: list[tuple[str, str]] = [
    (r"\bpassport\b",                  "Passport office"),
    (r"\bincome\s*tax\b",             "Tax authority"),
    (r"\bgst\b",                       "Tax authority"),
    (r"\btrain\s+ticket\b|\brailway\b", "Rail operator"),
    (r"\belectricity\s+bill\b|\bpower\s+supply\b", "Electricity provider"),
    (r"\bwater\s+(?:bill|supply)\b",   "Water utility"),
    (r"\binsurance\s+(?:policy|claim)\b", "Insurer"),
    (r"\bparcel\b|\bcourier\b",        "Courier"),
]

# Signals that the sender is writing to us rather than about someone else.
# Presence of these reduces the external-provider score.
_OWN_SIGNALS = [
    r"\bmy\s+connection\b", r"\bself\s*care\b", r"\byour\s+engineer\b",
    r"\bmy\s+plan\b", r"\bex-\d{3}\b",  # exchange code
    r"\bcr\d{6}\b",                      # customer reference
]

# Thread-break markers — everything below these lines is quoted history
_THREAD_BREAK_PATTERNS = [
    re.compile(r"^-{3,}\s*(original|forwarded|previous)\s+(message|mail|email)\s*-{3,}", re.I | re.M),
    re.compile(r"^={3,}\s*(original|forwarded)\s+(message|mail|email)\s*={3,}", re.I | re.M),
    re.compile(r"^On\s+.{10,80}\s+wrote:\s*$", re.I | re.M),
    re.compile(r"^From:\s+.+\nSent:\s+", re.I | re.M),
    re.compile(r"^\s*>{2,}", re.M),           # ≥2 quote chars indicate deep nesting
    re.compile(r"^_{5,}", re.M),              # Outlook horizontal separator
    re.compile(r"\[cid:", re.I),              # Embedded image marker (Outlook)
]

_STOPWORDS = frozenset({
    "dear", "sir", "madam", "hello", "hi", "please", "help", "thanks",
    "regards", "sincerely", "the", "and", "for", "from", "that", "this",
    "with", "have", "has", "was", "were", "your", "our", "provider",
    "team", "email", "subject", "account", "customer", "service",
    "kindly", "requesting", "write", "writing", "reach", "out",
})


# ── Dataclass result types ─────────────────────────────────────────────────────

@dataclass
class ThreadInfo:
    is_thread: bool
    depth: int
    top_message: str           # extracted latest message
    full_text: str             # original full text
    turns: list[dict[str, Any]] = field(default_factory=list)  # parsed turns


@dataclass
class BankDetectionResult:
    is_external_provider: bool
    is_off_topic: bool
    competitor_provider: str | None
    off_topic_domain: str | None
    own_signal_count: int
    external_signal_count: int
    confidence: float          # 0–1, how sure we are it's external provider
    reason: str


@dataclass
class DensityResult:
    density: str               # "sufficient" | "sparse" | "empty"
    word_count: int
    meaningful_word_count: int
    identifier_count: int
    extracted_issue: str | None  # brief best-effort issue extraction


@dataclass
class SmartIntakeResult:
    """Unified result from the pre-flight layer."""
    # What text should the AI pipeline actually process?
    effective_text: str
    original_text: str

    # Thread
    thread: ThreadInfo

    # Provider detection
    provider: BankDetectionResult

    # Information density
    density: DensityResult

    # Customer match + historical solutions (populated for sparse emails)
    customer_match: dict[str, Any] | None = None
    historical_solutions: list[dict[str, Any]] = field(default_factory=list)

    # Overall intake verdict
    verdict: str = "proceed"  # "proceed" | "external_bank" | "off_topic" | "sparse" | "empty"
    redirect_response: dict[str, str] | None = None  # subject + body for external provider


# ── Thread extraction ──────────────────────────────────────────────────────────

def extract_thread(text: str) -> ThreadInfo:
    """
    Split a possibly-threaded email into turns and extract the top message.

    Returns a ThreadInfo with:
    - top_message: only the latest message text
    - turns: list of {role, text} dicts (latest first)
    - depth: number of turns detected
    """
    # Count From: headers to estimate thread depth
    from_count = len(re.findall(r"^From:\s+\S+", text, re.M | re.I))
    quote_lines = len(re.findall(r"^>+", text, re.M))
    is_thread = from_count >= 2 or quote_lines >= 3

    if not is_thread:
        return ThreadInfo(
            is_thread=False,
            depth=1,
            top_message=text,
            full_text=text,
            turns=[{"role": "latest", "text": text}],
        )

    # Find the earliest thread-break position
    cut_pos = len(text)
    for pat in _THREAD_BREAK_PATTERNS:
        m = pat.search(text)
        if m and m.start() < cut_pos:
            cut_pos = m.start()

    top_message = text[:cut_pos].strip()
    historical_chain = text[cut_pos:].strip()

    # Parse historical turns from quoted chain (best-effort)
    turns = _parse_turns(top_message, historical_chain)

    return ThreadInfo(
        is_thread=True,
        depth=max(from_count, len(turns), 2),
        top_message=top_message or text,  # fallback to full text if extraction fails
        full_text=text,
        turns=turns,
    )


def _parse_turns(top: str, chain: str) -> list[dict[str, Any]]:
    """Best-effort turn parser — gives back list of {role, text} dicts."""
    turns: list[dict[str, Any]] = []
    if top:
        turns.append({"role": "latest", "text": top[:500]})

    # Split chain by From:/On ... wrote: markers
    splitter = re.compile(
        r"(?:^On\s+.{10,80}\s+wrote:\s*$|^From:\s+\S+)",
        re.I | re.M,
    )
    parts = splitter.split(chain)
    for i, part in enumerate(parts):
        part = part.strip()
        if len(part) > 20:
            # Strip leading quote chars
            cleaned = re.sub(r"^>+\s?", "", part, flags=re.M).strip()
            turns.append({"role": f"turn_{i+1}", "text": cleaned[:400]})

    return turns[:6]  # cap at 6 turns


# ── Provider / domain detection ───────────────────────────────────────────────────

def detect_bank(text: str) -> BankDetectionResult:
    """
    Score the text for external provider and off-topic domain signals.

    A high external_signal_count with low own_signal_count → flag for rejection.
    """
    lower = text.lower()

    own_hits = sum(1 for pat in _OWN_SIGNALS if re.search(pat, lower))
    external_hits = 0
    competitor_provider: str | None = None
    off_topic_domain: str | None = None

    for pattern, name in _COMPETITOR_PROVIDERS:
        if re.search(pattern, lower):
            external_hits += 2  # competitor provider hit is strong signal
            if competitor_provider is None:
                competitor_provider = name

    for pattern, name in _OFF_TOPIC_PATTERNS:
        if re.search(pattern, lower):
            external_hits += 1
            if off_topic_domain is None:
                off_topic_domain = name

    is_off_topic = off_topic_domain is not None and competitor_provider is None and own_hits == 0

    # Compute confidence: how certain are we this is NOT Docket?
    if external_hits == 0:
        confidence = 0.0
    elif own_hits == 0:
        # Any competitor provider mention with no Docket signals → high confidence
        confidence = min(0.40 + external_hits * 0.20, 0.98)
    else:
        # Docket signals dampen the confidence
        confidence = max(0.0, (external_hits - own_hits * 2) / max(external_hits, 1)) * 0.85

    is_external_provider = confidence >= 0.55 and competitor_provider is not None

    reason = ""
    if is_external_provider:
        reason = f"Email references {competitor_provider} ({external_hits} competitor signals, {own_hits} Docket signals)"
    elif is_off_topic:
        reason = f"Email appears unrelated to services — domain: {off_topic_domain}"

    return BankDetectionResult(
        is_external_provider=is_external_provider,
        is_off_topic=is_off_topic,
        competitor_provider=competitor_provider,
        off_topic_domain=off_topic_domain,
        own_signal_count=own_hits,
        external_signal_count=external_hits,
        confidence=round(confidence, 3),
        reason=reason,
    )


# ── Information density ───────────────────────────────────────────────────────

def _meaningful_words(text: str) -> list[str]:
    tokens = re.findall(r"[a-z]{3,}", text.lower())
    return [t for t in tokens if t not in _STOPWORDS]


def _count_identifiers(text: str) -> int:
    """Count numeric identifiers (account nos, amounts, mobile numbers)."""
    count = 0
    count += len(re.findall(r"\b\d{9,18}\b", text))          # account nos
    count += len(re.findall(r"(?:rs\.?|inr|₹)\s*\d", text, re.I))  # amounts
    count += len(re.findall(r"(?<!\d)[6-9]\d{9}(?!\d)", text))  # mobile
    count += len(re.findall(r"[\w.]+@(?:okbank|ybl|okaxis|upi|paytm|gpay)", text, re.I))  # UPI
    return count


def _extract_issue_hint(text: str) -> str | None:
    """Best-effort extraction of the core issue in very short emails."""
    # Look for issue-signaling phrases
    patterns = [
        r"(?:problem|issue|complaint|error|unable|cannot|failed|blocked|not working|stuck)\s+(?:with|in|on|about|regarding)?\s*([\w\s]{3,40})",
        r"(?:regarding|about|related to|for)\s+([\w\s]{3,40})",
        r"(?:please\s+help|need\s+help|need\s+assistance)\s+(?:with|in|on|about)?\s*([\w\s]{3,30})",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1).strip().title()
    return None


def assess_density(text: str) -> DensityResult:
    words = _meaningful_words(text)
    identifiers = _count_identifiers(text)
    wcount = len(words)

    if wcount < EMPTY_WORD_THRESHOLD and identifiers == 0:
        density = "empty"
    elif wcount < SPARSE_WORD_THRESHOLD and identifiers < 2:
        density = "sparse"
    else:
        density = "sufficient"

    return DensityResult(
        density=density,
        word_count=len(re.findall(r"\S+", text)),
        meaningful_word_count=wcount,
        identifier_count=identifiers,
        extracted_issue=_extract_issue_hint(text),
    )


# ── Sparse-email: customer match + historical suggestions ─────────────────────

def _customer_match_for_sparse(text: str, sender_email: str | None, db: Session, sender_name: str | None = None) -> dict[str, Any] | None:
    """
    Try to identify the customer even for sparse emails.
    Uses customer_validation on whatever identifiers exist, plus
    fuzzy name matching against sender email domain parts.
    """
    from .customer_validation import run_customer_validation
    result = run_customer_validation(text, db, sender_email=sender_email, sender_name=sender_name)
    if result.get("validation_status") not in ("none", "no_identifiers"):
        return result

    # Second attempt: use sender email local part as name hint
    # e.g. "rajesh.sharma@example.com" → parts {"rajesh", "sharma"}
    # Match if ≥2 parts overlap with customer name words, or first name matches
    if sender_email:
        local = sender_email.split("@")[0].replace(".", " ").replace("_", " ")
        hint_parts = {p for p in local.lower().split() if len(p) > 2}
        from ..models import Subscriber
        customers = db.query(Subscriber).all()
        for cust in customers:
            cust_parts = set(cust.name.lower().split())
            overlap = hint_parts & cust_parts
            # Match if ≥2 name parts overlap, or first name alone matches
            first_name_match = bool(hint_parts and cust_parts and next(iter(hint_parts)) in cust_parts)
            if len(overlap) >= 2 or (len(overlap) >= 1 and first_name_match):
                return {
                    "extracted": {},
                    "validation_status": "name_match",
                    "matched_fields": ["name"],
                    "unmatched_fields": [],
                    "matched_customer": {
                        "customer_ref": cust.customer_ref,
                        "name": cust.name,
                        "mobile_number": cust.mobile_number,
                        "service_area": cust.service_area,
                        "connection_ids": cust.connection_ids or [],
                        "product_types": cust.product_types or [],
                        "verification_status": cust.verification_status,
                        "status": cust.status,
                    },
                    "match_score": 1,
                }
    return None


def _historical_solutions_for_sparse(
    text: str,
    customer_match: dict[str, Any] | None,
    db: Session,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """
    For sparse emails: find the best historical solutions.

    Strategy:
    1. If customer matched — pull their recent resolved cases first.
    2. Enrich query text with any extracted issue hint + customer product types.
    3. BERT semantic search against all resolved cases.
    4. Return ranked results with solution summaries.
    """
    from .historical import similarity_search
    from ..models import Case

    enhanced_query = text

    # If customer is known, enrich the query with their product types
    if customer_match and customer_match.get("matched_customer"):
        cust = customer_match["matched_customer"]
        products = " ".join(cust.get("product_types") or [])
        service_area = cust.get("service_area") or ""
        enhanced_query = f"{text} {products} {service_area}".strip()

    # Semantic search
    try:
        results = similarity_search(db, enhanced_query, limit=limit)
    except Exception:
        results = []

    # If customer is matched, also pull their own case history from the DB
    own_cases: list[dict[str, Any]] = []
    if customer_match and customer_match.get("matched_customer"):
        cust_name = customer_match["matched_customer"].get("name", "")
        if cust_name:
            own_resolved = (
                db.query(Case)
                .filter(
                    Case.workflow_state.in_(["RESOLVED", "CLOSED"]),
                    Case.normalized_text.ilike(f"%{cust_name.split()[0]}%"),
                )
                .order_by(Case.updated_at.desc())
                .limit(3)
                .all()
            )
            for c in own_resolved:
                if c.resolution_text:
                    own_cases.append({
                        "case_id": c.case_id,
                        "classification": c.classification,
                        "similarity": 0.95,  # high confidence for own cases
                        "similarity_method": "customer_history",
                        "resolution_text": c.resolution_text,
                        "summary": (c.ai_analysis or {}).get("routing_explanation", ""),
                        "source": "own_history",
                    })

    # Merge: own cases first, then semantic results (deduplicated)
    seen_ids: set[str] = {c["case_id"] for c in own_cases}
    for r in results:
        if r.get("case_id") not in seen_ids:
            r["source"] = "semantic_search"
            own_cases.append(r)
            seen_ids.add(r["case_id"])

    return own_cases[:limit]


# ── Redirect response builder ─────────────────────────────────────────────────

def _build_redirect_response(bank_result: BankDetectionResult, sender_name: str | None) -> dict[str, str]:
    """Generate a polite redirect message for external provider or off-topic emails."""
    name = sender_name or "Valued Customer"

    if bank_result.is_external_provider and bank_result.competitor_provider:
        provider = bank_result.competitor_provider
        subject = f"Re: Your Query — Please Contact {provider} Directly"
        body = (
            f"Dear {name},\n\n"
            f"Thank you for writing to us. After reviewing your email, we noticed that your query "
            f"appears to be related to {provider}, and not us.\n\n"
            f"We are unable to access or assist with accounts or services held with other financial "
            f"institutions. Please contact {provider} directly for assistance.\n\n"
            f"If you do hold an Docket account and have been directed here in error, please resend "
            f"your query mentioning your Docket connection id or customer reference so we can assist you promptly.\n\n"
            f"Regards,\nDocket Customer Care"
        )

    elif bank_result.is_off_topic and bank_result.off_topic_domain:
        domain = bank_result.off_topic_domain
        subject = "Re: Your Query — Outside Our Service Scope"
        body = (
            f"Dear {name},\n\n"
            f"Thank you for contacting Docket. Your query appears to be related "
            f"to {domain}, which falls outside our services.\n\n"
            f"For Docket-related queries (accounts, contracts, cards, self care, UPI), "
            f"please resend your query with your Docket account details and we will be happy to help.\n\n"
            f"For {domain} queries, please contact the relevant service provider directly.\n\n"
            f"Regards,\nDocket Customer Care"
        )

    else:
        subject = "Re: Your Query — Please Provide More Details"
        body = (
            f"Dear {name},\n\n"
            f"Thank you for writing to us. We were unable to identify a specific Docket service "
            f"query in your email.\n\n"
            f"To assist you effectively, please reply with:\n"
            f"1. Your Docket connection id or customer reference\n"
            f"2. A brief description of your issue\n"
            f"3. Any relevant transaction reference numbers\n\n"
            f"Regards,\nDocket Customer Care"
        )

    return {"subject": subject, "body": body}


# ── Main entry point ──────────────────────────────────────────────────────────

def run_intake_triage(
    text: str,
    *,
    sender_email: str | None = None,
    sender_name: str | None = None,
    db: Session | None = None,
) -> SmartIntakeResult:
    """
    Run all three pre-flight checks and return a unified SmartIntakeResult.

    Args:
        text:         Normalized email text (full, including any quoted history)
        sender_email: Extracted sender email address (for name-hint matching)
        sender_name:  Extracted sender name from headers
        db:           SQLAlchemy session (required for sparse path and customer match)

    Returns:
        SmartIntakeResult with verdict and enriched context.
    """
    # 1. Thread extraction — always run
    thread = extract_thread(text)

    # If the extracted top message is too sparse, fall back to using the full text
    # for both density assessment and AI processing (better than losing context)
    top_density_check = assess_density(thread.top_message)
    if thread.is_thread and top_density_check.density in ("sparse", "empty"):
        # Keep thread metadata but use full text so AI can classify properly
        effective_text = text
    else:
        effective_text = thread.top_message  # AI will process top message only

    # 2. Provider / domain detection — run on effective text + a bit of full context
    provider = detect_bank(effective_text + " " + text[:500])

    # 3. Information density — assess the effective text
    density = assess_density(effective_text)

    # ── Determine verdict ──────────────────────────────────────────────────────

    if provider.is_external_provider:
        verdict = "external_bank"
        redirect = _build_redirect_response(provider, sender_name)
        return SmartIntakeResult(
            effective_text=effective_text,
            original_text=text,
            thread=thread,
            provider=provider,
            density=density,
            verdict=verdict,
            redirect_response=redirect,
        )

    if provider.is_off_topic:
        verdict = "off_topic"
        redirect = _build_redirect_response(provider, sender_name)
        return SmartIntakeResult(
            effective_text=effective_text,
            original_text=text,
            thread=thread,
            provider=provider,
            density=density,
            verdict=verdict,
            redirect_response=redirect,
        )

    if density.density in ("sparse", "empty"):
        verdict = density.density
        customer_match = None
        historical_solutions: list[dict[str, Any]] = []

        if db is not None:
            customer_match = _customer_match_for_sparse(effective_text, sender_email, db, sender_name=sender_name)
            historical_solutions = _historical_solutions_for_sparse(
                effective_text, customer_match, db
            )

        return SmartIntakeResult(
            effective_text=effective_text,
            original_text=text,
            thread=thread,
            provider=provider,
            density=density,
            customer_match=customer_match,
            historical_solutions=historical_solutions,
            verdict=verdict,
            redirect_response=_build_redirect_response(provider, sender_name)
            if density.density == "empty"
            else None,
        )

    # All checks passed — proceed with standard AI pipeline
    return SmartIntakeResult(
        effective_text=effective_text,
        original_text=text,
        thread=thread,
        provider=provider,
        density=density,
        verdict="proceed",
    )

"""
Historical reference search — two-tier approach:

  Tier 1 (BERT semantic)  — cosine similarity on stored 384-dim embeddings.
                             Finds "UPI amount stuck" ↔ "failed transfer debited"
                             even with zero shared keywords.
  Tier 2 (token Jaccard)  — legacy fallback for older records without embeddings.

index_case() stores both tokens and embedding so both tiers work.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models import Case, HistoricalReference


STOPWORDS = {
    "the", "and", "for", "from", "that", "this", "with", "have", "has", "was", "were",
    "account", "connection", "please", "dear", "team", "email", "subject",
}


def tokens_for(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]{3,}", text.lower())
    return sorted({token for token in tokens if token not in STOPWORDS})


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

def index_case(
    db: Session,
    case: Case,
    embedding: list[float] | None = None,
) -> None:
    """
    Index a case for historical search.

    *embedding* — 384-dim BERT vector produced by bert_analysis.embed().
    If None, semantic search falls back to token Jaccard for this record.
    """
    tokens = tokens_for(case.normalized_text)
    existing = (
        db.query(HistoricalReference)
        .filter(HistoricalReference.case_id == case.case_id)
        .one_or_none()
    )
    if existing:
        existing.normalized_text = case.normalized_text
        existing.tokens = tokens
        existing.classification = case.classification
        existing.primary_department = case.primary_department
        existing.resolution_summary = case.resolution_text
        if embedding is not None:
            existing.embedding = embedding
        flag_modified(existing, "tokens")
        if embedding is not None:
            flag_modified(existing, "embedding")
    else:
        db.add(
            HistoricalReference(
                case_id=case.case_id,
                normalized_text=case.normalized_text,
                tokens=tokens,
                classification=case.classification,
                primary_department=case.primary_department,
                embedding=embedding,
                resolution_summary=case.resolution_text,
            )
        )


# ---------------------------------------------------------------------------
# Tier-1: BERT semantic search
# ---------------------------------------------------------------------------

def _bert_similarity_search(
    db: Session,
    query_embedding: list[float],
    limit: int = 10,
    min_score: float = 0.30,
) -> list[dict[str, Any]]:
    """Cosine similarity search over stored BERT embeddings."""
    from .bert_analysis import cosine_sim  # lazy to avoid import at module load

    matches = []
    for ref in db.query(HistoricalReference).filter(HistoricalReference.embedding.isnot(None)).all():
        if not ref.embedding:
            continue
        score = cosine_sim(query_embedding, ref.embedding)
        if score >= min_score:
            matches.append(
                {
                    "case_id": ref.case_id,
                    "similarity": round(score, 4),
                    "similarity_method": "bert_semantic",
                    "classification": ref.classification,
                    "primary_department": ref.primary_department,
                    "resolution_recommendation": (
                        ref.resolution_summary
                        or "Review similar historical case and AI explanation before action."
                    ),
                }
            )
    return sorted(matches, key=lambda item: item["similarity"], reverse=True)[:limit]


# ---------------------------------------------------------------------------
# Tier-2: Token Jaccard fallback
# ---------------------------------------------------------------------------

def _token_similarity_search(
    db: Session,
    query: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    query_tokens = set(tokens_for(query))
    if not query_tokens:
        return []
    matches = []
    for ref in db.query(HistoricalReference).all():
        ref_tokens = set(ref.tokens or [])
        if not ref_tokens:
            continue
        score = len(query_tokens & ref_tokens) / max(1, len(query_tokens | ref_tokens))
        if score > 0:
            matches.append(
                {
                    "case_id": ref.case_id,
                    "similarity": round(score, 3),
                    "similarity_method": "token_jaccard",
                    "classification": ref.classification,
                    "primary_department": ref.primary_department,
                    "resolution_recommendation": (
                        ref.resolution_summary
                        or "Review similar historical case and AI explanation before action."
                    ),
                }
            )
    return sorted(matches, key=lambda item: item["similarity"], reverse=True)[:limit]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def similarity_search(
    db: Session,
    query: str,
    limit: int = 10,
    *,
    allow_model_load: bool = True,
) -> list[dict[str, Any]]:
    """
    Search historical cases for *query*.

    Attempts BERT semantic search first.  If no records have stored embeddings
    (e.g. freshly migrated database), falls back to token Jaccard automatically.
    When allow_model_load=False, BERT is used only if it is already warm so
    non-critical UI surfaces do not block on first model load.
    """
    try:
        from .bert_analysis import embed, runtime_status
        if not allow_model_load and not runtime_status().get("embedding_model_loaded"):
            raise RuntimeError("BERT embedder is not warm.")
        query_embedding = embed(query)
        results = _bert_similarity_search(db, query_embedding, limit=limit)
        if results:
            return results
        # No embedding-indexed records yet — fall through to token search
    except Exception:
        pass  # sentence-transformers not installed or first-load error

    return _token_similarity_search(db, query, limit=limit)


def duplicate_hints(db: Session, normalized_text: str) -> list[dict[str, Any]]:
    """Return candidate duplicates (similarity ≥ 0.35 for BERT, ≥ 0.42 for tokens)."""
    try:
        from .bert_analysis import embed
        query_embedding = embed(normalized_text)
        results = _bert_similarity_search(db, query_embedding, limit=5, min_score=0.35)
        if results:
            return results
    except Exception:
        pass

    return [
        item
        for item in _token_similarity_search(db, normalized_text, limit=5)
        if item["similarity"] >= 0.42
    ]


def find_customer_followups(
    db: Session,
    sender_email_masked: str | None,
    query_embedding: list[float] | None,
    exclude_hash: str | None,
    *,
    min_score: float = 0.42,
) -> list[dict[str, Any]]:
    """
    Catch genuine follow-ups from the SAME customer even when the subject line
    changed heavily.

    Subject-based duplicate detection misses a customer who rewords their subject.
    This matches on the masked sender email instead, then uses a lower BERT
    similarity bar (same customer raises the prior probability that it's related)
    against their still-open cases.

    Returns matches sorted by similarity desc.
    """
    if not sender_email_masked:
        return []

    # All of this customer's still-open cases (exclude the email we're processing)
    candidates = (
        db.query(Case)
        .filter(
            Case.workflow_state.notin_(["CLOSED"]),
            Case.is_parent.is_(False),
        )
        .all()
    )

    same_customer = [
        c for c in candidates
        if (c.customer_metadata or {}).get("sender_email_masked") == sender_email_masked
        and c.raw_email_hash != exclude_hash
    ]
    if not same_customer:
        return []

    matches: list[dict[str, Any]] = []
    cosine_sim = None
    if query_embedding:
        try:
            from .bert_analysis import cosine_sim as _cs
            cosine_sim = _cs
        except Exception:
            cosine_sim = None

    for c in same_customer:
        score = None
        if cosine_sim is not None:
            ref = (
                db.query(HistoricalReference)
                .filter(HistoricalReference.case_id == c.case_id)
                .one_or_none()
            )
            if ref and ref.embedding:
                score = cosine_sim(query_embedding, ref.embedding)
        # Same customer with an open case is itself a signal; if we have a
        # similarity score, require it to clear the (lower) bar. If embeddings
        # are missing, still surface the open case as a possible follow-up.
        if score is None or score >= min_score:
            matches.append({
                "case_id": c.case_id,
                "similarity": round(score, 4) if score is not None else None,
                "similarity_method": "same_customer_semantic" if score is not None else "same_customer_open",
                "classification": c.classification,
                "primary_department": c.primary_department,
                "workflow_state": c.workflow_state,
            })

    return sorted(matches, key=lambda m: m["similarity"] or 0.0, reverse=True)


# Threshold above which the same customer is considered to be re-submitting the
# same complaint rather than raising a new, distinct issue.
SAME_COMPLAINT_THRESHOLD = 0.72


def detect_customer_repeat(
    db: Session,
    normalized_text: str,
    *,
    customer_ref: str | None = None,
    sender_email_masked: str | None = None,
    exclude_hash: str | None = None,
    lookback_days: int = 90,
) -> dict[str, Any]:
    """
    Determine whether this email is:

    "exact_repeat"   — the same customer is re-submitting a complaint they already
                       raised (similarity ≥ SAME_COMPLAINT_THRESHOLD). The prior case
                       may be open (impatient re-send) or recently resolved (they
                       weren't happy with the resolution).

    "new_issue"      — the same customer is writing about something new and different.
                       They should be identified automatically without having to
                       re-provide their details, and their prior case history should
                       be surfaced to the operator.

    "first_contact"  — no prior case found for this customer.

    Uses customer reference (from customer_validation) when available; falls back to the
    masked sender email so the detection still works even without a DB match.

    Returns:
        repeat_type:        "exact_repeat" | "new_issue" | "first_contact"
        prior_cases:        list of matching prior case summaries
        top_similarity:     float | None  — highest similarity score found
        open_count:         int   — number of still-open cases for this customer
        resolved_count:     int   — closed/resolved cases found in lookback window
        same_complaint_case: str | None  — case_id of the near-identical prior case
    """
    from datetime import datetime, timezone, timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    # ── Find all cases from this customer ──────────────────────────────────────
    all_cases = (
        db.query(Case)
        .filter(
            Case.is_parent.is_(False),
            Case.created_at >= cutoff,
        )
        .all()
    )

    # Match by customer reference (stored in intake_flags.sparse_customer_match or extracted_fields)
    # OR by masked sender email (reliable when email is consistent)
    def _belongs_to_customer(c: Case) -> bool:
        if c.raw_email_hash == exclude_hash:
            return False
        meta = c.customer_metadata or {}
        if sender_email_masked and meta.get("sender_email_masked") == sender_email_masked:
            return True
        if customer_ref:
            # Check extracted_fields for customer_ref match
            ef = c.extracted_fields or {}
            if ef.get("customer_ref") == customer_ref:
                return True
            # Check intake_flags customer match
            flags = c.intake_flags or {}
            sparse = flags.get("sparse_customer_match") or {}
            sc = sparse.get("matched_customer") or {}
            if sc.get("customer_ref") == customer_ref:
                return True
        return False

    customer_cases = [c for c in all_cases if _belongs_to_customer(c)]
    if not customer_cases:
        return {
            "repeat_type": "first_contact",
            "prior_cases": [],
            "top_similarity": None,
            "open_count": 0,
            "resolved_count": 0,
            "same_complaint_case": None,
        }

    # ── Score each prior case by semantic similarity ───────────────────────────
    cosine_sim_fn = None
    query_embedding: list[float] | None = None
    try:
        from .bert_analysis import embed, cosine_sim as _cs
        query_embedding = embed(normalized_text)
        cosine_sim_fn = _cs
    except Exception:
        pass

    scored: list[dict[str, Any]] = []
    for c in customer_cases:
        sim: float | None = None
        if cosine_sim_fn is not None and query_embedding is not None:
            ref = (
                db.query(HistoricalReference)
                .filter(HistoricalReference.case_id == c.case_id)
                .one_or_none()
            )
            if ref and ref.embedding:
                sim = round(cosine_sim_fn(query_embedding, ref.embedding), 4)
        if sim is None:
            # Token Jaccard fallback
            q_tok = set(tokens_for(normalized_text))
            c_tok = set(tokens_for(c.normalized_text or ""))
            union = q_tok | c_tok
            sim = round(len(q_tok & c_tok) / max(1, len(union)), 4) if union else 0.0

        scored.append({
            "case_id": c.case_id,
            "similarity": sim,
            "classification": c.classification,
            "primary_department": c.primary_department,
            "workflow_state": c.workflow_state,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "resolution_text": c.resolution_text,
        })

    scored.sort(key=lambda x: x["similarity"] or 0.0, reverse=True)

    top_sim = scored[0]["similarity"] if scored else None
    open_count = sum(1 for c in customer_cases if c.workflow_state not in ("CLOSED", "RESOLVED"))
    resolved_count = sum(1 for c in customer_cases if c.workflow_state in ("CLOSED", "RESOLVED"))

    # Classify the repeat type
    same_complaint_case: str | None = None
    if top_sim is not None and top_sim >= SAME_COMPLAINT_THRESHOLD:
        repeat_type = "exact_repeat"
        same_complaint_case = scored[0]["case_id"]
    else:
        repeat_type = "new_issue"

    return {
        "repeat_type": repeat_type,
        "prior_cases": scored[:5],  # top 5 for display
        "top_similarity": top_sim,
        "open_count": open_count,
        "resolved_count": resolved_count,
        "same_complaint_case": same_complaint_case,
    }

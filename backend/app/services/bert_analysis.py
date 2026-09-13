"""
BERT analysis layer — runs alongside Gemma for maximum accuracy.

Models used (downloaded on first call, cached by HuggingFace):
  • all-MiniLM-L6-v2       — 22M params, 384-dim embeddings (semantic search + zero-shot)
  • dslim/bert-base-NER    — BERT NER for entity extraction

Design: every model is lazy-loaded on first use so startup time is unchanged.
"""
from __future__ import annotations

import importlib.util
import re
import time
from typing import Any

import numpy as np

from ..catalog.taxonomy import bert_descriptions

# ---------------------------------------------------------------------------
# Model identifiers
# ---------------------------------------------------------------------------
_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
_NER_MODEL = "dslim/bert-base-NER"

# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------
_embedder = None
_ner_pipeline = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer  # type: ignore
        _embedder = SentenceTransformer(_EMBEDDING_MODEL)
    return _embedder


def _get_ner():
    global _ner_pipeline
    if _ner_pipeline is None:
        from transformers import pipeline  # type: ignore
        _ner_pipeline = pipeline(
            "ner",
            model=_NER_MODEL,
            aggregation_strategy="simple",
            device=-1,  # CPU — no GPU required
        )
    return _ner_pipeline


# ---------------------------------------------------------------------------
# Label descriptions — rich prose lets the embedding model do real semantics.
# Each description encodes what the category *means*, not just its name.
#
# Sourced from the taxonomy so BERT's zero-shot label space stays in lock-step
# with the classification taxonomy (single source of truth). Hardcoding these
# used to leave the contracts/NRI/cards leaves unreachable by the BERT service_area.
# ---------------------------------------------------------------------------
_LABEL_DESCRIPTIONS: dict[str, str] = bert_descriptions()

_LABELS = list(_LABEL_DESCRIPTIONS.keys())
_DESCRIPTIONS = list(_LABEL_DESCRIPTIONS.values())

# Cached pre-computed label embeddings (built once after model is loaded)
_label_vecs: np.ndarray | None = None


def _get_label_vecs() -> np.ndarray:
    global _label_vecs
    if _label_vecs is None:
        model = _get_embedder()
        _label_vecs = model.encode(_DESCRIPTIONS, normalize_embeddings=True)
    return _label_vecs


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def embed(text: str) -> list[float]:
    """Return a 384-dim normalized BERT embedding as a plain float list (JSON-safe)."""
    model = _get_embedder()
    vec: np.ndarray = model.encode(text[:1024], normalize_embeddings=True)
    return vec.tolist()


def cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two pre-normalized vectors."""
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    return float(np.dot(va, vb))


def zero_shot_classify(text: str) -> tuple[str, float]:
    """
    Classify *text* by measuring cosine similarity against label description
    embeddings.  Returns (label, confidence) where confidence ∈ [0.50, 0.97].
    """
    model = _get_embedder()
    label_vecs = _get_label_vecs()

    text_vec: np.ndarray = model.encode(text[:512], normalize_embeddings=True)
    sims: np.ndarray = label_vecs @ text_vec  # shape (n_labels,)

    best_idx = int(np.argmax(sims))
    best_sim = float(sims[best_idx])

    # Map cosine similarity [0.25, 0.70] → confidence [0.50, 0.97]
    raw_conf = 0.50 + (best_sim - 0.25) / (0.70 - 0.25) * (0.97 - 0.50)
    confidence = float(np.clip(raw_conf, 0.50, 0.97))

    return _LABELS[best_idx], confidence


def extract_entities(text: str) -> dict[str, Any]:
    """
    Extract structured entities from email text.

    BERT NER handles: persons, organisations.
    Regex handles: all financial identifiers (more reliable for structured patterns).
    """
    try:
        ner = _get_ner()
        raw_entities = ner(text[:512])
    except Exception:
        raw_entities = []

    def _clean(words: list[str]) -> list[str]:
        return [w for w in words if len(w) > 2 and not w.startswith("##")]

    persons = _clean([e["word"] for e in raw_entities if e.get("entity_group") == "PER"])
    orgs = _clean([e["word"] for e in raw_entities if e.get("entity_group") == "ORG"])

    # Amounts: prefix (₹/INR/Rs) or suffix (INR/rupees)
    amounts_raw = re.findall(r"(?:rs\.?|inr|₹)\s*([0-9,]+(?:\.[0-9]{1,2})?)", text.lower())
    amounts_raw.extend(re.findall(r"([0-9,]+(?:\.[0-9]{1,2})?)\s*(?:inr|rupees?)\b", text.lower()))
    amount_values: list[float] = []
    for a in amounts_raw:
        try:
            amount_values.append(float(a.replace(",", "")))
        except ValueError:
            pass

    # Account numbers: 9–18 digit strings (excl. phone-number-sized runs)
    account_nos = list(dict.fromkeys(re.findall(r"\b(\d{9,18})\b", text)))

    # UPI IDs: handle@vpa where VPA has no dot (e.g. 5510000000@upi, name@okaxis)
    payment_handles = [h.rstrip(".,;:") for h in re.findall(r"(?<![\w.-])[\w.\-]+@[\w.\-]+", text)]
    payment_handles = [h for h in payment_handles if "." not in h.rsplit("@", 1)[-1]]

    # Indian mobile numbers: 10 digits starting 6–9, optionally prefixed +91 or 0
    phone_raw = re.findall(r"(?:(?:\+91|0)\s*[-.]?\s*)?([6-9]\d{9})\b", text)
    phone_numbers = list(dict.fromkeys(phone_raw))

    # exchange code codes: 4 alpha + 0 + 6 alphanumeric (e.g. EXBK0001234)
    exchange_code_codes = list(dict.fromkeys(re.findall(r"\b([A-Z]{4}0[A-Z0-9]{6})\b", text.upper())))

    # Card numbers: 16 digits in groups of 4 (may be masked with X)
    card_raw = re.findall(
        r"\b([0-9X]{4}[\s\-]?[0-9X]{4}[\s\-]?[0-9X]{4}[\s\-]?[0-9]{4})\b", text.upper()
    )
    device_serials = [c.replace(" ", "").replace("-", "") for c in card_raw]

    # Transaction reference / UTR numbers: common service patterns
    # UTR: 22-char (payment reference), 12-digit (transactionk transfer), or alphanumeric refs like TXN/REF
    txn_refs = list(dict.fromkeys(
        re.findall(r"\b([A-Z]{2,4}[0-9]{6,18})\b", text.upper()) +
        re.findall(r"(?:utr|ref(?:erence)?|txn|transaction)\s*(?:no\.?|number|#|:)?\s*([A-Z0-9]{8,22})", text.upper())
    ))

    # Dates: DD/MM/YYYY, DD-MM-YYYY, DD Mon YYYY, Month DD YYYY
    date_strings = list(dict.fromkeys(
        re.findall(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", text) +
        re.findall(
            r"\b(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{2,4})\b",
            text.lower()
        )
    ))

    # Email addresses other than the sender (strip duplicates)
    emails_found = list(dict.fromkeys(re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)))

    return {
        "persons": persons[:3],
        "organizations": orgs[:3],
        "amount_values": sorted(set(amount_values), reverse=True)[:5],
        "connection_ids": account_nos[:3],
        "payment_handles": payment_handles[:3],
        "phone_numbers": phone_numbers[:3],
        "exchange_code_codes": exchange_code_codes[:3],
        "device_serials": device_serials[:2],
        "transaction_refs": txn_refs[:4],
        "dates_mentioned": date_strings[:5],
        "emails_found": emails_found[:3],
    }


def adjudicate_classification(
    bert_label: str,
    bert_conf: float,
    gemma_label: str,
    gemma_conf: float,
) -> tuple[str, float, str]:
    """
    Ensemble the BERT and Gemma classification signals.

    Returns (final_label, final_confidence, adjudication_mode) where
    adjudication_mode is one of: 'agreement', 'bert_wins', 'gemma_wins', 'low_confidence'.
    """
    if bert_label == gemma_label:
        # Both models agree — confidence is higher than either alone
        blended = 0.45 * bert_conf + 0.45 * gemma_conf + 0.10  # agreement bonus
        final_conf = float(np.clip(blended, 0.0, 0.97))
        return bert_label, final_conf, "agreement"

    # Models disagree — pick based on confidence gap
    gap = bert_conf - gemma_conf
    if gap > 0.15:
        # BERT is meaningfully more confident
        return bert_label, float(bert_conf * 0.90), "bert_wins"
    if gap < -0.15:
        # Gemma is meaningfully more confident
        return gemma_label, float(gemma_conf * 0.90), "gemma_wins"

    # Too close to call — fall back to Gemma (LLM reasoning is richer)
    # but mark confidence low to surface to human review
    return gemma_label, 0.62, "low_confidence"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_bert_analysis(normalized_text: str) -> dict[str, Any]:
    """
    Full BERT pass: zero-shot classification + NER + sentiment + embedding.

    Returns a dict that ai_analysis.py merges with Gemma output.
    """
    classification, confidence = zero_shot_classify(normalized_text)
    entities = extract_entities(normalized_text)
    embedding = embed(normalized_text)

    # Sentiment analysis (lazy-loaded, non-blocking on failure)
    try:
        from .sentiment import analyse_sentiment
        sentiment_result = analyse_sentiment(normalized_text)
    except Exception:
        sentiment_result = {"sentiment": "NEUTRAL", "score": 0.5, "intensity": "mild"}

    return {
        "bert_classification": classification,
        "bert_confidence": round(confidence, 4),
        "entities": entities,
        "embedding": embedding,
        "primary_amount": entities["amount_values"][0] if entities["amount_values"] else None,
        "primary_person": entities["persons"][0] if entities["persons"] else None,
        "sentiment": sentiment_result["sentiment"],
        "sentiment_score": sentiment_result["score"],
        "sentiment_intensity": sentiment_result["intensity"],
        "emotional_tone": sentiment_result.get("emotional_tone", "NEUTRAL"),
        "distress_level": sentiment_result.get("distress_level", 0),
        "sentiment_signals": sentiment_result.get("signals", []),
    }


def runtime_status() -> dict[str, Any]:
    """Return fast, non-inference BERT dependency and warm-cache status."""
    dependencies_available = all(
        importlib.util.find_spec(package) is not None
        for package in ("sentence_transformers", "transformers", "torch")
    )
    return {
        "status": "available" if dependencies_available else "unavailable",
        "dependencies_available": dependencies_available,
        "embedding_model": _EMBEDDING_MODEL,
        "ner_model": _NER_MODEL,
        "embedding_model_loaded": _embedder is not None,
        "ner_model_loaded": _ner_pipeline is not None,
    }


def verify_runtime() -> dict[str, Any]:
    """Execute one representative BERT analysis for operator diagnostics."""
    started = time.perf_counter()
    sample = run_bert_analysis(
        "Customer reports an unauthorized debit of INR 500 from my Docket account."
    )
    return {
        **runtime_status(),
        "status": "ready",
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "sample_classification": sample["bert_classification"],
        "embedding_dimensions": len(sample["embedding"]),
    }

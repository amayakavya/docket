from __future__ import annotations

from typing import Any

from triage_system import LocalStore, normalize_text, triage_email

from ..database import VAR_DIR
from ..catalog.taxonomy import (
    all_classifications,
    default_department,
    keyword_match,
    primary_type_to_classification,
    sla_tier,
)
from .bert_analysis import adjudicate_classification, run_bert_analysis
from .classification import canonical_classification, confidence_from_triage


LEGACY_STORE = VAR_DIR / "legacy_triage_runs.db"

# Default model — the configured local model preferred; falls back to the configured local model if unavailable
DEFAULT_MODEL = "the configured local model"

# Below this threshold the case goes to the human triage queue instead of auto-routing
CONFIDENCE_THRESHOLD = 0.65


def run_triage(
    email_input,
    model: str = DEFAULT_MODEL,
    normalized_text: str | None = None,
) -> dict[str, Any]:
    """
    Hybrid BERT + Gemma pipeline.

    1. BERT  — zero-shot classification against taxonomy descriptions, NER, embedding.
    2. Gemma — generative triage with strict JSON: real confidence + abstain flag.
    3. Adjudicate — ensemble the two signals into a final label + calibrated confidence.
    4. Human triage flag — set when model abstains or confidence < CONFIDENCE_THRESHOLD.
    """
    # --- BERT pass (fast, deterministic) ------------------------------------
    text_for_bert = normalized_text or normalize_text(email_input)
    bert = run_bert_analysis(text_for_bert)

    # --- Gemma pass — always the configured local model, no fallback ----------------------------
    legacy_store = LocalStore(LEGACY_STORE)
    triage = triage_email(email_input, legacy_store, model)

    # Gemma now outputs primary_type_confidence directly — use it
    gemma_conf_raw = triage.get("primary_type_confidence", None)
    gemma_classification = canonical_classification(triage)
    gemma_confidence, fallback_reason = confidence_from_triage(triage, gemma_conf_raw)

    # --- Ensemble adjudication ----------------------------------------------
    final_classification, final_confidence, adjudication_mode = adjudicate_classification(
        bert_label=bert["bert_classification"],
        bert_conf=bert["bert_confidence"],
        gemma_label=gemma_classification,
        gemma_conf=gemma_confidence,
    )

    # --- Human triage decision ----------------------------------------------
    abstain = bool(triage.get("abstain", False))
    needs_human_triage = abstain or final_confidence < CONFIDENCE_THRESHOLD

    # --- Build triage_labels (multi-signal provenance) ----------------------
    triage_labels = [
        {
            "label": final_classification,
            "confidence": round(final_confidence, 4),
            "source": "ensemble",
            "adjudication_mode": adjudication_mode,
        },
    ]
    if bert["bert_classification"] != final_classification:
        triage_labels.append({
            "label": bert["bert_classification"],
            "confidence": round(bert["bert_confidence"], 4),
            "source": "bert",
        })
    if gemma_classification != final_classification:
        triage_labels.append({
            "label": gemma_classification,
            "confidence": round(gemma_confidence, 4),
            "source": "gemma",
        })

    # --- Build explanation ---------------------------------------------------
    explanation = {
        "model": f"bert+{model}",
        "official_core": "case triage",
        "classification": final_classification,
        "confidence_score": round(final_confidence, 4),
        "adjudication_mode": adjudication_mode,
        "needs_human_triage": needs_human_triage,
        "abstain": abstain,
        "abstain_reason": triage.get("abstain_reason"),
        "confidence_threshold": CONFIDENCE_THRESHOLD,

        # Per-model signals (preserved for audit)
        "bert_classification": bert["bert_classification"],
        "bert_confidence": bert["bert_confidence"],
        "gemma_classification": gemma_classification,
        "gemma_confidence": gemma_confidence,

        # Gemma-only outputs
        "fallback_trigger_reason": fallback_reason,
        "reasoning": triage.get("audit_log", []),
        "fraud_indicators": [
            tag for tag in triage.get("secondary_tags", [])
            if tag in {"FINANCIAL-LOSS", "ACCOUNT-ACCESS", "URGENCY-STATED"}
        ],
        "routing_explanation": (
            triage.get("suggested_routing_reason") or triage.get("classification", {})
        ),

        # BERT-only outputs
        "extracted_entities": bert["entities"],
        "sentiment": bert.get("sentiment", "NEUTRAL"),
        "sentiment_score": bert.get("sentiment_score", 0.5),
        "sentiment_intensity": bert.get("sentiment_intensity", "mild"),
        "emotional_tone": bert.get("emotional_tone", "NEUTRAL"),
        "distress_level": bert.get("distress_level", 0),
        "sentiment_signals": bert.get("sentiment_signals", []),

        # Taxonomy metadata
        "default_department": default_department(final_classification),
        "sla_tier": sla_tier(final_classification),

        # Full payloads
        "raw_triage": triage,
    }

    return {
        "triage": triage,
        "classification": final_classification,
        "confidence_score": round(final_confidence, 4),
        "needs_human_triage": needs_human_triage,
        "triage_labels": triage_labels,
        "language": triage.get("customer_language", "English"),
        "bert_analysis": bert,
        "ai_analysis": explanation,
        "embedding": bert["embedding"],
    }

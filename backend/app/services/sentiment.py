"""
Sentiment analysis layer — DistilBERT SST-2 fused with service-domain keyword rules.

SST-2 gives a reliable positive/negative signal; domain keywords sharpen the
emotional tone into categories that matter for service operations (distress,
urgency, anger, legal threat, welfare risk).
"""
from __future__ import annotations

import importlib.util
from typing import Any

_sentiment_pipeline = None
_MODEL = "distilbert-base-uncased-finetuned-sst-2-english"

# ── Service-domain keyword banks ──────────────────────────────────────────────

_WELFARE_TERMS = [
    "suicide", "self harm", "kill myself", "end my life", "ruined my life",
    "destroyed my life", "life savings gone", "dying", "no reason to live",
    "harm myself", "nothing left", "lost everything",
]
_DISTRESS_TERMS = [
    "please help", "desperate", "helpless", "crying", "begging", "devastated",
    "traumatised", "traumatized", "severe distress", "mentally stressed",
    "my whole family", "my children", "rent money", "medical money",
]
_ANGER_TERMS = [
    "cheated", "looted", "scammed", "robbed", "provider at fault", "billing fraud",
    "harassment", "pathetic service", "disgusting", "incompetent", "useless staff",
    "filed fir", "police complaint", "consumer court", "consumer forum",
    "legal action", "sue", "deficiency of service", "worst provider",
]
_URGENCY_TERMS = [
    "urgent", "immediately", "asap", "emergency", "right now", "today itself",
    "within hours", "cannot wait", "critical", "life-threatening",
]
_FRUSTRATION_TERMS = [
    "not resolved", "still waiting", "no response", "follow up", "escalate",
    "multiple times", "again and again", "nth time", "months ago", "weeks ago",
    "never resolved", "ignored", "disappointed", "unacceptable", "ridiculous",
    "no action taken",
]
_LEGAL_TERMS = [
    "legal notice", "court case", "advocate", "regulator complaint", "service ombudsman",
    "consumer court", "arbitration", "compensation", "damages",
]


def _keyword_distress(text: str) -> tuple[str, int]:
    """
    Return (emotional_tone, distress_level 0-10) based purely on keywords.
    Called before the model so heavy model failures fall back gracefully.
    """
    lower = text.lower()
    if any(t in lower for t in _WELFARE_TERMS):
        return "WELFARE_RISK", 10
    if any(t in lower for t in _ANGER_TERMS):
        if any(t in lower for t in _LEGAL_TERMS):
            return "LEGAL_THREAT", 9
        return "ANGRY", 8
    if any(t in lower for t in _DISTRESS_TERMS):
        return "DISTRESSED", 8
    if any(t in lower for t in _URGENCY_TERMS):
        return "URGENT", 7
    if any(t in lower for t in _FRUSTRATION_TERMS):
        return "FRUSTRATED", 5
    if any(t in lower for t in _LEGAL_TERMS):
        return "LEGAL_THREAT", 7
    return "NEUTRAL", 0


def _get_pipeline():
    global _sentiment_pipeline
    if _sentiment_pipeline is None:
        from transformers import pipeline  # type: ignore
        _sentiment_pipeline = pipeline(
            "sentiment-analysis",
            model=_MODEL,
            device=-1,
            truncation=True,
            max_length=512,
        )
    return _sentiment_pipeline


def analyse_sentiment(text: str) -> dict[str, Any]:
    """
    Full sentiment analysis combining DistilBERT SST-2 + service domain keywords.

    Returns:
        {
          "sentiment":      "POSITIVE" | "NEGATIVE" | "NEUTRAL",
          "score":          float,           # SST-2 raw confidence 0-1
          "intensity":      "mild" | "strong",
          "emotional_tone": str,             # service-specific tone label
          "distress_level": int,             # 0-10 (10 = welfare risk)
          "signals":        list[str],       # human-readable matched signals
          "model":          str,
        }
    """
    keyword_tone, keyword_distress = _keyword_distress(text)
    signals: list[str] = []
    if keyword_tone != "NEUTRAL":
        signals.append(f"keyword:{keyword_tone.lower()}")

    bert_sentiment = "NEUTRAL"
    bert_score = 0.5
    try:
        pipe = _get_pipeline()
        result = pipe(text[:512])[0]
        label: str = result["label"].upper()
        bert_score = float(result["score"])
        bert_sentiment = "NEUTRAL" if bert_score < 0.65 else label
        signals.append(f"bert:{bert_sentiment.lower()}@{round(bert_score, 2)}")
    except Exception:
        pass

    # Fuse: keyword tone drives final emotional classification;
    # SST-2 label is used for the coarse positive/negative/neutral field.
    final_sentiment = bert_sentiment
    if keyword_tone in ("WELFARE_RISK", "ANGRY", "DISTRESSED", "FRUSTRATED", "URGENT", "LEGAL_THREAT"):
        final_sentiment = "NEGATIVE"

    distress = keyword_distress
    if bert_sentiment == "NEGATIVE" and bert_score >= 0.80 and distress < 6:
        distress = min(distress + 2, 6)

    if bert_sentiment == "POSITIVE" and distress == 0:
        signals.append("keyword:satisfied")
        emotional_tone = "SATISFIED"
    else:
        emotional_tone = keyword_tone

    intensity = "strong" if (bert_score >= 0.85 or distress >= 7) else "mild"

    return {
        "sentiment": final_sentiment,
        "score": round(bert_score, 4),
        "intensity": intensity,
        "emotional_tone": emotional_tone,
        "distress_level": distress,
        "signals": signals,
        "model": _MODEL,
    }


def runtime_status() -> dict[str, Any]:
    available = importlib.util.find_spec("transformers") is not None
    return {
        "status": "available" if available else "unavailable",
        "model": _MODEL,
        "loaded": _sentiment_pipeline is not None,
    }

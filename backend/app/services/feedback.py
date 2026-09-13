"""
ML feedback loop — track human corrections to AI classifications.

When an operator overrides an AI decision during human triage, the correction
is recorded here. This data feeds the model performance analytics and provides
a ground-truth corpus for future fine-tuning.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models import MLFeedback


def record_feedback(
    db: Session,
    *,
    case_id: str,
    original_classification: str,
    corrected_classification: str,
    original_confidence: float,
    adjudication_mode: str | None,
    actor: str,
    note: str | None = None,
) -> MLFeedback:
    """
    Store one human correction event.

    Returns the persisted MLFeedback row.
    """
    fb = MLFeedback(
        case_id=case_id,
        original_classification=original_classification,
        corrected_classification=corrected_classification,
        original_confidence=original_confidence,
        adjudication_mode=adjudication_mode,
        actor=actor,
        note=note,
        created_at=datetime.now(timezone.utc),
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)
    return fb


def list_feedback(db: Session, case_id: str | None = None) -> list[dict[str, Any]]:
    """Return feedback records, optionally filtered to one case."""
    q = db.query(MLFeedback)
    if case_id:
        q = q.filter(MLFeedback.case_id == case_id)
    rows = q.order_by(MLFeedback.created_at.desc()).all()
    return [
        {
            "id": fb.id,
            "case_id": fb.case_id,
            "original_classification": fb.original_classification,
            "corrected_classification": fb.corrected_classification,
            "original_confidence": fb.original_confidence,
            "adjudication_mode": fb.adjudication_mode,
            "is_correction": fb.original_classification != fb.corrected_classification,
            "actor": fb.actor,
            "note": fb.note,
            "created_at": fb.created_at.isoformat() if fb.created_at else None,
        }
        for fb in rows
    ]

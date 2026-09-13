from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.main import _apply_queue_move
from backend.app.models import Case, HistoricalReference
from backend.app.schemas import WorkflowActionRequest
from backend.app.services.bert_analysis import extract_entities
from backend.app.services.historical import index_case, similarity_search
from backend.app.services.workflow import (
    apply_workflow_action,
    validate_action_request,
    workflow_capabilities,
)
from backend.app.services.sla import build_sla_metadata
from triage_system import LocalStore, parse_email_bytes, triage_email


class ClassificationRegressionTests(unittest.TestCase):
    def test_customer_rbi_guideline_reference_does_not_override_fraud(self) -> None:
        raw = b"""From: customer@example.com
Subject: Unauthorized debit from my account

I did not authorize this transaction from my account. This is fraud.
Please reverse the debit under the regulator guidelines for zero liability.
"""
        email = parse_email_bytes(raw, "fraud.eml")
        model_result = {
            "primary_type": "E",
            "secondary_tags": [],
            "issue_summary": "Unauthorized transaction reported.",
            "customer_language": "English",
            "requires_human_review": True,
            "suggested_routing_reason": "Review required.",
            "case_form": {},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("triage_system.call_ollama", return_value=model_result):
                result = triage_email(email, LocalStore(Path(temp_dir) / "triage.db"), "the configured local model")

        self.assertEqual(result["primary_type"], "F")
        self.assertNotIn("regulatory_or_legal_sender", result["override_reasons"])


class BertExtractionRegressionTests(unittest.TestCase):
    def test_currency_suffix_and_upi_handle_exclude_email(self) -> None:
        text = "Transfer of 20,000 INR to ravi@okbank. Contact ravi@example.com."
        with patch("backend.app.services.bert_analysis._get_ner", return_value=lambda _text: []):
            entities = extract_entities(text)

        self.assertEqual(entities["amount_values"], [20000.0])
        self.assertEqual(entities["payment_handles"], ["ravi@okbank"])


class WorkflowRegressionTests(unittest.TestCase):
    def test_assigned_case_exposes_only_valid_stateful_actions(self) -> None:
        case = Case(
            case_id="BET-TEST",
            workflow_state="ASSIGNED",
            is_parent=False,
            primary_department="Trust & Safety Desk",
        )
        capabilities = workflow_capabilities(case)

        self.assertIn("acknowledge", capabilities["available_actions"])
        self.assertIn("start_review", capabilities["available_actions"])
        self.assertIn("suspend_service", capabilities["available_actions"])
        self.assertNotIn("resolve", capabilities["available_actions"])
        self.assertNotIn("file_regulatory_report", capabilities["available_actions"])

    def test_mandatory_action_fields_are_validated(self) -> None:
        case = Case(
            case_id="BET-TEST",
            workflow_state="ASSIGNED",
            is_parent=False,
            primary_department="Trust & Safety Desk",
        )
        request = WorkflowActionRequest(action="assign_operator", operator="")

        with self.assertRaisesRegex(ValueError, "operator"):
            validate_action_request(case, request)

    def test_specialist_resolve_actions_freeze_sla_clock(self) -> None:
        # Regression: technical_resolved / compliance_clearance reach RESOLVED via a
        # specialist action. The SLA freeze must key on the resulting state, not the
        # action name, otherwise resolved_at / minutes_to_resolve are never recorded.
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, future=True)

        for dept, action, role in [
            ("Network Support Desk", "technical_resolved", "technical_support_engineer"),
            ("Compliance Desk", "compliance_clearance", "compliance_officer"),
        ]:
            db = Session()
            try:
                case = Case(
                    case_id=f"BET-SLA-{action}",
                    source_type="txt",
                    classification="CONNECTION_FAULT",
                    workflow_state="UNDER_REVIEW",
                    primary_department=dept,
                    priority="HIGH",
                    raw_email_hash=f"hash-{action}",
                    normalized_text="issue text",
                    sla_metadata=build_sla_metadata("HIGH"),
                )
                db.add(case)
                db.flush()

                request = WorkflowActionRequest(
                    action=action, actor="op", role=role, department=dept, note="Resolved by specialist"
                )
                apply_workflow_action(db, case, request)
                db.commit()

                self.assertEqual(case.workflow_state, "RESOLVED")
                self.assertIsNotNone((case.sla_metadata or {}).get("resolved_at"))
                self.assertIn("minutes_to_resolve", case.sla_metadata or {})
            finally:
                db.close()

    def test_desk_cannot_invoke_another_desks_specialist_action(self) -> None:
        case = Case(
            case_id="BET-TEST",
            workflow_state="UNDER_REVIEW",
            is_parent=False,
            primary_department="Trust & Safety Desk",
        )
        request = WorkflowActionRequest(
            action="file_regulatory_report",
            department="Trust & Safety Desk",
            regulatory_ref="the regulator-1",
        )

        with self.assertRaisesRegex(ValueError, "not permitted"):
            validate_action_request(case, request)


class QueueMoveRegressionTests(unittest.TestCase):
    def _case(self, case_id: str, position: int | None = None) -> Case:
        return Case(
            case_id=case_id,
            workflow_state="ASSIGNED",
            primary_department="Trust & Safety Desk",
            queue_position=position,
        )

    def test_queue_move_normalizes_full_queue_before_swap(self) -> None:
        queue = [self._case("A"), self._case("B"), self._case("C"), self._case("D")]

        target, previous_position, previous_order, new_order = _apply_queue_move(queue, "C", "up")

        self.assertEqual(target.case_id, "C")
        self.assertIsNone(previous_position)
        self.assertEqual(previous_order, 3)
        self.assertEqual(new_order, 2)
        self.assertEqual(
            sorted((case.case_id, case.queue_position) for case in queue),
            [("A", 1), ("B", 3), ("C", 2), ("D", 4)],
        )
        self.assertEqual(
            [case.case_id for case in sorted(queue, key=lambda case: case.queue_position or 9999)],
            ["A", "C", "B", "D"],
        )

    def test_queue_move_blocks_invalid_boundary_move(self) -> None:
        queue = [self._case("A"), self._case("B")]

        with self.assertRaises(HTTPException) as ctx:
            _apply_queue_move(queue, "A", "up")

        self.assertEqual(ctx.exception.status_code, 409)


class HistoricalIndexRegressionTests(unittest.TestCase):
    def test_index_case_stores_and_updates_resolution_summary(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, future=True)
        db = Session()
        try:
            case = Case(
                case_id="BET-HIST",
                source_type="txt",
                classification="UNAUTHORISED_USE",
                workflow_state="CLOSED",
                primary_department="Trust & Safety Desk",
                raw_email_hash="hash",
                normalized_text="unauthorized debit from account",
                resolution_text="Account blocked and debit reversed.",
            )
            db.add(case)
            db.flush()

            index_case(db, case)
            db.commit()

            ref = db.query(HistoricalReference).filter(HistoricalReference.case_id == "BET-HIST").one()
            self.assertEqual(ref.resolution_summary, "Account blocked and debit reversed.")

            case.resolution_text = "Customer reimbursed and credentials reset."
            index_case(db, case)
            db.commit()

            ref = db.query(HistoricalReference).filter(HistoricalReference.case_id == "BET-HIST").one()
            self.assertEqual(ref.resolution_summary, "Customer reimbursed and credentials reset.")
        finally:
            db.close()

    def test_case_detail_similarity_can_skip_cold_model_load(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, future=True)
        db = Session()
        try:
            case = Case(
                case_id="BET-HIST-FAST",
                source_type="txt",
                classification="UNAUTHORISED_USE",
                workflow_state="CLOSED",
                primary_department="Trust & Safety Desk",
                raw_email_hash="hash-fast",
                normalized_text="unauthorized debit upi fraud account",
                resolution_text="UPI handle blocked and customer reimbursed.",
            )
            db.add(case)
            db.flush()
            index_case(db, case)
            db.commit()

            with patch("backend.app.services.bert_analysis.runtime_status", return_value={"embedding_model_loaded": False}):
                with patch("backend.app.services.bert_analysis.embed", side_effect=AssertionError("should not load BERT")):
                    results = similarity_search(db, "unauthorized debit fraud", allow_model_load=False)

            self.assertTrue(results)
            self.assertEqual(results[0]["similarity_method"], "token_jaccard")
        finally:
            db.close()


class RiskEngineRegressionTests(unittest.TestCase):
    """Composite risk scoring — the Docket-scale within-band differentiator."""

    def _signals(self, **overrides):
        base = {
            "monetary_amount_detected": None,
            "regulatory_flag": False,
            "repeat_flag": False,
            "sentiment": "NEUTRAL",
        }
        base.update(overrides)
        return base

    def test_monetary_magnitude_separates_same_category_cases(self) -> None:
        from backend.app.services.risk_engine import compute_risk_assessment

        small = compute_risk_assessment(
            category="DISPUTE", signals=self._signals(monetary_amount_detected=2_000)
        )
        large = compute_risk_assessment(
            category="DISPUTE", signals=self._signals(monetary_amount_detected=50_000_000)
        )
        # Same category, but a ₹5Cr dispute must outrank a ₹2,000 one.
        self.assertGreater(large.score, small.score)

    def test_vulnerable_segment_raises_score(self) -> None:
        from backend.app.services.risk_engine import compute_risk_assessment

        plain = compute_risk_assessment(category="COMPLAINT", signals=self._signals())
        senior = compute_risk_assessment(
            category="COMPLAINT", signals=self._signals(), customer_segment="SENIOR_CITIZEN"
        )
        self.assertGreater(senior.score, plain.score)
        self.assertTrue(any(f["name"] == "customer_vulnerability" for f in senior.factors))

    def test_score_is_bounded_and_normalized(self) -> None:
        from backend.app.services.risk_engine import compute_risk_assessment

        maxed = compute_risk_assessment(
            category="LEGAL_NOTICE",
            signals=self._signals(
                monetary_amount_detected=10_000_000_000,
                regulatory_flag=True,
                repeat_flag=True,
                sentiment="NEGATIVE",
            ),
            customer_segment="HNI",
            impersonation_level="CRITICAL",
            regulatory_override="CRITICAL",
            correlated_case_count=500,
        )
        self.assertLessEqual(maxed.score, 100.0)
        self.assertEqual(maxed.band, "CRITICAL")
        self.assertAlmostEqual(maxed.normalized, maxed.score / 100.0, places=4)

    def test_factors_are_explainable(self) -> None:
        from backend.app.services.risk_engine import compute_risk_assessment

        assessment = compute_risk_assessment(
            category="UNAUTHORISED_USE",
            signals=self._signals(monetary_amount_detected=100_000, repeat_flag=True),
        )
        names = {f["name"] for f in assessment.factors}
        # Every contributing term must be present in the audit breakdown.
        self.assertIn("category_severity", names)
        self.assertIn("monetary_exposure", names)
        self.assertIn("repeat_contact", names)

    def test_escalate_priority_only_lifts_never_lowers(self) -> None:
        from backend.app.services.risk_engine import escalate_priority

        # A composite score in the CRITICAL band lifts a MEDIUM rule verdict.
        self.assertEqual(escalate_priority("MEDIUM", 0.85), "CRITICAL")
        # A low composite score never downgrades a CRITICAL rule verdict.
        self.assertEqual(escalate_priority("CRITICAL", 0.20), "CRITICAL")

    def test_aging_contribution_ramps_after_half_window(self) -> None:
        from backend.app.services.risk_engine import aging_contribution

        self.assertEqual(aging_contribution(30, 240), 0.0)      # 12.5% elapsed → nothing
        self.assertEqual(aging_contribution(None, 240), 0.0)    # unknown age → safe
        fresh = aging_contribution(150, 240)                    # 62.5% elapsed
        breached = aging_contribution(300, 240)                 # past deadline
        self.assertGreater(fresh, 0.0)
        self.assertGreaterEqual(breached, fresh)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="txt")
    classification: Mapped[str] = mapped_column(String(64), index=True)
    workflow_state: Mapped[str] = mapped_column(String(64), index=True, default="NEW")
    primary_department: Mapped[str] = mapped_column(String(128), index=True)
    secondary_departments: Mapped[list] = mapped_column(JSON, default=list)
    customer_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    email_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    extracted_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    ai_analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    priority: Mapped[str] = mapped_column(String(32), index=True, default="LOW")
    sla_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    escalation_state: Mapped[dict] = mapped_column(JSON, default=dict)
    linked_cases: Mapped[list] = mapped_column(JSON, default=list)
    incident_group: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    attachments: Mapped[list] = mapped_column(JSON, default=list)
    # ── Parent-child linkage ────────────────────────────────────────────────
    # is_parent=True  → this is a parent case; its workflow_state is derived
    #                   from children and cannot be set directly
    # parent_case_id  → set on child sub-cases; None for standalone + parents
    # child_case_ids  → set on parent cases; empty for standalone + children
    is_parent: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    parent_case_id: Mapped[str | None] = mapped_column(String(72), nullable=True, index=True)
    child_case_ids: Mapped[list] = mapped_column(JSON, default=list)
    # ───────────────────────────────────────────────────────────────────────
    raw_email_hash: Mapped[str] = mapped_column(String(96), index=True)
    raw_email_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    raw_email: Mapped[str] = mapped_column(Text, default="")
    normalized_text: Mapped[str] = mapped_column(Text, default="")
    audit_history: Mapped[list] = mapped_column(JSON, default=list)
    unified_timeline: Mapped[list] = mapped_column(JSON, default=list)
    # ── Resolution & queue management ───────────────────────────────────────
    resolution_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    queue_position: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # queue_pinned=True → operator manually set position; auto-rerank by SLA/priority skips it
    queue_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    # ── Human triage & multi-label ───────────────��──────────────────────────
    # needs_human_triage: model abstained or confidence < threshold
    needs_human_triage: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    language: Mapped[str] = mapped_column(String(64), default="English")
    # triage_labels: list of {label, confidence, source} dicts from AI
    triage_labels: Mapped[list] = mapped_column(JSON, default=list)
    # ── Smart intake flags ───────────────────────────────────────────────────
    # Populated by intake_triage pre-flight: thread/external provider/sparse detection results
    intake_flags: Mapped[dict] = mapped_column(JSON, default=dict)
    # ── Customer linkage ────────────────────────────────────────────────────
    # customer reference of the matched Subscriber; None if customer not identified
    customer_customer_ref: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    departments: Mapped[list["CaseDepartment"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    timeline_events: Mapped[list["TimelineEvent"]] = relationship(back_populates="case", cascade="all, delete-orphan")


class CaseDepartment(Base):
    __tablename__ = "case_departments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[str] = mapped_column(String(64), ForeignKey("cases.case_id"), index=True)
    department: Mapped[str] = mapped_column(String(128), index=True)
    department_role: Mapped[str] = mapped_column(String(32), default="primary")
    routing_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(64), default="NEW", index=True)
    assigned_operator: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Department-local priority — computed from this dept's own ruleset, independent of Case.priority
    priority: Mapped[str] = mapped_column(String(32), default="LOW", index=True)
    findings: Mapped[dict] = mapped_column(JSON, default=dict)
    internal_notes: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    case: Mapped[Case] = relationship(back_populates="departments")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("cases.case_id"), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(128), default="system")
    role: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(128), index=True)
    event_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    previous_state: Mapped[dict] = mapped_column(JSON, default=dict)
    new_state: Mapped[dict] = mapped_column(JSON, default=dict)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    case: Mapped[Case | None] = relationship(back_populates="audit_events")


class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[str] = mapped_column(String(64), ForeignKey("cases.case_id"), index=True)
    actor: Mapped[str] = mapped_column(String(128), default="system")
    department: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    message: Mapped[str] = mapped_column(Text)
    event_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    case: Mapped[Case] = relationship(back_populates="timeline_events")


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(64), default="OPEN", index=True)
    pattern_key: Mapped[str] = mapped_column(String(128), index=True)
    master_case_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    case_ids: Mapped[list] = mapped_column(JSON, default=list)
    incident_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class DepartmentPriorityRule(Base):
    """
    Database-driven per-department priority rules.
    Rules are evaluated in rule_order (ascending); first match wins.
    All non-null signal fields must match for the rule to fire (AND logic).
    Edit rows in the DB to change behaviour without redeployment.
    """
    __tablename__ = "department_priority_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    department_code: Mapped[str] = mapped_column(String(64), index=True)   # e.g. "FRAUD_AND_RISK"
    rule_name: Mapped[str] = mapped_column(String(128))

    # ── Signal conditions (all non-null fields must match) ─────────────────
    signal_category: Mapped[str | None] = mapped_column(String(64), nullable=True)   # classification
    signal_keywords: Mapped[list | None] = mapped_column(JSON, nullable=True)         # any keyword must be present
    monetary_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)    # amount >= threshold
    regulatory_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    repeat_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sender_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ── Output ────────────────────────────────────────────────────────────
    priority_result: Mapped[str] = mapped_column(String(32))                          # CRITICAL/HIGH/MEDIUM/LOW
    rule_order: Mapped[int] = mapped_column(Integer, default=100)                     # lower = evaluated first
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class HistoricalReference(Base):
    __tablename__ = "historical_references"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[str] = mapped_column(String(64), index=True)
    normalized_text: Mapped[str] = mapped_column(Text)
    tokens: Mapped[list] = mapped_column(JSON, default=list)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    classification: Mapped[str] = mapped_column(String(64), index=True)
    primary_department: Mapped[str] = mapped_column(String(128), index=True)
    resolution_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Subscriber(Base):
    """
    Comprehensive mock Docket customer — every field a real core-service profile carries.

    Identifiers used by customer_validation.py for email matching:
      connection_ids, device_serials, payment_handles, customer_ref, mobile_number

    Rich product data surfaced in the Smart Intake sparse-email panel:
      contracts, addon_services, static_ip_blocks, premises_equipment,
      protection_plans, roaming_profile
    """
    __tablename__ = "subscribers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # ── Core identifiers ─────────────────────────────────────────────────────
    customer_ref: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    mobile_number: Mapped[str] = mapped_column(String(15), index=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    tax_id: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    id_last4: Mapped[str | None] = mapped_column(String(8), nullable=True)   # last 4 digits
    date_of_birth: Mapped[str | None] = mapped_column(String(12), nullable=True)   # YYYY-MM-DD

    # ── Service area ───────────────────────────────────────────────────────────────
    service_area: Mapped[str] = mapped_column(String(128))
    area_code: Mapped[str] = mapped_column(String(16))

    # ── Status / segment ─────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    verification_status: Mapped[str] = mapped_column(String(32), default="COMPLETED")
    customer_segment: Mapped[str] = mapped_column(String(32), default="RETAIL")
    # RETAIL | HNI | PRIORITY | NRI | CORPORATE | AGRI | SALARY | SENIOR_CITIZEN
    occupation: Mapped[str | None] = mapped_column(String(64), nullable=True)
    annual_income: Mapped[float | None] = mapped_column(Float, nullable=True)
    payment_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Address ──────────────────────────────────────────────────────────────
    address: Mapped[dict] = mapped_column(JSON, default=dict)
    # {street, city, state, pincode, country}

    # ── Deposit / transaction accounts ──────────────────────────────────────
    connection_ids: Mapped[list] = mapped_column(JSON, default=list)
    # flat list of SB/CA/OD/CC/roaming/roaming connection ids used by validation

    connection_details: Mapped[list] = mapped_column(JSON, default=list)
    # [{connection_id, type: SB/CA/OD/CC/roaming/roaming/roaming/JAN_DHAN/SALARY,
    #   balance, status: ACTIVE/DORMANT/BLOCKED/CLOSED, exchange_code, od_limit?}]

    # ── Cards ────────────────────────────────────────────────────────────────
    device_serials: Mapped[list] = mapped_column(JSON, default=list)
    # flat list used by validation

    device_details: Mapped[list] = mapped_column(JSON, default=list)
    # [{card_number, type: DEBIT/CREDIT/PREPAID, variant: CLASSIC/GOLD/PLATINUM/SIGNATURE,
    #   network: VISA/MASTERCARD/RUPAY, status: ACTIVE/BLOCKED/EXPIRED,
    #   expiry, credit_limit?, outstanding_balance?}]

    # ── UPI ──────────────────────────────────────────────────────────────────
    payment_handles: Mapped[list] = mapped_column(JSON, default=list)

    # ── Contracts ────────────────────────────────────────────────────────────────
    contracts: Mapped[list] = mapped_column(JSON, default=list)
    # [{contract_number, type: HOME/AUTO/PERSONAL/EDUCATION/GOLD///LAP/OD,
    #   sanctioned_amount, outstanding_amount, instalment_amount, tenure_months,
    #   interest_rate, status: ACTIVE/CLOSED/NPA/RESTRUCTURED,
    #   instalment_due_date, next_instalment_date, collateral?}]

    # ── Deposits (FD / RD /  / SSA / NSC) ─────────────────────────────────
    addon_services: Mapped[list] = mapped_column(JSON, default=list)
    # [{folio_number, type: FD/RD//SSA/NSC/, principal_amount,
    #   interest_rate, maturity_date, maturity_amount, status, auto_renew?}]

    # ── Static IP allocations ────────────────────────────────────────────────
    static_ip_blocks: Mapped[list] = mapped_column(JSON, default=list)
    # [{static IP_number, dp_id, trading_account?, dp_name, status}]

    # ── Premises equipment ───────────────────────────────────────────────────
    premises_equipment: Mapped[list] = mapped_column(JSON, default=list)
    # [{premises_kit_number, service_area, size: SMALL/MEDIUM/LARGE, annual_rent, status}]

    # ── Insurance ────────────────────────────────────────────────────────────
    protection_plans: Mapped[list] = mapped_column(JSON, default=list)
    # [{policy_number, type: LIFE/TERM/HEALTH/MOTOR/HOME/ACCIDENT,
    #   insurer: Docket_LIFE/Docket_GENERAL, sum_assured, premium_annual,
    #   premium_frequency, status, maturity_date?}]

    # ── NRI specifics ────────────────────────────────────────────────────────
    roaming_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    # {country_of_residence, passport_number, visa_type, nre_accounts, nro_accounts,
    #  fcnr_deposits, roaming_partner, fema_declaration_date}

    # ── Relationship & digital ────────────────────────────────────────────────
    account_manager: Mapped[str | None] = mapped_column(String(128), nullable=True)
    self_care_portal: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    # ACTIVE | BLOCKED | LOCKED | NOT_REGISTERED
    mobile_app_access: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    # ACTIVE | BLOCKED | NOT_REGISTERED
    sms_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    email_alerts: Mapped[bool] = mapped_column(Boolean, default=True)

    # ── Nominee ──────────────────────────────────────────────────────────────
    alternate_contact: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    # {name, relationship, dob, mobile}

    # ── Flat product summary (for validation quick-match) ────────────────────
    product_types: Mapped[list] = mapped_column(JSON, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MLFeedback(Base):
    """Human corrections to AI classifications — ground truth for model improvement."""
    __tablename__ = "ml_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[str] = mapped_column(String(64), index=True)
    original_classification: Mapped[str] = mapped_column(String(64))
    corrected_classification: Mapped[str] = mapped_column(String(64))
    original_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    adjudication_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor: Mapped[str] = mapped_column(String(128), default="operator")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IntakeError(Base):
    """Dead-letter queue — failed intake attempts that must not be silently lost."""
    __tablename__ = "intake_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    error_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_filename: Mapped[str] = mapped_column(String(256), default="unknown")
    raw_email_hash: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    raw_email_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_type: Mapped[str] = mapped_column(String(128), default="unknown")
    error_detail: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(128), default="system")
    retryable: Mapped[bool] = mapped_column(Boolean, default=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


WorkflowAction = Literal[
    # Common actions (all desks)
    "acknowledge",
    "assign_operator",
    "start_review",
    "escalate",
    "resolve",
    "reopen",
    "close",
    "add_note",
    "change_priority",
    "request_department",
    "attach_evidence",
    "release_triage",              # Human triage queue — release to department
    # Trust & Safety Desk
    "suspend_service",
    "request_evidence",
    "flag_usage",
    # Customer Resolution Desk
    "offer_resolution",
    "send_communication",
    # Compliance Desk
    "file_regulatory_report",
    "compliance_clearance",
    # Network Support Desk
    "raise_field_job",
    "technical_resolved",
    # Central Operations Desk
    "inter_dept_transfer",
    "override_priority_critical",
    "multi_dept_review",
]


class TriageReleaseRequest(BaseModel):
    """Release a case from the Human Triage Queue to one or more departments."""
    department: str                        # primary department (required)
    departments: list[str] = []            # additional secondary departments
    classification: str | None = None
    note: str | None = None
    actor: str = "triage_operator"


class TextIngestRequest(BaseModel):
    filename: str = Field(default="pasted_email.txt", min_length=1, max_length=240)
    text: str = Field(min_length=10, max_length=2_000_000)
    actor: str = "intake_operator"


class WorkflowActionRequest(BaseModel):
    action: WorkflowAction
    actor: str = "department_operator"
    role: str = "intake_operator"
    department: str | None = None
    operator: str | None = None
    priority: str | None = None
    requested_department: str | None = None
    note: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    customer_visible: bool = False
    # Desk-specific extras
    ticket_ref: str | None = None          # Technical desk IT ticket
    regulatory_ref: str | None = None      # Compliance desk filing ref
    transfer_to: str | None = None         # Central Ops transfer target


class CaseResponse(BaseModel):
    case_id: str
    source_type: str
    classification: str
    workflow_state: str
    primary_department: str
    secondary_departments: list[Any]
    customer_metadata: dict[str, Any]
    email_metadata: dict[str, Any]
    extracted_fields: dict[str, Any]
    ai_analysis: dict[str, Any]
    confidence_score: float
    risk_score: float
    priority: str
    sla_metadata: dict[str, Any]
    escalation_state: dict[str, Any]
    linked_cases: list[Any]
    incident_group: str | None
    attachments: list[Any]
    audit_history: list[Any]
    unified_timeline: list[Any]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DashboardResponse(BaseModel):
    counts: dict[str, Any]
    incoming_queue: list[dict[str, Any]]
    classification_stream: list[dict[str, Any]]
    sla_alerts: list[dict[str, Any]]
    escalation_alerts: list[dict[str, Any]]
    department_workloads: list[dict[str, Any]]
    incident_clusters: list[dict[str, Any]]
    ai_confidence_metrics: dict[str, Any]
    operational_heatmap: list[dict[str, Any]]


class SearchResponse(BaseModel):
    query: str
    matches: list[dict[str, Any]]


class QueueMoveRequest(BaseModel):
    direction: Literal["up", "down"]


class QueueReorderRequest(BaseModel):
    """Drag-and-drop: move case_id to new_index (0-based) in the active queue."""
    case_id: str
    new_index: int


class DeptQueueMoveRequest(BaseModel):
    case_id: str
    direction: Literal["up", "down"]


PortalRequestType = Literal[
    "fraud",
    "technical",
    "account_access",
    "compliance",
    "grievance",
    "escalation",
    "general",
]

PortalUrgency = Literal["critical", "high", "medium", "low"]


class PortalSubmitRequest(BaseModel):
    requester_name: str = Field(min_length=2, max_length=120)
    requester_email: str = Field(min_length=5, max_length=200)
    request_type: PortalRequestType
    description: str = Field(min_length=20, max_length=5000)
    reference_number: str | None = Field(default=None, max_length=80)
    urgency: PortalUrgency = "medium"
    connection_id: str | None = Field(default=None, max_length=20)


class PortalSubmitResponse(BaseModel):
    case_id: str
    priority: str
    suggested_department: str
    message: str

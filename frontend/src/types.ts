export type TriageLabel = {
  label: string;
  confidence: number;
  source: "ensemble" | "bert" | "gemma";
  adjudication_mode?: string;
};

export type Dashboard = {
  counts: Record<string, any>;
  incoming_queue: CaseSummary[];   // active (non-closed) cases, queue-position ordered
  closed_queue: CaseSummary[];     // CLOSED / RESOLVED cases, most-recent first
  classification_stream: any[];
  sla_alerts: any[];
  escalation_alerts: CaseSummary[];
  department_workloads: any[];
  incident_clusters: any[];
  ai_confidence_metrics: Record<string, any>;
  operational_heatmap: any[];
};

export type ImpersonationRisk = {
  level: "NONE" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" | "UNKNOWN";
  score: number;
  signals: string[];
  recommendation: string;
};

export type RepeatDetection = {
  repeat_type: "exact_repeat" | "new_issue" | "first_contact";
  prior_cases: {
    case_id: string;
    similarity: number | null;
    classification: string;
    workflow_state: string;
    created_at: string | null;
    resolution_text?: string | null;
  }[];
  top_similarity: number | null;
  open_count: number;
  resolved_count: number;
  same_complaint_case: string | null;
};

export type ClarificationDraft = {
  needed: boolean;
  subject?: string;
  body?: string;
  missing_fields?: string[];
  conflict_fields?: string[];
  reason?: string;
};

export type RegulatoryBreach = {
  detected: boolean;
  breach_type: string;
  severity: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "NONE";
  compliance_sla_hours: number;
  escalation_path: string;
  all_signals: string[];
  urgency_boosted: boolean;
  override_priority: string | null;
  details: string;
};

export type AttachmentExtracted = {
  filename: string;
  content_type: string;
  extraction_method: "pdf" | "ocr" | "plain" | "skipped";
  char_count: number;
  entities: {
    amounts: string[];
    reference_ids: string[];
    dates: string[];
    connection_ids: string[];
    error_codes: string[];
  };
};

export type AutoDraft = {
  subject: string;
  body: string;
  source: "llm" | "template";
  classification: string;
  context_injected?: Record<string, any>;
};

export type IntakeFlags = {
  verdict?: "proceed" | "external_bank" | "off_topic" | "sparse" | "empty" | string;
  thread_detected?: boolean;
  thread_depth?: number;
  top_message_extracted?: boolean;
  thread_turns?: { role: string; text: string }[];
  is_external_bank?: boolean;
  is_off_topic?: boolean;
  competitor_bank?: string | null;
  off_topic_domain?: string | null;
  external_bank_confidence?: number;
  external_bank_reason?: string;
  information_density?: "sufficient" | "sparse" | "empty";
  meaningful_word_count?: number;
  identifier_count?: number;
  extracted_issue_hint?: string | null;
  customer_matched?: boolean;
  sender_validation_run?: boolean;
  customer_validation_status?: "full" | "partial" | "none" | "no_identifiers" | string;
  customer_matched_fields?: string[];
  claimed_names?: string[];
  cross_conflict?: {
    has_conflict: boolean;
    has_strong_conflict: boolean;
    conflicting_customer_count: number;
  };
  impersonation_risk?: ImpersonationRisk;
  repeat_detection?: RepeatDetection;
  clarification?: ClarificationDraft;
  regulatory_breach?: RegulatoryBreach;
  attachments_extracted?: AttachmentExtracted[];
  attachment_entities?: {
    amounts: string[];
    reference_ids: string[];
    dates: string[];
    connection_ids: string[];
    error_codes: string[];
  };
  auto_draft?: AutoDraft;
  sparse_customer_match?: any;
  sparse_solutions?: any[];
  redirect_subject?: string;
  redirect_body?: string;
};

export type CaseSummary = {
  case_id: string;
  classification: string;
  workflow_state: string;
  priority: string;
  risk_score: number;
  confidence_score: number;
  primary_department: string;
  secondary_departments: any[];
  summary?: string;
  amount_involved?: number;
  customer?: string;
  sla_alerts: any[];
  incident_group?: string;
  created_at: string;
  updated_at: string;
  // parent-child
  is_parent: boolean;
  parent_case_id?: string | null;
  child_count: number;
  // department queue extras
  department_role?: string;
  department_status?: string;
  department_priority?: string;   // dept-local priority from CaseDepartment.priority
  assigned_operator?: string;
  routing_confidence?: number;
  findings?: Record<string, any>;
  internal_notes?: any[];
  // queue management
  queue_position?: number | null;
  queue_pinned?: boolean;
  resolution_text?: string | null;
  // human triage & multi-label
  needs_human_triage?: boolean;
  language?: string;
  triage_labels?: TriageLabel[];
  // smart intake
  intake_flags?: IntakeFlags;
  sla_metadata?: Record<string, any>;
};

export type SuggestedSolution = {
  case_id: string;
  classification: string;
  similarity: number;
  similarity_method: string;
  resolution_text: string;
  summary?: string | null;
};

export type CaseDetail = CaseSummary & {
  source_type: string;
  customer_metadata: Record<string, any>;
  email_metadata: Record<string, any>;
  extracted_fields: Record<string, any>;
  ai_analysis: Record<string, any>;
  sla_metadata: Record<string, any>;
  escalation_state: Record<string, any>;
  linked_cases: any[];
  child_case_ids: string[];
  attachments: any[];
  raw_email_hash: string;
  raw_email_path?: string;
  raw_email: string;
  normalized_text: string;
  departments: any[];
  audit_history: any[];
  unified_timeline: any[];
  // populated server-side when is_parent or parent_case_id is set
  children?: CaseSummary[];
  parent_summary?: CaseSummary;
  sibling_cases?: CaseSummary[];
  available_actions: string[];
  blocked_actions: Record<string, string>;
  valid_next_states: string[];
  // BERT-powered solution suggestions from similar closed cases
  suggested_solutions?: SuggestedSolution[];
  solutions_synthesis?: {
    total_similar: number;
    headline: string;
    top_classifications: { label: string; count: number }[];
    top_actions: { label: string; count: number }[];
    top_department: string | null;
    avg_similarity: number;
  } | null;
};

export type Department = {
  department: string;
  slug: string;
};

export type DepartmentQueue = {
  department: string;
  slug: string;
  items: CaseSummary[];                    // active (non-closed, non-triage) cases
  pending_triage_items?: CaseSummary[];    // HUMAN_TRIAGE — visible but locked
  closed_items?: CaseSummary[];            // CLOSED / RESOLVED cases
  counts: Record<string, number>;
};

export type DeptStats = {
  department: string;
  slug: string;
  total_cases: number;
  classification_breakdown: Record<string, number>;
  operator_workloads: Record<string, number>;
  sla_health: { overdue: number; nearing_breach: number; healthy: number };
  workflow_states: Record<string, number>;
};

export type ModelRuntime = {
  status: "available" | "ready" | "unavailable" | "missing_model" | "failed";
  error?: string;
  latency_ms?: number;
  model?: string;
  embedding_model?: string;
  ner_model?: string;
  model_installed?: boolean;
};

export type PortalRequestType = "fraud" | "technical" | "account_access" | "compliance" | "grievance" | "escalation" | "general";
export type PortalUrgency = "critical" | "high" | "medium" | "low";

export type PortalSubmitPayload = {
  requester_name: string;
  requester_email: string;
  request_type: PortalRequestType;
  description: string;
  reference_number?: string;
  urgency: PortalUrgency;
  connection_id?: string;
};

export type PortalSubmitResult = {
  case_id: string;
  priority: string;
  suggested_department: string;
  message: string;
};

export type SystemReadiness = {
  ok: boolean;
  status: "available" | "ready" | "degraded";
  checked_at: string;
  pipeline: string;
  bert: ModelRuntime;
  ollama: ModelRuntime;
  database?: { status: string; case_count: number };
};

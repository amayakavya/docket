import type { CaseDetail, Dashboard, Department, DepartmentQueue, DeptStats, PortalSubmitPayload, PortalSubmitResult, SystemReadiness } from "./types";

export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8010";
const headers = { "Content-Type": "application/json" };

async function request<T>(path: string, init?: RequestInit, timeoutMs = 15000): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_BASE}${path}`, { ...init, signal: controller.signal });
    const text = await response.text();
    let payload: any = {};
    try {
      payload = text ? JSON.parse(text) : {};
    } catch {
      payload = { detail: text || "The server returned an unreadable response." };
    }
    if (!response.ok) throw new Error(payload.detail || payload.error || `Request failed (${response.status})`);
    return payload as T;
  } catch (error: any) {
    if (error?.name === "AbortError") {
      throw new Error("Request timed out. Check service health and retry.");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

export const api = {
  readiness: () => request<SystemReadiness>("/api/v1/system/readiness"),
  verifyModels: () => request<SystemReadiness>("/api/v1/system/readiness/verify", { method: "POST" }, 240000),
  dashboard: () => request<Dashboard>("/api/v1/central/dashboard"),
  cases: () => request<any[]>("/api/v1/cases"),
  caseDetail: (caseId: string, audit = false) =>
    request<CaseDetail>(`/api/v1/cases/${encodeURIComponent(caseId)}${audit ? "?audit=true" : ""}`, undefined, 30000),
  departments: () => request<Department[]>("/api/v1/departments"),
  departmentQueue: (department: string) =>
    request<DepartmentQueue>(`/api/v1/departments/${encodeURIComponent(department)}/queue`),
  departmentStats: (department: string) =>
    request<DeptStats>(`/api/v1/departments/${encodeURIComponent(department)}/stats`),
  slaAlerts: () => request<any[]>("/api/v1/sla/alerts"),
  incidents: () => request<any[]>("/api/v1/incidents"),
  audit: (caseId?: string) =>
    request<any[]>(`/api/v1/audit${caseId ? `?case_id=${encodeURIComponent(caseId)}` : ""}`),
  caseChildren: (caseId: string) =>
    request<any[]>(`/api/v1/cases/${encodeURIComponent(caseId)}/children`),
  historicalSearch: (query: string) =>
    request<any>(`/api/v1/historical/search?q=${encodeURIComponent(query)}`, undefined, 60000),
  caseSearch: (q: string) =>
    request<any[]>(`/api/v1/cases/search?q=${encodeURIComponent(q)}`, undefined, 15000),
  createContinuation: (caseId: string, note?: string) =>
    request<any>(`/api/v1/cases/${encodeURIComponent(caseId)}/continuation`, {
      method: "POST",
      headers,
      body: JSON.stringify({ note: note ?? "" }),
    }),
  ingestText: (text: string, filename = "pasted_email.txt") =>
    request<CaseDetail>("/api/v1/intake/text", {
      method: "POST",
      headers,
      body: JSON.stringify({ text, filename }),
    }, 240000),
  ingestFile: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<CaseDetail>("/api/v1/intake/email", { method: "POST", body }, 240000);
  },
  workflow: (caseId: string, body: Record<string, any>) =>
    request<CaseDetail>(`/api/v1/cases/${encodeURIComponent(caseId)}/workflow`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    }),
  deptQueueMove: (dept: string, caseId: string, direction: "up" | "down") =>
    request<any>(`/api/v1/departments/${encodeURIComponent(dept)}/queue-move`, {
      method: "POST",
      headers,
      body: JSON.stringify({ case_id: caseId, direction }),
    }),
  deptQueueReorder: (dept: string, caseId: string, newIndex: number) =>
    request<any>(`/api/v1/departments/${encodeURIComponent(dept)}/queue-reorder`, {
      method: "POST",
      headers,
      body: JSON.stringify({ case_id: caseId, new_index: newIndex }),
    }),
  centralQueueMove: (caseId: string, direction: "up" | "down") =>
    request<any>(`/api/v1/cases/${encodeURIComponent(caseId)}/queue-move`, {
      method: "POST",
      headers,
      body: JSON.stringify({ direction }),
    }),
  unpinCase: (caseId: string) =>
    request<any>(`/api/v1/cases/${encodeURIComponent(caseId)}/unpin`, {
      method: "POST",
      headers,
    }),
  portalSubmit: (payload: PortalSubmitPayload) =>
    request<PortalSubmitResult>("/api/v1/portal/submit", {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    }, 30000),
  triageQueue: () => request<any[]>("/api/v1/triage-queue"),
  releaseTriage: (caseId: string, body: { department: string; departments?: string[]; classification?: string; note?: string; actor?: string }) =>
    request<any>(`/api/v1/triage-queue/${encodeURIComponent(caseId)}/release`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    }),
  errorQueue: () => request<any[]>("/api/v1/errors"),
  dismissError: (errorId: string) =>
    request<any>(`/api/v1/errors/${encodeURIComponent(errorId)}/dismiss`, { method: "POST" }),
  customerLookup: (caseId: string) =>
    request<any>(`/api/v1/cases/${encodeURIComponent(caseId)}/customer-lookup`, undefined, 10000),
  customerHistory: (caseId: string) =>
    request<any>(`/api/v1/cases/${encodeURIComponent(caseId)}/customer-history`, undefined, 10000),
  summarizeThread: (caseId: string) =>
    request<any>(`/api/v1/cases/${encodeURIComponent(caseId)}/summarize-thread`, { method: "POST" }, 120000),
  editTriageComplaint: (caseId: string, complaintText: string) =>
    request<any>(`/api/v1/triage-queue/${encodeURIComponent(caseId)}/complaint`, {
      method: "PATCH",
      headers,
      body: JSON.stringify({ complaint_text: complaintText }),
    }),
  // Analytics
  analyticsVolumeTrend: (days = 14) =>
    request<any[]>(`/api/v1/analytics/volume-trend?days=${days}`),
  analyticsClassificationHeatmap: () =>
    request<any>("/api/v1/analytics/classification-heatmap"),
  analyticsResolutionTimes: () =>
    request<any>("/api/v1/analytics/resolution-times"),
  analyticsSlaBreachRates: () =>
    request<any>("/api/v1/analytics/sla-breach-rates"),
  analyticsModelPerformance: () =>
    request<any>("/api/v1/analytics/model-performance"),
  analyticsFraudTrend: (days = 14) =>
    request<any[]>(`/api/v1/analytics/fraud-trend?days=${days}`),
  analyticsDepartmentPerformance: () =>
    request<any[]>("/api/v1/analytics/department-performance"),
  analyticsDepartmentDeepDive: () =>
    request<any[]>("/api/v1/analytics/department-deep-dive"),
  // Draft response
  generateDraftResponse: (caseId: string) =>
    request<any>(`/api/v1/cases/${encodeURIComponent(caseId)}/draft-response`, { method: "POST" }, 30000),
  // ML Feedback
  submitFeedback: (caseId: string, correctedClassification: string, note?: string) =>
    request<any>(`/api/v1/cases/${encodeURIComponent(caseId)}/feedback`, {
      method: "POST",
      headers,
      body: JSON.stringify({ corrected_classification: correctedClassification, note }),
    }),
  adminAssistant: (message: string, history: { role: string; content: string }[]) =>
    request<{ reply: string; context_used: any }>(
      "/api/v1/admin/assistant",
      { method: "POST", headers, body: JSON.stringify({ message, history }) },
      90000,
    ),
};

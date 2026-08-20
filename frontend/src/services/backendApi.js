import { optionalJson, requestJson } from "./apiClient.js";

function periodQuery(range) {
  const normalizedRange = range === "today" ? "day" : range || "week";
  return `period=${encodeURIComponent(normalizedRange)}`;
}

function rangeAndSignal(rangeOrSignal, signal) {
  if (typeof rangeOrSignal === "string") {
    return { range: rangeOrSignal, signal };
  }
  return { range: "week", signal: rangeOrSignal };
}

export const backendApi = {
  dashboardSummary(range, signal) {
    return requestJson(`/api/snoc/dashboard/summary?${periodQuery(range)}`, { signal });
  },
  dashboardTrends(range, signal) {
    return requestJson(`/api/snoc/dashboard/trends?${periodQuery(range)}`, { signal });
  },
  dashboardIntents(range, signal) {
    return requestJson(`/api/snoc/dashboard/intents?${periodQuery(range)}`, { signal });
  },
  dashboardRecent(range, signal) {
    return requestJson(`/api/snoc/dashboard/recent?${periodQuery(range)}`, { signal });
  },
  legacyDashboard(signal) {
    return optionalJson("/api/dashboard", { signal });
  },
  dqExecutive(signal) {
    return optionalJson("/api/snoc/dq/executive", { signal });
  },
  dqDimensions(signal) {
    return optionalJson("/api/snoc/dq/dimensions", { signal });
  },
  dqRules(signal) {
    return optionalJson("/api/snoc/dq/rules", { signal });
  },
  modelSnapshot(signal) {
    return optionalJson("/api/snoc/model/snapshot", { signal });
  },
  workflowHealth(signal) {
    return optionalJson("/api/snoc/workflow/health", { signal });
  },
  health(signal) {
    return optionalJson("/health/live", { signal });
  },
  runtime(signal) {
    return optionalJson("/api/snoc/frontend/runtime", { signal });
  },
  confidenceAnalytics(rangeOrSignal, signal) {
    const args = rangeAndSignal(rangeOrSignal, signal);
    return optionalJson(
      `/api/snoc/frontend/analytics/confidence?${periodQuery(args.range)}`,
      { signal: args.signal },
    );
  },
  missingEntityAnalytics(rangeOrSignal, signal) {
    const args = rangeAndSignal(rangeOrSignal, signal);
    return optionalJson(
      `/api/snoc/frontend/analytics/missing-entities?${periodQuery(args.range)}`,
      { signal: args.signal },
    );
  },
  executionAnalytics(rangeOrSignal, signal) {
    const args = rangeAndSignal(rangeOrSignal, signal);
    return optionalJson(
      `/api/snoc/frontend/analytics/executions?${periodQuery(args.range)}`,
      { signal: args.signal },
    );
  },
  requestTrace(publicReference, signal) {
    if (!publicReference) return Promise.resolve(null);
    return optionalJson(
      `/api/snoc/frontend/requests/${encodeURIComponent(publicReference)}/trace`,
      { signal },
    );
  },
  requestPipeline(publicReference, signal) {
    if (!publicReference) return Promise.resolve(null);
    return optionalJson(`/api/requests/${encodeURIComponent(publicReference)}/pipeline`, { signal });
  },
  listEscalations(signal) {
    return optionalJson("/api/escalations", { signal });
  },
  resolveEscalation(publicReference, decision, note = "") {
    return requestJson(`/api/escalations/${encodeURIComponent(publicReference)}/resolve`, {
      method: "POST",
      body: { decision, note },
    });
  },
  listWhitelist(signal) {
    return optionalJson("/api/whitelist", { signal });
  },
  addWhitelist(email, zone = "Unknown") {
    return requestJson("/api/whitelist", {
      method: "POST",
      body: { email, zone },
    });
  },
  removeWhitelist(email) {
    return requestJson(`/api/whitelist/${encodeURIComponent(email)}`, { method: "DELETE" });
  },
  listAccounts(signal) {
    return optionalJson("/api/accounts", { signal });
  },
  createAccount(payload) {
    return requestJson("/api/accounts", { method: "POST", body: payload });
  },
  updateAccount(username, payload) {
    return requestJson(`/api/accounts/${encodeURIComponent(username)}`, {
      method: "PUT",
      body: payload,
    });
  },
  deleteAccount(username) {
    return requestJson(`/api/accounts/${encodeURIComponent(username)}`, { method: "DELETE" });
  },
  toggleAccount(username) {
    return requestJson(`/api/accounts/${encodeURIComponent(username)}/toggle`, { method: "POST" });
  },
  toggleAgent() {
    return requestJson("/api/agent-toggle", { method: "POST" });
  },
  processInbox() {
    return requestJson("/api/simulate-inbox", { method: "POST" });
  },
};

export default backendApi;

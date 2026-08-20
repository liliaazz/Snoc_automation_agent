import { useCallback, useEffect, useMemo, useState } from "react";
import { DEMO_DASHBOARD } from "../data/mockData";
import backendApi from "../services/backendApi.js";
import adapters from "../services/backendAdapters.js";

const EMPTY_LIVE = {
  mode: "live",
  executionMode: "unknown",
  generatedAt: null,
  summary: {
    operational: {
      totalRequests: 0,
      successfulExecutions: 0,
      autoResolved: 0,
      escalated: 0,
      manualReview: 0,
      rejected: 0,
      pendingRequests: 0,
      inProgress: 0,
      failed: 0,
      unauthorized: 0,
      lowConfidence: 0,
    },
    dataQuality: null,
  },
  trends: [],
  intents: [],
  recent: [],
  auditLogs: [],
  alerts: [],
  escalations: [],
  operations: { hourly: [], actions: [] },
  dq: { executive: null, dimensions: [], rules: [] },
  model: null,
  workflow: [],
  runtime: null,
  confidenceAnalytics: null,
  missingEntityAnalytics: [],
  executionAnalytics: [],
  whitelist: [],
  accounts: [],
  agentActive: true,
};

function rejectedReason(result) {
  if (result.status !== "rejected") return null;
  const error = result.reason;
  return {
    status: error?.status ?? 0,
    message: error?.message || "Request failed",
  };
}

export function useDashboard({ range = "week" } = {}) {
  const [data, setData] = useState(DEMO_DASHBOARD);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [partialErrors, setPartialErrors] = useState([]);
  const [refreshKey, setRefreshKey] = useState(0);

  const refresh = useCallback(() => setRefreshKey((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    let mounted = true;

    async function load() {
      setLoading(true);
      setError("");

      const requests = [
        ["summary", () => backendApi.dashboardSummary(range, controller.signal)],
        ["trends", () => backendApi.dashboardTrends(range, controller.signal)],
        ["intents", () => backendApi.dashboardIntents(range, controller.signal)],
        ["recent", () => backendApi.dashboardRecent(range, controller.signal)],
        ["legacy", () => backendApi.legacyDashboard(controller.signal)],
        ["dqExecutive", () => backendApi.dqExecutive(controller.signal)],
        ["dqDimensions", () => backendApi.dqDimensions(controller.signal)],
        ["dqRules", () => backendApi.dqRules(controller.signal)],
        ["model", () => backendApi.modelSnapshot(controller.signal)],
        ["workflow", () => backendApi.workflowHealth(controller.signal)],
        ["health", () => backendApi.health(controller.signal)],
        ["runtime", () => backendApi.runtime(controller.signal)],
        ["confidence", () => backendApi.confidenceAnalytics(range, controller.signal)],
        ["missingEntities", () => backendApi.missingEntityAnalytics(range, controller.signal)],
        ["executions", () => backendApi.executionAnalytics(range, controller.signal)],
        ["escalations", () => backendApi.listEscalations(controller.signal)],
        ["whitelist", () => backendApi.listWhitelist(controller.signal)],
        ["accounts", () => backendApi.listAccounts(controller.signal)],
      ];

      const settled = await Promise.allSettled(requests.map(([, run]) => run()));
      if (!mounted) return;

      const payload = Object.fromEntries(
        requests.map(([name], index) => [
          name,
          settled[index].status === "fulfilled" ? settled[index].value : null,
        ]),
      );

      const failures = requests
        .map(([name], index) => {
          const reason = rejectedReason(settled[index]);
          return reason ? { name, ...reason } : null;
        })
        .filter(Boolean);
      setPartialErrors(failures);

      const hasLiveCore = Boolean(payload.summary || payload.recent || payload.legacy);
      if (!hasLiveCore) {
        setData(DEMO_DASHBOARD);
        setError("Backend dashboard endpoints are unavailable. Deterministic demo data is displayed.");
        setLoading(false);
        return;
      }

      const legacyRecent = payload.legacy?.requests ? { items: payload.legacy.requests } : null;
      const auditLogs = adapters.normalizeRecentRows(payload.recent || legacyRecent);
      const summary = adapters.normalizeSummary(payload.summary, payload.legacy);
      const trends = adapters.normalizeTrends(payload.trends);
      const intents = adapters.normalizeIntents(payload.intents);
      const executionAnalytics = adapters.normalizeExecutionAnalytics(payload.executions, auditLogs);
      const runtime = adapters.normalizeRuntime(payload.runtime, payload.health);
      const model = adapters.normalizeModel(payload.model);
      const alerts = adapters.normalizeAlerts(payload.legacy?.alerts || []);
      const escalations = adapters.normalizeEscalations(payload.escalations, auditLogs);

      setData({
        ...EMPTY_LIVE,
        mode: "live",
        executionMode: runtime.executionMode,
        generatedAt: summary.generatedAt,
        summary: {
          operational: summary.operational,
          dataQuality: summary.dataQuality,
        },
        trends,
        intents,
        recent: auditLogs,
        auditLogs,
        alerts,
        escalations,
        operations: adapters.deriveOperations(auditLogs, executionAnalytics),
        dq: adapters.normalizeDq(
          payload.dqExecutive,
          payload.dqDimensions,
          payload.dqRules,
          summary.dataQuality,
        ),
        model,
        workflow: adapters.normalizeWorkflow(payload.workflow),
        runtime,
        confidenceAnalytics: adapters.normalizeConfidenceAnalytics(payload.confidence, auditLogs),
        missingEntityAnalytics: adapters.normalizeMissingEntityAnalytics(payload.missingEntities, auditLogs),
        executionAnalytics,
        whitelist: adapters.normalizeWhitelist(payload.whitelist),
        accounts: adapters.normalizeAccounts(payload.accounts),
        agentActive: payload.legacy?.agent_active ?? payload.legacy?.agentActive ?? true,
      });
      setLoading(false);
    }

    load().catch((reason) => {
      if (!mounted || reason?.name === "AbortError") return;
      setData(DEMO_DASHBOARD);
      setError("Backend unavailable. Deterministic demo data is displayed.");
      setLoading(false);
    });

    return () => {
      mounted = false;
      controller.abort();
    };
  }, [range, refreshKey]);

  return useMemo(
    () => ({ data, loading, error, partialErrors, refresh }),
    [data, loading, error, partialErrors, refresh],
  );
}

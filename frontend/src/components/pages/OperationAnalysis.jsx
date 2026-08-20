import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApp } from "../../context/AppContext";
import { useTranslation } from "../../i18n/useTranslation";
import { OPERATIONS } from "../../data/mockData";
import { formatPercent } from "../../utils/formatters";
import {
  getConfidenceMetrics,
  getConfidenceBuckets,
  getProcessingMetrics,
  getApiMetrics,
  getClassificationVolume,
} from "../../utils/analytics";
import { ChartCard, KpiCard, EmptyState } from "../Primitives";
import PageTabs from "../common/PageTabs";

const OPERATION_KEYS = {
  "Create VPN": "op.createVpn",
  "Unlock Account": "op.unlockAccount",
  "OTP Change": "op.otpChange",
  "Password Reset": "op.passwordReset",
  Escalation: "op.escalation",
};

const TABS = [
  { id: "entityTime", label: "Entity & Time" },
  { id: "confidence", label: "Confidence" },
  { id: "performance", label: "Performance" },
  { id: "apiHealth", label: "API Health" },
  { id: "classification", label: "Classification Volume" },
];

const CONFIDENCE_BUCKET_LABELS = [
  { key: "na", label: "N/A" },
  { key: "below70", label: "Below 70%" },
  { key: "b70_80", label: "70–80%" },
  { key: "b80_90", label: "80–90%" },
  { key: "b90_95", label: "90–95%" },
  { key: "b95_100", label: "95–100%" },
];

export default function OperationAnalysis({ data }) {
  const { state } = useApp();
  const { t } = useTranslation();
  const records = data?.mode === "live" ? data.auditLogs || [] : state.auditLogs;
  const op = data.operations || { hourly: [], actions: [] };
  const entityRows = data?.missingEntityAnalytics?.length
    ? data.missingEntityAnalytics.map((row) => ({
        operation: row.action || row.rawAction || "Unknown",
        total: row.totalRequests ?? row.total_requests ?? 0,
        missingPdv: row.missingPdv ?? row.missing_pdv ?? 0,
        missingOtp: row.missingPhone ?? row.missing_phone ?? 0,
        missingPdvPercent: row.missingPdvPercent ?? row.missing_pdv_percent ?? null,
        missingOtpPercent: row.missingPhonePercent ?? row.missing_phone_percent ?? null,
        phoneApplicable: row.phoneApplicable ?? String(row.action || "").includes("OTP"),
      }))
    : [];

  const activeThreshold = useMemo(() => {
    const rule = state.rules.find((r) => r.name === "Low Confidence Escalation");
    return rule?.threshold ?? 85;
  }, [state.rules]);

  const confidenceMetrics = useMemo(
    () => getConfidenceMetrics(records, activeThreshold),
    [records, activeThreshold],
  );
  const confidenceBuckets = useMemo(() => getConfidenceBuckets(records), [records]);
  const processingMetrics = useMemo(() => getProcessingMetrics(records), [records]);
  const apiHealth = useMemo(() => {
    if (data?.executionAnalytics?.length) {
      return data.executionAnalytics.map((row) => ({
        operation: row.action,
        attempts: row.attempts,
        successful: row.succeeded,
        failed: row.failed,
        successRate: row.successRate,
        hasTelemetry: row.attempts > 0,
      }));
    }
    return OPERATIONS.filter((operation) => operation !== "Escalation").map((operation) =>
      getApiMetrics(records, operation),
    );
  }, [data?.executionAnalytics, records]);
  const classificationVolume = useMemo(() => getClassificationVolume(records), [records]);

  const confidenceChartData = CONFIDENCE_BUCKET_LABELS.map(({ key, label }) => ({ name: label, value: confidenceBuckets[key] }));

  return (
    <PageTabs tabs={TABS}>
      {(active) => (
        <>
          {active === "entityTime" ? (
            <>
              <h3 className="section-title">{t("op.entityTitle")}</h3>
              <div className="requests-card dashboard-card mb-6">
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>{t("op.operation")}</th>
                        <th>{t("op.totalReq")}</th>
                        <th>{t("op.missingPdv")}</th>
                        <th>{t("op.missingOtp")}</th>
                        <th>{t("op.pctPdv")}</th>
                        <th>{t("op.pctOtp")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {entityRows.map((row) => (
                        <tr key={row.operation}>
                          <td>{t(OPERATION_KEYS[row.operation] || row.operation)}</td>
                          <td>{row.total}</td>
                          <td>{row.missingPdv}</td>
                          <td>{row.missingOtp}</td>
                          <td>{row.missingPdvPercent == null ? formatPercent(row.missingPdv, row.total) : `${Number(row.missingPdvPercent).toFixed(1)}%`}</td>
                          <td>{row.phoneApplicable === false ? "N/A" : row.missingOtpPercent == null ? formatPercent(row.missingOtp, row.total) : `${Number(row.missingOtpPercent).toFixed(1)}%`}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <h3 className="section-title">{t("op.timeTitle")}</h3>
              <section className="two-chart-grid">
                <ChartCard title="Requests by hour" subtitle="Incoming SNOC workload">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={op.hourly}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} />
                      <XAxis dataKey="label" />
                      <YAxis />
                      <Tooltip />
                      <Bar dataKey="value" fill="#DA291C" radius={[5, 5, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </ChartCard>
                <ChartCard title="API actions" subtitle="Successful and failed deterministic routes">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={op.actions}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} />
                      <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                      <YAxis />
                      <Tooltip />
                      <Legend />
                      <Bar dataKey="success" fill="#4caf50" radius={[5, 5, 0, 0]} />
                      <Bar dataKey="failed" fill="#f44336" radius={[5, 5, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </ChartCard>
              </section>
            </>
          ) : null}

          {active === "confidence" ? (
            <>
              <section className="four-kpi-grid">
                <KpiCard
                  value={confidenceMetrics.average === null ? "N/A" : Math.round(confidenceMetrics.average)}
                  suffix={confidenceMetrics.average === null ? "" : "%"}
                  label="Average measured confidence"
                />
                <KpiCard value={confidenceMetrics.lowConfidenceCount} label="Low-confidence predictions" />
                <KpiCard value={confidenceMetrics.unmeasuredCount} label="Unmeasured confidence count" />
                <KpiCard value={activeThreshold} suffix="%" label="Active decision threshold" />
              </section>
              <ChartCard title="Confidence Distribution" subtitle="N/A shown separately from below-70% predictions">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={confidenceChartData}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                    <YAxis allowDecimals={false} />
                    <Tooltip />
                    <Bar dataKey="value" fill="#2563eb" radius={[5, 5, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </ChartCard>
            </>
          ) : null}

          {active === "performance" ? (
            <section className="four-kpi-grid">
              <KpiCard
                value={processingMetrics.successRate === null ? "N/A" : (processingMetrics.successRate * 100).toFixed(1)}
                suffix={processingMetrics.successRate === null ? "" : "%"}
                label="Success rate"
              />
              <KpiCard
                value={processingMetrics.failureRate === null ? "N/A" : (processingMetrics.failureRate * 100).toFixed(1)}
                suffix={processingMetrics.failureRate === null ? "" : "%"}
                label="Failure rate"
              />
              <KpiCard
                value={processingMetrics.automationRate === null ? "N/A" : (processingMetrics.automationRate * 100).toFixed(1)}
                suffix={processingMetrics.automationRate === null ? "" : "%"}
                label="Automation rate"
              />
              <KpiCard
                value={processingMetrics.escalationRate === null ? "N/A" : (processingMetrics.escalationRate * 100).toFixed(1)}
                suffix={processingMetrics.escalationRate === null ? "" : "%"}
                label="Escalation rate"
              />
            </section>
          ) : null}

          {active === "apiHealth" ? (
            <div className="requests-card dashboard-card">
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Operation</th>
                      <th>Attempts</th>
                      <th>Successful</th>
                      <th>Failed</th>
                      <th>Success Rate</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {apiHealth.map((row) => (
                      <tr key={row.operation}>
                        <td>{row.operation}</td>
                        <td>{row.attempts}</td>
                        <td>{row.successful}</td>
                        <td>{row.failed}</td>
                        <td>{row.successRate === null ? "N/A" : `${(row.successRate * 100).toFixed(1)}%`}</td>
                        <td>{row.hasTelemetry ? "Telemetry available" : "No execution telemetry available"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}

          {active === "classification" ? (
            <>
              <section className="four-kpi-grid">
                <KpiCard value={classificationVolume.total} label="Total classified" />
                <KpiCard value={classificationVolume.unresolved} label="Unresolved / unknown classifications" />
                <KpiCard value={classificationVolume.escalatedOrClarify} label="Escalated / requires clarification" />
                <KpiCard value={Object.keys(classificationVolume.byIntent).length} label="Distinct intents" />
              </section>
              <ChartCard title="Classification Volume by Intent" subtitle="Derived from current audit records">
                {Object.keys(classificationVolume.byIntent).length ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={Object.entries(classificationVolume.byIntent).map(([name, value]) => ({ name, value }))}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} />
                      <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                      <YAxis allowDecimals={false} />
                      <Tooltip />
                      <Bar dataKey="value" fill="#ea8b00" radius={[5, 5, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <EmptyState title="No classifications" description="No classified requests are available yet." />
                )}
              </ChartCard>
            </>
          ) : null}
        </>
      )}
    </PageTabs>
  );
}

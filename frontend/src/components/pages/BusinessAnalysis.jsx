import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useApp } from "../../context/AppContext";
import { useTranslation } from "../../i18n/useTranslation";
import { countByProcessingStatus } from "../../utils/statusConstants";
import {
  getVolumeByPeriod,
  getPeakHours,
  getSupportHeatmap,
  getUnstablePos,
  getMissingEntityAnalysis,
  getIntentCounts,
} from "../../utils/analytics";
import { getBusinessImpact, IMPACT_FORMULAS } from "../../utils/businessImpact";
import { ChartCard, EmptyState, KpiCard } from "../Primitives";
import PageTabs from "../common/PageTabs";
import Heatmap from "../common/Heatmap";
import MetricInfoTooltip from "../common/MetricInfoTooltip";

const RATE_COLORS = { Resolved: "#4caf50", Escalated: "#ea8b00", Rejected: "#f44336", Unauthorized: "#7e22ce" };
const REGION_COLORS = ["#2563eb", "#4caf50", "#ea8b00", "#f44336"];

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "time", label: "Time Analytics" },
  { id: "classes", label: "Classes & Missing Entities" },
  { id: "impact", label: "Business Impact" },
];

const PERIODS = [
  { id: "hour", label: "Per Hour" },
  { id: "day", label: "Per Day" },
  { id: "month", label: "Per Month" },
  { id: "year", label: "Per Year" },
];

export default function BusinessAnalysis({ data }) {
  const { state } = useApp();
  const { t } = useTranslation();
  const [period, setPeriod] = useState("day");
  const records = data?.mode === "live" ? data.auditLogs || [] : state.auditLogs;

  const counts = useMemo(() => countByProcessingStatus(records), [records]);

  const rateData = [
    { name: "Resolved", value: counts.resolved },
    { name: "Escalated", value: counts.escalated },
    { name: "Rejected", value: counts.rejected },
    { name: "Unauthorized", value: counts.unauthorized },
  ];

  const intentData = useMemo(() => {
    const byIntent = {};
    records.forEach((row) => {
      byIntent[row.intent] = (byIntent[row.intent] || 0) + 1;
    });
    return Object.entries(byIntent).map(([name, value], index) => ({
      name,
      value,
      color: data?.intents?.[index]?.color || REGION_COLORS[index % REGION_COLORS.length],
    }));
  }, [records, data]);

  const regionData = useMemo(() => {
    const byRegion = {};
    records.forEach((row) => {
      byRegion[row.region] = (byRegion[row.region] || 0) + 1;
    });
    return Object.entries(byRegion).map(([name, value]) => ({ name, value }));
  }, [records]);

  const stateDistribution = rateData;

  const rankedRegions = useMemo(() => {
    const total = records.length || 1;
    return [...regionData]
      .sort((a, b) => b.value - a.value)
      .map((r, index) => ({ ...r, rank: index + 1, pct: (r.value / total) * 100 }));
  }, [regionData, records]);

  const unstablePos = useMemo(() => getUnstablePos(records), [records]);

  const volumeSeries = useMemo(() => getVolumeByPeriod(records, period), [records, period]);
  const peakHours = useMemo(() => getPeakHours(records), [records]);
  const heatmap = useMemo(() => getSupportHeatmap(records), [records]);
  const missingAnalysis = useMemo(() => getMissingEntityAnalysis(records), [records]);

  const entitySummary = useMemo(() => {
    const pdvExtracted = records.filter((r) => r.posCode).length;
    const otpRequests = records.filter((r) => r.intent === "OTP Change").length;
    const missingRequired = missingAnalysis.missingPdv + missingAnalysis.missingOtp;
    return { pdvExtracted, otpRequests, missingRequired };
  }, [records, missingAnalysis]);

  const classDistribution = useMemo(() => getIntentCounts(records), [records]);

  const impact = useMemo(() => getBusinessImpact(records), [records]);

  const trends = data?.trends || [];

  return (
    <PageTabs tabs={TABS}>
      {(active) => (
        <>
          {active === "overview" ? (
            <>
              <section className="two-chart-grid mb-3">
                <ChartCard title="Email Volume (7 days)" subtitle="Received requests by day">
                  {trends.length ? (
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={trends} margin={{ top: 12, right: 12, left: -16, bottom: 0 }}>
                        <defs>
                          <linearGradient id="volumeFill" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stopColor="#2563eb" stopOpacity={0.3} />
                            <stop offset="100%" stopColor="#2563eb" stopOpacity={0.05} />
                          </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
                        <XAxis dataKey="label" stroke="#999" tick={{ fill: "#8a8f95", fontSize: 12 }} />
                        <YAxis stroke="#999" tick={{ fill: "#8a8f95", fontSize: 12 }} />
                        <Tooltip />
                        <Area type="monotone" dataKey="received" stroke="#2563eb" strokeWidth={2} fill="url(#volumeFill)" />
                      </AreaChart>
                    </ResponsiveContainer>
                  ) : (
                    <EmptyState title="No volume data" description="Email volume trend is unavailable for this range." />
                  )}
                </ChartCard>

                <ChartCard title="Resolution / Escalation / Rejection Rate" subtitle="Outcome distribution across all processed requests">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={rateData}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} />
                      <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                      <YAxis allowDecimals={false} />
                      <Tooltip />
                      <Bar dataKey="value" radius={[5, 5, 0, 0]}>
                        {rateData.map((entry) => (
                          <Cell key={entry.name} fill={RATE_COLORS[entry.name]} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </ChartCard>
              </section>

              <section className="two-chart-grid mb-3">
                <ChartCard title={t("table.intent")} subtitle="Requests grouped by classified intent">
                  {intentData.length ? (
                    <ResponsiveContainer width="100%" height="100%">
                      <PieChart>
                        <Pie data={intentData} dataKey="value" nameKey="name" innerRadius="55%" outerRadius="80%" paddingAngle={2}>
                          {intentData.map((item) => (
                            <Cell key={item.name} fill={item.color} />
                          ))}
                        </Pie>
                        <Tooltip />
                      </PieChart>
                    </ResponsiveContainer>
                  ) : (
                    <EmptyState title="No intent data" description="No classified requests are available yet." />
                  )}
                </ChartCard>

                <ChartCard title="Regional Distribution" subtitle="Requests grouped by originating zone">
                  {regionData.length ? (
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={regionData} layout="vertical">
                        <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                        <XAxis type="number" allowDecimals={false} />
                        <YAxis type="category" dataKey="name" width={110} tick={{ fontSize: 11 }} />
                        <Tooltip />
                        <Bar dataKey="value" fill="#ea8b00" radius={[0, 6, 6, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  ) : (
                    <EmptyState title="No regional data" description="Regional breakdown is unavailable." />
                  )}
                </ChartCard>
              </section>

              <section className="two-chart-grid mb-3">
                <ChartCard title="State Distribution" subtitle="Automatically resolved, escalated, rejected, unauthorized">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={stateDistribution} dataKey="value" nameKey="name" innerRadius="55%" outerRadius="80%" paddingAngle={2}>
                        {stateDistribution.map((entry) => (
                          <Cell key={entry.name} fill={RATE_COLORS[entry.name]} />
                        ))}
                      </Pie>
                      <Tooltip />
                    </PieChart>
                  </ResponsiveContainer>
                </ChartCard>

                <ChartCard title="Ranked Regional Traffic" subtitle="Regions ordered by request volume">
                  <div className="flex h-full flex-col justify-center gap-3">
                    {rankedRegions.map((r) => (
                      <div key={r.name} className="flex items-center gap-3">
                        <span className="w-5 text-xs font-semibold text-gray-400">#{r.rank}</span>
                        <span className="w-28 shrink-0 truncate text-xs text-gray-700">{r.name}</span>
                        <div className="h-2 flex-1 overflow-hidden rounded-full bg-gray-200">
                          <div className="h-full rounded-full bg-[#ea8b00]" style={{ width: `${r.pct}%` }} />
                        </div>
                        <span className="w-16 shrink-0 text-right text-[11px] text-gray-500">
                          {r.value} ({r.pct.toFixed(0)}%)
                        </span>
                      </div>
                    ))}
                  </div>
                </ChartCard>
              </section>

              <ChartCard title="Top Unstable POS" subtitle="Points of sale with repeated incidents" className="min-h-[240px]">
                {unstablePos.length === 0 ? (
                  <EmptyState title="No repeated incidents" description="No PDV code has more than one recorded incident yet." />
                ) : (
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>PDV Code</th>
                          <th>Incidents</th>
                          <th>Region</th>
                          <th>Most common intent/status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {unstablePos.map((row) => (
                          <tr key={row.posCode}>
                            <td className="mono-cell">{row.posCode}</td>
                            <td>{row.count}</td>
                            <td>{row.region}</td>
                            <td>
                              {row.topIntent} / {row.topStatus}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </ChartCard>
            </>
          ) : null}

          {active === "time" ? (
            <>
              <div className="mb-4 flex flex-wrap gap-2">
                {PERIODS.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => setPeriod(p.id)}
                    aria-pressed={period === p.id}
                    className={`rounded-full px-3 py-1.5 text-xs font-medium transition ${
                      period === p.id ? "bg-black text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                    }`}
                  >
                    {p.label}
                  </button>
                ))}
              </div>

              <ChartCard title="Request Volume" subtitle={`Grouped ${period}ly, derived from record timestamps`} className="mb-3">
                {volumeSeries.length ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={volumeSeries}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} />
                      <XAxis dataKey="label" tick={{ fontSize: 10 }} />
                      <YAxis allowDecimals={false} />
                      <Tooltip />
                      <Bar dataKey="value" fill="#2563eb" radius={[5, 5, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <EmptyState title="No timestamped requests" description="No records available for this period." />
                )}
              </ChartCard>

              <section className="two-chart-grid mb-3">
                <ChartCard title="Peak Support Hours" subtitle="Top three hours by volume">
                  {peakHours.length === 0 ? (
                    <EmptyState title="No peak data" description="Not enough timestamped requests." />
                  ) : (
                    <div className="flex h-full flex-col justify-center gap-3">
                      {peakHours.map((h, index) => (
                        <div key={h.label} className="flex items-center justify-between rounded-xl bg-white p-3 shadow-sm">
                          <span className="text-xs font-semibold text-gray-500">#{index + 1} — {h.label}</span>
                          <span className="font-outfit text-lg font-bold">{h.value}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </ChartCard>

                <ChartCard title="Entity Extraction Summary" subtitle="Derived from current audit records">
                  <div className="grid h-full grid-cols-3 gap-3">
                    <MiniStat label="PDV codes extracted" value={entitySummary.pdvExtracted} />
                    <MiniStat label="OTP-related requests" value={entitySummary.otpRequests} />
                    <MiniStat label="Missing required entities" value={entitySummary.missingRequired} />
                  </div>
                </ChartCard>
              </section>

              <ChartCard title="Support Load Heatmap" subtitle="Requests by day of week and hour" className="min-h-[260px]">
                <Heatmap grid={heatmap.grid} dayLabels={heatmap.dayLabels} hasData={heatmap.hasData} />
              </ChartCard>
            </>
          ) : null}

          {active === "classes" ? (
            <>
              <ChartCard title="Distribution of Classes" subtitle="Requests grouped by intent" className="mb-3">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={Object.entries(classDistribution).map(([name, value]) => ({ name, value }))}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                    <YAxis allowDecimals={false} />
                    <Tooltip />
                    <Bar dataKey="value" fill="#DA291C" radius={[5, 5, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </ChartCard>

              <ChartCard title="Missing Entities — By Region & Sender" subtitle="Grouped by request type" className="mb-3">
                {missingAnalysis.rows.length === 0 ? (
                  <EmptyState title="Nothing missing" description="No records are missing required entities." />
                ) : (
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>Request Type</th>
                          <th>Missing Count</th>
                          <th>Top Affected Region</th>
                          <th>Top Affected Sender</th>
                        </tr>
                      </thead>
                      <tbody>
                        {missingAnalysis.rows.map((row) => (
                          <tr key={row.type}>
                            <td>{row.type}</td>
                            <td>{row.missingCount}</td>
                            <td>{row.topRegion}</td>
                            <td>{row.topSender}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </ChartCard>

              <section className="four-kpi-grid">
                <KpiCard value={missingAnalysis.total} label="Total classified requests" />
                <KpiCard
                  value={missingAnalysis.missingPdvRate === null ? "N/A" : `${(missingAnalysis.missingPdvRate * 100).toFixed(1)}%`}
                  label="Missing PDV rate"
                />
                <KpiCard
                  value={missingAnalysis.missingOtpRate === null ? "N/A" : `${(missingAnalysis.missingOtpRate * 100).toFixed(1)}%`}
                  label="Missing OTP rate"
                />
                <KpiCard value={missingAnalysis.missingBoth} label="Missing both PDV and OTP" />
              </section>
            </>
          ) : null}

          {active === "impact" ? (
            <section className="four-kpi-grid">
              <div className="relative">
                <KpiCard value={impact.manualOperationsAvoided} label="Manual operations avoided" />
                <div className="absolute right-3 top-3">
                  <MetricInfoTooltip text={IMPACT_FORMULAS.manualOperationsAvoided} />
                </div>
              </div>
              <div className="relative">
                <KpiCard
                  value={impact.estimatedHoursSaved === null ? "N/A" : impact.estimatedHoursSaved.toFixed(1)}
                  suffix={impact.estimatedHoursSaved === null ? "" : "h"}
                  label="Estimated hours saved"
                />
                <div className="absolute right-3 top-3">
                  <MetricInfoTooltip text={IMPACT_FORMULAS.estimatedHoursSaved} />
                </div>
              </div>
              <div className="relative">
                <KpiCard
                  value={impact.automationRate === null ? "N/A" : (impact.automationRate * 100).toFixed(1)}
                  suffix={impact.automationRate === null ? "" : "%"}
                  label="Automation rate"
                />
                <div className="absolute right-3 top-3">
                  <MetricInfoTooltip text={IMPACT_FORMULAS.automationRate} />
                </div>
              </div>
              <div className="relative">
                <KpiCard
                  value={impact.estimatedFinancialGain === null ? "N/A" : impact.estimatedFinancialGain}
                  label={impact.estimatedFinancialGain === null ? "Estimated financial gain — cost assumption not configured" : "Estimated financial gain"}
                />
                <div className="absolute right-3 top-3">
                  <MetricInfoTooltip text={IMPACT_FORMULAS.estimatedFinancialGain} />
                </div>
              </div>
              <KpiCard value={impact.escalationsRequiringHumanWork} label="Escalations requiring human work" />
            </section>
          ) : null}
        </>
      )}
    </PageTabs>
  );
}

function MiniStat({ label, value }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl bg-white p-3 text-center shadow-sm">
      <span className="font-outfit text-xl font-bold">{value}</span>
      <span className="mt-1 text-[10px] text-gray-500">{label}</span>
    </div>
  );
}

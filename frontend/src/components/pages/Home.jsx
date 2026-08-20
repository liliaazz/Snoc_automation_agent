import { AlertTriangle, CircleAlert, Info, Mail, MailCheck, MailX, ShieldAlert, TriangleAlert } from "lucide-react";
import { useApp } from "../../context/AppContext";
import { useTranslation } from "../../i18n/useTranslation";
import SeverityBadge from "../common/SeverityBadge";

const KPI_ICONS = {
  total: Mail,
  resolved: MailCheck,
  escalated: TriangleAlert,
  rejected: MailX,
  unauthorized: ShieldAlert,
};

const SEVERITY_ICONS = { Critical: CircleAlert, Warning: AlertTriangle, Information: Info };

export default function Home({ data, onNavigate }) {
  const { state } = useApp();
  const { t } = useTranslation();
  const operational = data?.summary?.operational || {};
  const alerts = [...(data?.alerts || []), ...(state.alerts || [])];

  const kpis = [
    { key: "total", label: t("kpi.total"), value: operational.totalRequests ?? 0 },
    {
      key: "resolved",
      label: t("kpi.resolved"),
      value: operational.successfulExecutions ?? operational.autoResolved ?? 0,
    },
    { key: "escalated", label: t("kpi.escalated"), value: operational.escalated ?? operational.manualReview ?? 0 },
    { key: "rejected", label: t("kpi.rejected"), value: operational.rejected ?? 0 },
    { key: "unauthorized", label: t("kpi.unauthorized"), value: operational.unauthorized ?? 0 },
  ];

  const firstName = (state.currentUser.name || "").split(" ")[0] || state.currentUser.username;

  function goToKpi(key) {
    if (key === "escalated") {
      onNavigate?.("emails");
      return;
    }
    if (key === "total") {
      onNavigate?.("audit");
      return;
    }
    const statusByKey = {
      resolved: "Automatically Resolved",
      rejected: "Rejected",
      unauthorized: "Unauthorized",
    };
    onNavigate?.("audit", { status: statusByKey[key] });
  }

  return (
    <>
      <div className="mb-6 rounded-2xl bg-white p-10 shadow-2xl">
        <h2 className="font-oxanium text-xl font-semibold sm:text-2xl">
          {t("home.welcome")}, {firstName}
        </h2>
        <p className="mt-1 text-xs text-[#777] sm:text-sm">
          {data?.generatedAt ? new Date(data.generatedAt).toLocaleDateString() : ""}
        </p>
      </div>

      <h3 className="section-title">{t("home.snapshot")}</h3>
      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {kpis.map((kpi) => {
          const Icon = KPI_ICONS[kpi.key];
          return (
            <button
              type="button"
              key={kpi.key}
              onClick={() => goToKpi(kpi.key)}
              className="flex flex-col items-center justify-center gap-2 rounded-2xl bg-white p-5 text-center shadow-xl transition hover:shadow-2xl focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-400"
            >
              <Icon size={20} className="text-[#DA291C]" aria-hidden="true" />
              <div className="font-outfit text-3xl font-bold">{kpi.value}</div>
              <div className="text-xs text-[#757575]">{kpi.label}</div>
            </button>
          );
        })}
      </div>

      <div className="mb-6 flex justify-end">
        <button
          type="button"
          onClick={() => onNavigate?.("opanalysis")}
          className="text-xs font-medium text-[#2563eb] hover:underline"
        >
          {t("home.viewOpSummary")}
        </button>
      </div>

      <h3 className="section-title">{t("home.alerts")}</h3>
      {alerts.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-[#cdd2d8] bg-white p-6 text-center text-xs text-gray-500">
          {t("home.noAlerts")}
        </div>
      ) : (
        <ul className="flex flex-col gap-2">
          {alerts.map((alert) => {
            const Icon = SEVERITY_ICONS[alert.severity] || Info;
            return (
              <li key={alert.id}>
                <button
                  type="button"
                  onClick={() => onNavigate?.(alert.target)}
                  className="flex w-full items-start gap-3 rounded-2xl bg-white p-4 text-left shadow-md transition hover:shadow-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-400"
                >
                  <Icon size={18} className="mt-0.5 shrink-0 text-[#ad6800]" aria-hidden="true" />
                  <div className="flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <SeverityBadge value={alert.severity} />
                      <span className="text-xs font-semibold text-gray-500">{alert.category}</span>
                      <span className="font-outfit text-sm font-semibold text-gray-900">{alert.title}</span>
                    </div>
                    <p className="mt-1 text-xs text-gray-600">{alert.description}</p>
                    <div className="mt-2 flex gap-3 text-[11px] text-gray-400">
                      <span>{alert.time}</span>
                      <span>{alert.status}</span>
                    </div>
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}

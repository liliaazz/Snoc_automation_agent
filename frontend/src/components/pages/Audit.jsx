import { useEffect, useMemo, useState } from "react";
import { useApp } from "../../context/AppContext";
import { useTranslation } from "../../i18n/useTranslation";
import { PROCESSING_STATUSES, RULE_ACTIONS } from "../../utils/statusConstants";
import { formatConfidence, formatDateTime } from "../../utils/formatters";
import { REGIONS, INTENT_NAMES, AGENTS } from "../../data/mockData";
import AuditDetailsDrawer from "../common/AuditDetailsDrawer";
import EmailViewer from "../common/EmailViewer";
import FilterSelect from "../common/FilterSelect";
import SearchInput from "../common/SearchInput";
import StatusBadge from "../common/StatusBadge";
import ViewDetailsButton from "../common/ViewDetailsButton";
import ViewEmailButton from "../common/ViewEmailButton";

const EMPTY_FILTERS = { from: "", to: "", region: "", intent: "", status: "", decision: "", sender: "", agent: "" };
const QUICK_INTENTS = ["All Requests", "Locked Account", "OTP Change", "Create VPN", "Password Reset", "Escalated", "Irrelevant"];
const QUICK_INTENT_KEYS = {
  "All Requests": "quick.allRequests",
  "Locked Account": "quick.lockedAccount",
  "OTP Change": "quick.otpChange",
  "Create VPN": "quick.createVpn",
  "Password Reset": "quick.passwordReset",
  Escalated: "quick.escalated",
  Irrelevant: "quick.irrelevant",
};

export default function Audit({ params, data }) {
  const { state } = useApp();
  const { t, lang } = useTranslation();
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [quickIntent, setQuickIntent] = useState("All Requests");
  const [viewEmail, setViewEmail] = useState(null);
  const [viewDetails, setViewDetails] = useState(null);
  const [showOptionalColumns, setShowOptionalColumns] = useState(false);
  const sourceRows = data?.mode === "live" ? data.auditLogs || [] : state.auditLogs;

  // Arriving here from a KPI card / alert (Home) or another page can carry
  // a preset status filter — apply it and reset everything else so the
  // landing view matches exactly what was clicked.
  useEffect(() => {
    if (params?.status) {
      setFilters({ ...EMPTY_FILTERS, status: params.status });
      setQuickIntent("All Requests");
      setQuery("");
    }
  }, [params]);

  function clearFilters() {
    setFilters(EMPTY_FILTERS);
    setQuickIntent("All Requests");
    setQuery("");
  }

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return sourceRows.filter((row) => {
      const matchesQuery =
        !q ||
        [row.emailId, row.sender, row.region, row.subject, row.intent, row.status, row.decision, row.user, row.agent]
          .filter(Boolean)
          .join(" ")
          .toLowerCase()
          .includes(q);
      const rowDate = row.timestamp ? row.timestamp.slice(0, 10) : "";
      const matchesFrom = !filters.from || rowDate >= filters.from;
      const matchesTo = !filters.to || rowDate <= filters.to;
      const matchesRegion = !filters.region || row.region === filters.region;
      const matchesIntent = !filters.intent || row.intent === filters.intent;
      const matchesStatus = !filters.status || row.status === filters.status;
      const matchesDecision = !filters.decision || row.decision === filters.decision;
      const matchesSender = !filters.sender || (row.sender || "").toLowerCase().includes(filters.sender.toLowerCase());
      const matchesAgent = !filters.agent || row.agent === filters.agent;
      const matchesQuickIntent =
        quickIntent === "All Requests" ||
        (quickIntent === "Escalated" ? row.status === "Escalated" : quickIntent === "Irrelevant" ? row.intent === "Unknown" : row.intent === quickIntent);
      return (
        matchesQuery &&
        matchesFrom &&
        matchesTo &&
        matchesRegion &&
        matchesIntent &&
        matchesStatus &&
        matchesDecision &&
        matchesSender &&
        matchesAgent &&
        matchesQuickIntent
      );
    });
  }, [sourceRows, query, filters, quickIntent]);

  function exportCsv() {
    const columns = ["timestamp", "emailId", "sender", "region", "intent", "confidence", "decision", "status", "user", "agent"];
    const csv = [
      columns.join(","),
      ...rows.map((row) => columns.map((key) => JSON.stringify(row[key] ?? "")).join(",")),
    ].join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "snoc-audit.csv";
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <>
      <div className="rounded-lg bg-gray-100 shadow-2xl">
        <div className="table-title-row flex-col items-stretch gap-3 sm:flex-row sm:items-center">
          <div>
            <h2 className="font-outfit text-lg font-semibold">{t("audit.title")}</h2>
            <p className="mt-1 text-xs text-gray-500">{t("audit.subtitle")}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={clearFilters} className="primary-button bg-white text-gray-700 shadow-sm">
              {t("btn.clearFilters")}
            </button>
            <button type="button" onClick={exportCsv} className="primary-button">
              {t("btn.exportCsv")}
            </button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 px-6 pt-4">
          {QUICK_INTENTS.map((label) => (
            <button
              key={label}
              type="button"
              onClick={() => setQuickIntent(label)}
              aria-pressed={quickIntent === label}
              className={`rounded-full px-3 py-1.5 text-xs font-medium transition ${
                quickIntent === label ? "bg-black text-white" : "bg-white text-gray-600 hover:bg-gray-100"
              }`}
            >
              {t(QUICK_INTENT_KEYS[label])}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2 px-6 py-4">
          <SearchInput value={query} onChange={setQuery} placeholder={t("search.placeholder")} />
          <input
            type="date"
            value={filters.from}
            onChange={(event) => setFilters((prev) => ({ ...prev, from: event.target.value }))}
            aria-label={t("filter.dateRange")}
            className="rounded-md border border-gray-300 px-3 py-2 text-xs text-gray-700 sm:text-sm"
          />
          <input
            type="date"
            value={filters.to}
            onChange={(event) => setFilters((prev) => ({ ...prev, to: event.target.value }))}
            aria-label={t("filter.dateRange")}
            className="rounded-md border border-gray-300 px-3 py-2 text-xs text-gray-700 sm:text-sm"
          />
          <FilterSelect
            value={filters.region}
            onChange={(v) => setFilters((prev) => ({ ...prev, region: v }))}
            options={REGIONS}
            allLabel={`${t("filter.region")}: ${t("filter.all")}`}
            ariaLabel={t("filter.region")}
          />
          <FilterSelect
            value={filters.intent}
            onChange={(v) => setFilters((prev) => ({ ...prev, intent: v }))}
            options={INTENT_NAMES}
            allLabel={`${t("filter.intent")}: ${t("filter.all")}`}
            ariaLabel={t("filter.intent")}
          />
          <FilterSelect
            value={filters.status}
            onChange={(v) => setFilters((prev) => ({ ...prev, status: v }))}
            options={PROCESSING_STATUSES}
            allLabel={`${t("filter.status")}: ${t("filter.all")}`}
            ariaLabel={t("filter.status")}
          />
          <FilterSelect
            value={filters.decision}
            onChange={(v) => setFilters((prev) => ({ ...prev, decision: v }))}
            options={RULE_ACTIONS}
            allLabel={`${t("filter.decision")}: ${t("filter.all")}`}
            ariaLabel={t("filter.decision")}
          />
          <FilterSelect
            value={filters.agent}
            onChange={(v) => setFilters((prev) => ({ ...prev, agent: v }))}
            options={AGENTS}
            allLabel={`${t("filter.agent")}: ${t("filter.all")}`}
            ariaLabel={t("filter.agent")}
          />
          <input
            type="text"
            value={filters.sender}
            onChange={(event) => setFilters((prev) => ({ ...prev, sender: event.target.value }))}
            placeholder={t("filter.sender")}
            aria-label={t("filter.sender")}
            className="rounded-md border border-gray-300 px-3 py-2 text-xs text-gray-700 sm:text-sm"
          />
          <button
            type="button"
            onClick={() => setShowOptionalColumns((v) => !v)}
            aria-pressed={showOptionalColumns}
            className="primary-button bg-white text-gray-700 shadow-sm"
          >
            {showOptionalColumns ? t("btn.hideExtraColumns") : t("btn.showExtraColumns")}
          </button>
        </div>

        <p className="px-6 pb-2 text-xs text-gray-500">
          {t("audit.showingRecords").replace("{shown}", rows.length).replace("{total}", sourceRows.length)}
        </p>

        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{t("table.timestamp")}</th>
                <th>{t("table.emailId")}</th>
                <th>{t("table.sender")}</th>
                <th>{t("table.region")}</th>
                <th>{t("table.intent")}</th>
                <th>{t("table.confidence")}</th>
                <th>{t("table.decision")}</th>
                <th>{t("table.status")}</th>
                <th>{t("table.user")}</th>
                {showOptionalColumns ? (
                  <>
                    <th>{t("table.pdvCode")}</th>
                    <th>{t("table.executionTime")}</th>
                    <th>{t("table.stoppedAtStep")}</th>
                  </>
                ) : null}
                <th>{t("table.action")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={showOptionalColumns ? 13 : 10}>
                    <div className="p-8 text-center text-xs text-gray-500">{t("empty.noResults")}</div>
                  </td>
                </tr>
              ) : (
                rows.map((row) => (
                  <tr key={row.id}>
                    <td>{formatDateTime(row.timestamp, lang)}</td>
                    <td className="mono-cell">{row.emailId}</td>
                    <td>{row.sender}</td>
                    <td>{row.region}</td>
                    <td>{row.intent}</td>
                    <td>{formatConfidence(row.confidence)}</td>
                    <td>{row.decision}</td>
                    <td>
                      <StatusBadge value={row.status} />
                    </td>
                    <td>{row.user}</td>
                    {showOptionalColumns ? (
                      <>
                        <td className="mono-cell">{row.posCode || "—"}</td>
                        <td>{row.processingDurationMs ? `${row.processingDurationMs} ms` : "N/A"}</td>
                        <td>{row.status === "Escalated" || row.status === "Rejected" || row.status === "Unauthorized" ? row.agent : "—"}</td>
                      </>
                    ) : null}
                    <td>
                      <div className="flex items-center gap-1">
                        <ViewEmailButton onClick={() => setViewEmail(row)} />
                        <ViewDetailsButton onClick={() => setViewDetails(row)} />
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {viewEmail ? <EmailViewer record={viewEmail} onClose={() => setViewEmail(null)} /> : null}
      <AuditDetailsDrawer record={viewDetails} onClose={() => setViewDetails(null)} />
    </>
  );
}

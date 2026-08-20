import { useMemo, useState } from "react";
import { useApp } from "../../context/AppContext";
import { useTranslation } from "../../i18n/useTranslation";
import { escalationService } from "../../services/escalationService";
import { formatConfidence, formatDateTime } from "../../utils/formatters";
import { countByTreatment, TREATMENT_STATUSES } from "../../utils/statusConstants";
import EmailViewer from "../common/EmailViewer";
import FormModal, { FormField, inputClass } from "../common/FormModal";
import StatusBadge from "../common/StatusBadge";
import ViewEmailButton from "../common/ViewEmailButton";
import SearchInput from "../common/SearchInput";

const QUICK_FILTERS = ["All", "Pending", "Not Yet Treated", "Treated", "Canceled"];
const QUICK_FILTER_KEYS = {
  All: "quick.all",
  Pending: "quick.pending",
  "Not Yet Treated": "quick.notYetTreated",
  Treated: "quick.treated",
  Canceled: "quick.canceled",
};
const SORT_OPTIONS = [
  { value: "newest", key: "emails.sortNewest" },
  { value: "oldest", key: "emails.sortOldest" },
  { value: "confidence", key: "emails.sortConfidence" },
  { value: "treatment", key: "emails.sortTreatment" },
];

export default function Emails({ data, onRefresh }) {
  const { state, dispatch, pushToast } = useApp();
  const { t, lang } = useTranslation();
  const [viewEmail, setViewEmail] = useState(null);
  const [editing, setEditing] = useState(null); // escalation being updated
  const [form, setForm] = useState({ status: "Pending", note: "" });
  const [saving, setSaving] = useState(false);
  const [quickFilter, setQuickFilter] = useState("All");
  const [query, setQuery] = useState("");
  const [sortBy, setSortBy] = useState("newest");

  const baseEscalations = data?.mode === "live" ? data.escalations || [] : state.escalations;
  const escalations = useMemo(
    () => baseEscalations.map((item) => ({ ...item, ...(state.treatmentOverrides[item.id] || {}) })),
    [baseEscalations, state.treatmentOverrides],
  );

  // The five pipeline counters below always reflect the FULL escalations
  // set, per spec — filtering/search only changes the visible rows and the
  // "Showing X of Y" counter, never these totals.
  const counters = useMemo(() => countByTreatment(escalations), [escalations]);

  const visibleEscalations = useMemo(() => {
    const q = query.trim().toLowerCase();
    let rows = escalations.filter((esc) => {
      const matchesQuick = quickFilter === "All" || esc.treatmentStatus === quickFilter;
      const matchesQuery =
        !q ||
        [esc.sender, esc.region, esc.intent, esc.reason, esc.entity, esc.updatedBy, esc.treatmentStatus]
          .filter(Boolean)
          .join(" ")
          .toLowerCase()
          .includes(q);
      return matchesQuick && matchesQuery;
    });

    rows = [...rows].sort((a, b) => {
      if (sortBy === "newest") return new Date(b.time) - new Date(a.time);
      if (sortBy === "oldest") return new Date(a.time) - new Date(b.time);
      if (sortBy === "confidence") return (b.confidence ?? -1) - (a.confidence ?? -1);
      if (sortBy === "treatment") return String(a.treatmentStatus).localeCompare(String(b.treatmentStatus));
      return 0;
    });

    return rows;
  }, [escalations, quickFilter, query, sortBy]);

  function openEditor(escalation) {
    setEditing(escalation);
    setForm({ status: escalation.treatmentStatus, note: escalation.note || "" });
  }

  async function saveTreatment() {
    if (!editing) return;
    setSaving(true);
    try {
      const result = await escalationService.updateEscalationStatus(
        editing,
        form.status,
        form.note,
        state.currentUser.name,
      );
      dispatch({ type: "UPDATE_ESCALATION", payload: result });
      pushToast(t("toast.statusUpdated"), "success");
      if (result.backendUpdated) onRefresh?.();
      setEditing(null);
    } catch {
      pushToast(t("toast.validationError"), "error");
    } finally {
      setSaving(false);
    }
  }

  function findEmailRecord(escalation) {
    const auditRows = data?.mode === "live" ? data.auditLogs || [] : state.auditLogs;
    const audit =
      escalation.sourceRecord ||
      auditRows.find((row) => row.id === escalation.sourceRecordId) ||
      auditRows.find((row) => row.emailId === escalation.emailId || row.publicReference === escalation.publicReference);
    return audit ? { ...audit, time: escalation.time } : { ...escalation, subject: escalation.reason };
  }

  const showNote = form.status === "Treated" || form.status === "Canceled";

  return (
    <>
      <div className="mb-6 rounded-2xl bg-white shadow-2xl">
        <div className="flex w-full items-center justify-center">
          <h1 className="p-2 font-outfit text-xs text-[#757575]">{t("emails.subtitle")}</h1>
        </div>
        <div className="grid grid-cols-2 divide-y sm:grid-cols-3 sm:divide-y-0 md:grid-cols-5">
          {[
            { label: t("pipeline.total"), value: counters.total },
            { label: t("pipeline.nyt"), value: counters.notYetTreated },
            { label: t("pipeline.inTreatment"), value: counters.inTreatment },
            { label: t("pipeline.completed"), value: counters.completed },
            { label: t("pipeline.canceled"), value: counters.canceled },
          ].map((item) => (
            <div key={item.label} className="p-5 text-center">
              <div className="font-outfit text-3xl font-normal sm:text-4xl">{item.value}</div>
              <div className="mt-1 text-xs text-gray-500">{item.label}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-lg bg-white shadow-2xl">
        <div className="flex flex-col gap-3 border-b p-6 sm:flex-row sm:items-center sm:justify-between">
          <h3 className="font-outfit text-lg font-semibold">{t("emails.title")}</h3>
          <div className="flex flex-wrap items-center gap-2">
            <SearchInput value={query} onChange={setQuery} placeholder={t("emails.searchPlaceholder")} />
            <select
              value={sortBy}
              onChange={(event) => setSortBy(event.target.value)}
              className="rounded-md border border-gray-300 px-3 py-2 text-xs text-gray-700 sm:text-sm"
              aria-label={t("emails.sortBy")}
            >
              {SORT_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {t(opt.key)}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 px-6 pt-4">
          {QUICK_FILTERS.map((label) => (
            <button
              key={label}
              type="button"
              onClick={() => setQuickFilter(label)}
              aria-pressed={quickFilter === label}
              className={`rounded-full px-3 py-1.5 text-xs font-medium transition ${
                quickFilter === label ? "bg-black text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
              }`}
            >
              {t(QUICK_FILTER_KEYS[label])}
            </button>
          ))}
        </div>

        <p className="px-6 pb-2 pt-3 text-xs text-gray-500">
          {t("emails.showingRecords")
            .replace("{shown}", visibleEscalations.length)
            .replace("{total}", escalations.length)}
        </p>

        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{t("table.received")}</th>
                <th>{t("table.sender")}</th>
                <th>{t("table.zone")}</th>
                <th>{t("table.intent")}</th>
                <th>{t("table.confidence")}</th>
                <th>{t("table.entity")}</th>
                <th>{t("table.reason")}</th>
                <th>{t("table.treatment")}</th>
                <th>{t("table.updatedBy")}</th>
                <th>{t("table.action")}</th>
              </tr>
            </thead>
            <tbody>
              {visibleEscalations.length === 0 ? (
                <tr>
                  <td colSpan={10}>
                    <div className="p-8 text-center text-xs text-gray-500">{t("empty.noEscalations")}</div>
                  </td>
                </tr>
              ) : (
                visibleEscalations.map((esc) => (
                  <tr key={esc.id}>
                    <td>{formatDateTime(esc.time, lang)}</td>
                    <td>{esc.sender}</td>
                    <td>{esc.region}</td>
                    <td>{esc.intent}</td>
                    <td>{formatConfidence(esc.confidence)}</td>
                    <td>{esc.entity}</td>
                    <td>{esc.reason}</td>
                    <td>
                      <button
                        type="button"
                        onClick={() => openEditor(esc)}
                        className="inline-flex items-center gap-1.5 rounded-md border border-transparent hover:border-gray-200 hover:bg-gray-50"
                        title={t("btn.updateStatus")}
                      >
                        <StatusBadge value={esc.treatmentStatus} />
                      </button>
                    </td>
                    <td>{esc.updatedBy || "—"}</td>
                    <td>
                      <ViewEmailButton onClick={() => setViewEmail(findEmailRecord(esc))} />
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {editing ? (
        <FormModal
          title={t("modal.updateStatus")}
          onClose={() => setEditing(null)}
          onSubmit={saveTreatment}
          submitting={saving}
        >
          <FormField label={t("table.status")}>
            <select
              value={form.status}
              onChange={(event) => setForm((prev) => ({ ...prev, status: event.target.value }))}
              className={inputClass}
            >
              {TREATMENT_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {status}
                </option>
              ))}
            </select>
          </FormField>
          {showNote ? (
            <FormField label={t("field.note")}>
              <textarea
                value={form.note}
                onChange={(event) => setForm((prev) => ({ ...prev, note: event.target.value }))}
                rows={4}
                className={inputClass}
              />
            </FormField>
          ) : null}
        </FormModal>
      ) : null}

      {viewEmail ? <EmailViewer record={viewEmail} onClose={() => setViewEmail(null)} /> : null}
    </>
  );
}

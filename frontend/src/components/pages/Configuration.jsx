import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useRef, useState } from "react";
import { useApp } from "../../context/AppContext";
import { useTranslation } from "../../i18n/useTranslation";
import { configurationService } from "../../services/configurationService";
import { formatDateTime } from "../../utils/formatters";
import { isAdmin } from "../../utils/permissions";
import { SNOC_ARCHITECTURE_STAGES } from "../../data/mockData";
import { ChartCard, KpiCard } from "../Primitives";
import SearchInput from "../common/SearchInput";
import StatusBadge from "../common/StatusBadge";

export default function Configuration({ data }) {
  const { state, dispatch, pushToast } = useApp();
  const { t, lang } = useTranslation();
  const model = data?.model || {};
  const runtime = data?.runtime || {};
  const confidence = data?.confidenceAnalytics || {};
  const workflow = data?.workflow || [];
  const classData = model.classes?.length ? model.classes : data?.intents || [];
  const modelAccuracy = model.accuracy == null ? "N/A" : model.accuracy;
  const modelF1 = model.macroF1 == null ? "N/A" : model.macroF1;
  const averageConfidence = confidence.averageAnalyzerConfidence == null
    ? "N/A"
    : Math.round(confidence.averageAnalyzerConfidence <= 1 ? confidence.averageAnalyzerConfidence * 100 : confidence.averageAnalyzerConfidence);
  const admin = isAdmin(state.currentUser.role);
  const [newEmail, setNewEmail] = useState("");
  const [adding, setAdding] = useState(false);
  const fileRef = useRef(null);

  async function addEmail() {
    setAdding(true);
    try {
      const entry = await configurationService.addWhitelistEmail(newEmail, state.whitelist, state.currentUser.name);
      dispatch({ type: "ADD_WHITELIST", entry });
      setNewEmail("");
      pushToast(t("toast.whitelistAdded"), "success");
    } catch (err) {
      pushToast(err.message === "duplicate-email" ? t("toast.whitelistDuplicate") : t("toast.whitelistInvalid"), "error");
    } finally {
      setAdding(false);
    }
  }

  async function removeEmail(email) {
    await configurationService.removeWhitelistEmail(email);
    dispatch({ type: "REMOVE_WHITELIST", email });
    pushToast(t("toast.whitelistRemoved"), "success");
  }

  async function importCsv(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    const rows = text.split(/\r?\n/).map((line) => line.split(",")[0]).filter(Boolean);
    const added = await configurationService.importWhitelistCsv(rows, state.whitelist, state.currentUser.name);
    added.forEach((entry) => dispatch({ type: "ADD_WHITELIST", entry }));
    pushToast(`${added.length} ${lang === "fr" ? "e-mail(s) importé(s)." : "email(s) imported."}`, "success");
    if (fileRef.current) fileRef.current.value = "";
  }

  return (
    <>
      <h3 className="section-title">{t("config.aiResults")}</h3>
      <section className="four-kpi-grid">
        <KpiCard value={modelAccuracy} suffix={modelAccuracy === "N/A" ? "" : "%"} label={t("config.successRate")} />
        <KpiCard value={modelF1} suffix={modelF1 === "N/A" ? "" : "%"} label="F1 score" />
        <KpiCard value={averageConfidence} suffix={averageConfidence === "N/A" ? "" : "%"} label={t("config.confidenceRate")} />
        <KpiCard value={model.datasetRows ?? "N/A"} label={t("config.modelConfig")} />
      </section>

      <section className="two-chart-grid">
        <ChartCard title="Class distribution" subtitle="Model evaluation snapshot">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={classData}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="name" />
              <YAxis />
              <Tooltip />
              <Bar dataKey="value" fill="#DA291C" radius={[5, 5, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
        <div className="requests-card dashboard-card" style={{ marginTop: 0 }}>
          <div className="table-title-row">
            <div>
              <h2>{t("config.apiHealth")}</h2>
              <p>Read-only operational health; unreachable endpoints are labeled, never faked</p>
            </div>
          </div>
          <div className="api-grid">
            {workflow.map((step) => (
              <div key={step.id}>
                <code>{step.title}</code>
                <StatusBadge value={step.status} />
              </div>
            ))}
          </div>
        </div>
      </section>

      <h3 className="section-title">{t("config.whitelist")}</h3>
      <div className="requests-card dashboard-card">
        <div className="table-title-row flex-col items-stretch gap-3 sm:flex-row sm:items-center">
          <div>
            <h2>{t("whitelist.entries")}</h2>
            <p>{state.whitelist.length} entries</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <SearchInput value={newEmail} onChange={setNewEmail} placeholder={t("whitelist.placeholder")} ariaLabel={t("btn.addEmail")} />
            <button type="button" onClick={addEmail} disabled={adding} className="primary-button">
              {t("btn.addEmail")}
            </button>
            {admin ? (
              <>
                <input ref={fileRef} type="file" accept=".csv" onChange={importCsv} className="hidden" id="whitelist-csv-input" />
                <label htmlFor="whitelist-csv-input" className="primary-button cursor-pointer bg-white text-gray-700 shadow-sm">
                  {t("btn.importCsv")}
                </label>
              </>
            ) : null}
          </div>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{t("field.email")}</th>
                <th>{t("whitelist.addedBy")}</th>
                <th>{t("whitelist.addedAt")}</th>
                <th>{t("table.action")}</th>
              </tr>
            </thead>
            <tbody>
              {state.whitelist.length === 0 ? (
                <tr>
                  <td colSpan={4}>
                    <div className="p-8 text-center text-xs text-gray-500">{t("empty.noWhitelist")}</div>
                  </td>
                </tr>
              ) : (
                state.whitelist.map((entry) => (
                  <tr key={entry.email}>
                    <td>{entry.email}</td>
                    <td>{entry.addedBy}</td>
                    <td>{formatDateTime(entry.addedAt, lang)}</td>
                    <td>
                      <button
                        type="button"
                        onClick={() => removeEmail(entry.email)}
                        className="rounded-md px-2 py-1 text-xs font-medium text-[#cb3444] hover:bg-[#fdebec]"
                      >
                        {t("btn.delete")}
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <h3 className="section-title mt-8">Model and Dataset Metadata</h3>
      <div className="workflow-card dashboard-card mb-6" style={{ minHeight: "auto" }}>
        <dl className="grid grid-cols-2 gap-5 sm:grid-cols-3 lg:grid-cols-4">
          <MetaItem label="LLM Provider" value={runtime.provider} />
          <MetaItem label="Analyzer Model" value={runtime.analyzerModel} />
          <MetaItem label="Verifier Model" value={runtime.verifierModel} />
          <MetaItem label="Model Snapshot" value={model.available ? "Available" : "Unavailable"} />
          <MetaItem label="Last Updated" value={model.lastUpdated ? formatDateTime(model.lastUpdated, lang) : null} />
          <MetaItem label="Dataset Size" value={model.datasetRows} />
          <MetaItem label="Confidence Threshold" value={runtime.analyzerThreshold == null ? null : `${Math.round(runtime.analyzerThreshold * 100)}%`} />
          <MetaItem label="Execution Mode" value={runtime.executionMode} />
          <MetaItem label="Business API" value={runtime.businessApiMode} />
          <MetaItem label="Inbound Mailbox" value={runtime.inboxAddress} />
          <MetaItem label="IMAP Folder" value={runtime.imapMailbox} />
          <MetaItem label="Inbound Search" value={runtime.imapSearchCriterion} />
        </dl>
      </div>

      <h3 className="section-title">SNOC Processing Architecture</h3>
      <div className="workflow-grid mb-6">
        {SNOC_ARCHITECTURE_STAGES.map((stage, index) => (
          <article key={stage.id} className="workflow-card dashboard-card">
            <span className="workflow-number">{String(index + 1).padStart(2, "0")}</span>
            <h2>{stage.title}</h2>
            <ul className="mt-2 flex list-disc flex-col gap-1 pl-4 text-xs text-gray-600">
              {stage.points.map((point) => (
                <li key={point}>{point}</li>
              ))}
            </ul>
          </article>
        ))}
      </div>
    </>
  );
}

function MetaItem({ label, value }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-[10px] uppercase tracking-wide text-gray-400">{label}</dt>
      <dd className="text-sm font-semibold text-gray-800">{value || "N/A — Configured externally"}</dd>
    </div>
  );
}

import { X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "../../i18n/useTranslation";
import backendApi from "../../services/backendApi.js";
import traceCache from "../../services/traceCache.js";
import { formatConfidence, formatDateTime } from "../../utils/formatters";
import { enrichAuditRecord } from "../../utils/enrichRecord";
import StatusBadge from "./StatusBadge";
import PageTabs from "./PageTabs";
import PipelineTimeline from "./PipelineTimeline";
import JsonViewer from "./JsonViewer";

const TABS = [
  { id: "pipeline", label: "Processing Pipeline" },
  { id: "email", label: "Email" },
  { id: "extracted", label: "Extracted Data" },
  { id: "api", label: "API / Fulfilment" },
  { id: "response", label: "Response" },
  { id: "history", label: "Audit History" },
];

function getPublicReference(record) {
  const candidates = [record?.publicReference, record?.emailId, record?.rawRequestId, record?.requestId];
  return candidates.find((value) => typeof value === "string" && value.startsWith("SNOC-REQ-")) || null;
}

function normalizePipeline(stages) {
  if (!Array.isArray(stages)) return [];
  return stages.map((stage, index) => {
    const name = stage.stage || stage.agent || stage.node || stage.name || `stage-${index + 1}`;
    const rawStatus = String(stage.status || (stage.active ? "completed" : "unknown")).toLowerCase();
    const status = rawStatus === "success" ? "completed" : rawStatus;
    return {
      id: `${name}-${index}`,
      label: String(name).replace(/[_-]+/g, " ").replace(/\b\w/g, (char) => char.toUpperCase()),
      agent: stage.agent || stage.node || name,
      status,
      error: stage.error_category || stage.errorCategory || null,
      note: stage.detail || stage.message || null,
      durationMs: stage.duration_ms || stage.durationMs || null,
    };
  });
}

export default function AuditDetailsDrawer({ record: rawRecord, onClose }) {
  const { t, lang } = useTranslation();
  const [trace, setTrace] = useState(null);
  const [pipeline, setPipeline] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    function onKey(event) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    if (!rawRecord) return undefined;
    const publicReference = getPublicReference(rawRecord);
    if (!publicReference) return undefined;
    const controller = new AbortController();
    setLoading(true);
    Promise.all([
      traceCache.loadTrace(publicReference, { signal: controller.signal }).catch(() => null),
      backendApi.requestPipeline(publicReference, controller.signal).catch(() => null),
    ])
      .then(([traceValue, pipelineValue]) => {
        setTrace(traceValue);
        setPipeline(pipelineValue);
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [rawRecord]);

  const record = useMemo(() => {
    const base = enrichAuditRecord(rawRecord);
    if (!base) return null;
    const operation =
      trace?.operations?.find(
        (item) =>
          (base.posCode && item.pdvCode === base.posCode && item.actionLabel === base.intent) ||
          (base.posCode && item.pdvCode === base.posCode) ||
          item.actionLabel === base.intent,
      ) || trace?.operations?.[0] || null;
    const executionRaw =
      trace?.executions?.find(
        (item) =>
          item.operation_id === operation?.operationId ||
          item.operationId === operation?.operationId,
      ) || trace?.executions?.[0] || null;
    const decisionRaw =
      trace?.decisions?.find(
        (item) =>
          item.operation_id === operation?.operationId ||
          item.operationId === operation?.operationId,
      ) || trace?.decisions?.[0] || null;
    const outboxRaw = trace?.outbox?.[0] || null;
    const tracePipeline = normalizePipeline(trace?.pipeline);
    const fallbackPipeline = normalizePipeline(pipeline?.stages || pipeline?.pipeline);

    return {
      ...base,
      publicReference: trace?.publicReference || base.publicReference,
      originalBody: trace?.email?.body || base.originalBody,
      sender: trace?.email?.sender || base.sender,
      recipient: trace?.email?.recipients?.join(", ") || base.recipient,
      subject: trace?.email?.subject || base.subject,
      timestamp: trace?.email?.date || base.timestamp,
      pipeline: tracePipeline.length ? tracePipeline : fallbackPipeline.length ? fallbackPipeline : base.pipeline,
      entities: {
        ...base.entities,
        pdvCode: operation?.pdvCode || base.entities?.pdvCode,
        phone: operation?.phone || base.entities?.phone,
        missingFields: operation?.missingFields || [],
      },
      confidence: operation?.analyzerConfidence ?? base.confidence,
      verifierConfidence: operation?.verifierConfidence ?? null,
      decision: operation?.finalDecision || decisionRaw?.decision || base.decision,
      execution: executionRaw
        ? {
            action: operation?.actionLabel || operation?.action || base.intent,
            endpointLabel: executionRaw.endpoint || executionRaw.endpointLabel || null,
            requestPayload: executionRaw.request_payload || executionRaw.requestPayload || null,
            responsePayload: executionRaw.response_body || executionRaw.responseBody || executionRaw,
            status: executionRaw.status || executionRaw.response_status || null,
            durationMs: executionRaw.latency_ms || executionRaw.latencyMs || null,
          }
        : base.execution,
      reply: outboxRaw
        ? {
            to: outboxRaw.recipient || outboxRaw.to || base.sender,
            subject: outboxRaw.subject || null,
            body: outboxRaw.body || null,
            status: outboxRaw.status || "N/A",
          }
        : base.reply,
      auditHistory: trace?.decisions?.length
        ? trace.decisions.map((event) => ({
            timestamp: event.created_at || event.createdAt || base.timestamp,
            decision: event.decision || "—",
            agent: "Policy",
            user: null,
          }))
        : base.auditHistory,
    };
  }, [rawRecord, trace, pipeline]);

  if (!record) return null;

  return (
    <div
      className="drawer-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <aside
        className="decision-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={t("modal.auditDetails")}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <button type="button" className="drawer-close" onClick={onClose} aria-label="Close dialog">
          <X size={20} />
        </button>
        <p>{t("modal.auditDetails")}</p>
        <h2>{record.publicReference || record.emailId}</h2>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <StatusBadge value={record.status} />
          <span className="text-[11px] text-gray-400">{formatDateTime(record.timestamp, lang)}</span>
          {loading ? <span className="text-[11px] text-gray-400">Loading trace…</span> : null}
        </div>

        <PageTabs tabs={TABS} className="mt-5">
          {(active) => (
            <>
              {active === "pipeline" ? <PipelineTimeline stages={record.pipeline} /> : null}

              {active === "email" ? (
                <dl>
                  <Row k={t("field.sender")} v={record.sender} />
                  <Row k={t("field.recipient")} v={record.recipient || "—"} />
                  <Row k={t("field.subject")} v={record.subject} />
                  <Row k={t("field.date")} v={formatDateTime(record.timestamp, lang)} />
                  <Row k="Original body" v={record.originalBody || "Not captured for this record"} />
                  <Row k="Cleaned / normalized body" v={record.cleanedBody || "Not captured for this record"} />
                  <Row k={t("field.attachments")} v={record.attachments?.length ? record.attachments.join(", ") : "None"} />
                  <Row k={t("field.processingStatus")} v={<StatusBadge value={record.status} />} />
                </dl>
              ) : null}

              {active === "extracted" ? (
                <dl>
                  <Row k={t("table.intent")} v={record.intent} />
                  <Row k="Analyzer confidence" v={formatConfidence(record.confidence)} />
                  <Row k="Verifier confidence" v={formatConfidence(record.verifierConfidence)} />
                  <Row k="PDV code" v={record.entities?.pdvCode || "Missing"} />
                  <Row k="Phone / MSISDN" v={record.entities?.phone || "N/A"} />
                  <Row k="Language" v={record.entities?.language || "N/A"} />
                  <Row k="Sender authorized" v={record.status === "Unauthorized" ? "No" : "Yes"} />
                  <div className="mt-3">
                    <dt className="mb-1 text-[10px] text-gray-400">Entities (JSON)</dt>
                    <JsonViewer value={record.entities} />
                  </div>
                </dl>
              ) : null}

              {active === "api" ? (
                record.execution ? (
                  <dl>
                    <Row k="Selected operation" v={record.execution.action} />
                    <Row k="Endpoint" v={record.execution.endpointLabel || "N/A"} />
                    <Row k="Status" v={record.execution.status || "N/A"} />
                    <div className="mt-3">
                      <dt className="mb-1 text-[10px] text-gray-400">Request payload</dt>
                      <JsonViewer value={record.execution.requestPayload} />
                    </div>
                    <div className="mt-3">
                      <dt className="mb-1 text-[10px] text-gray-400">Response payload</dt>
                      <JsonViewer value={record.execution.responsePayload} />
                    </div>
                  </dl>
                ) : (
                  <p className="text-xs text-gray-400">No API execution occurred for this request.</p>
                )
              ) : null}

              {active === "response" ? (
                <dl>
                  <Row k="Reply recipient" v={record.reply?.to || "—"} />
                  <Row k="Reply subject" v={record.reply?.subject || "N/A"} />
                  <Row k="Outbox status" v={record.reply?.status || "N/A"} />
                  <Row k="Body" v={record.reply?.body || "Not captured for this record"} />
                </dl>
              ) : null}

              {active === "history" ? (
                <dl>
                  {record.auditHistory?.map((event, index) => (
                    <Row
                      key={`${event.timestamp}-${index}`}
                      k={formatDateTime(event.timestamp, lang)}
                      v={`${event.agent} — ${event.decision}${event.user ? ` (${event.user})` : ""}`}
                    />
                  ))}
                  {record.validationError ? <Row k="Validation error" v={record.validationError} /> : null}
                </dl>
              ) : null}
            </>
          )}
        </PageTabs>
      </aside>
    </div>
  );
}

function Row({ k, v }) {
  return (
    <div>
      <dt>{k}</dt>
      <dd>{v}</dd>
    </div>
  );
}

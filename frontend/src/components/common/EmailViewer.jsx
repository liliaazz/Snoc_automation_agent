import { useEffect, useState } from "react";
import { useTranslation } from "../../i18n/useTranslation";
import traceCache from "../../services/traceCache.js";
import { formatDateTime } from "../../utils/formatters";
import Modal from "./Modal";
import StatusBadge from "./StatusBadge";

function getPublicReference(record) {
  const candidates = [record?.publicReference, record?.emailId, record?.rawRequestId, record?.requestId];
  return candidates.find((value) => typeof value === "string" && value.startsWith("SNOC-REQ-")) || null;
}

export default function EmailViewer({ record, onClose }) {
  const { t, lang } = useTranslation();
  const [trace, setTrace] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!record) return undefined;
    const publicReference = getPublicReference(record);
    if (!publicReference) return undefined;
    const controller = new AbortController();
    setLoading(true);
    traceCache
      .loadTrace(publicReference, { signal: controller.signal })
      .then((value) => setTrace(value))
      .catch(() => setTrace(null))
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [record]);

  if (!record) return null;

  const email = trace?.email || {};
  const body =
    email.body ||
    record.originalBody ||
    record.bodyText ||
    record.body_text ||
    record.cleanedBody ||
    record.cleaned_text ||
    (lang === "fr" ? "Corps de l’e-mail non disponible." : "Email body is not available.");
  const recipients = email.recipients?.length
    ? email.recipients.join(", ")
    : record.recipient || "—";
  const attachments = email.attachments?.length || record.attachments?.length || 0;

  return (
    <Modal
      title={t("modal.emailDetails")}
      wide
      onClose={onClose}
      footer={
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          {t("btn.cancel")}
        </button>
      }
    >
      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Row label={t("field.sender")} value={email.sender || record.sender || "—"} />
        <Row label={t("field.recipient")} value={recipients} />
        <Row label={t("field.subject")} value={email.subject || record.subject || "—"} />
        <Row label={t("field.date")} value={formatDateTime(email.date || record.time || record.timestamp, lang)} />
        <Row label={t("field.processingStatus")} value={<StatusBadge value={record.status || email.processingStatus} />} />
        <Row label={t("field.attachments")} value={attachments ? String(attachments) : lang === "fr" ? "Aucune" : "None"} />
      </dl>
      <div className="mt-4">
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">{t("field.body")}</h4>
        {loading ? <p className="mb-2 text-xs text-gray-400">Loading persisted trace…</p> : null}
        <pre className="whitespace-pre-wrap rounded-lg bg-gray-50 p-4 font-outfit text-xs text-gray-700">{body}</pre>
      </div>
    </Modal>
  );
}

function Row({ label, value }) {
  return (
    <div className="border-b border-[#f0f0f0] pb-2">
      <dt className="text-[10px] uppercase tracking-wide text-gray-400">{label}</dt>
      <dd className="mt-1 text-sm text-gray-800">{value}</dd>
    </div>
  );
}

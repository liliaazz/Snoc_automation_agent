import { toConfidencePercent } from "./analytics";

// Turns a base AUDIT_LOGS record (or a live-backend equivalent) into the
// richer shape the Audit Details drawer needs. Everything is DERIVED from
// fields already on the record — no random values, no invented telemetry.
// Records may omit any of these fields; callers must handle undefined.
export function enrichAuditRecord(record) {
  if (!record) return null;

  const isLive = record.liveSource === true || record.sourceMode === "live";
  const isUnauthorized = record.status === "Unauthorized";
  const isRejected = record.status === "Rejected";
  const isEscalated = record.status === "Escalated";
  const isResolved = record.status === "Automatically Resolved";

  const stoppedAtStep = record.stoppedAtStep || (isUnauthorized ? "security" : isRejected ? "policy" : isEscalated ? record.agent?.toLowerCase() : null);

  const pipeline = [
    stage("ingress", "Email received", "Ingress", "completed"),
    stage(
      "security",
      "Sender / whitelist verification",
      "Security",
      isUnauthorized ? "failed" : "completed",
      isUnauthorized ? record.validationError : null,
    ),
    stage("cleaning", "Email cleaning / segmentation", "Ingress", isUnauthorized ? "skipped" : "completed"),
    stage(
      "classification",
      "AI classification",
      "NLU",
      isUnauthorized ? "skipped" : "completed",
      null,
      formatConfNote(record.confidence),
    ),
    stage(
      "extraction",
      "Entity extraction",
      "NLU",
      isUnauthorized ? "skipped" : record.posCode ? "completed" : "partial",
      isUnauthorized ? null : record.posCode ? null : "PDV code missing",
    ),
    stage(
      "validation",
      "Business validation",
      "Policy",
      isUnauthorized ? "skipped" : isEscalated || isRejected ? "failed" : "completed",
      isUnauthorized ? null : isEscalated || isRejected ? record.validationError : null,
    ),
    stage(
      "execution",
      "API execution",
      "Fulfilment",
      isResolved ? "completed" : "skipped",
      isResolved ? null : "Not executed — request did not reach fulfilment",
    ),
    stage("response", "Response email", "Fulfilment", isResolved ? "completed" : "skipped"),
    stage("audit", "Audit log", "Audit", "completed"),
  ].map((s) => ({ ...s, stoppedDownstream: stoppedAtStep ? isDownstreamOf(s.id, stoppedAtStep) : false }));

  const entities = {
    pdvCode: record.entities?.pdvCode || record.posCode || null,
    otp: record.entities?.otp ?? (record.intent === "OTP Change" ? (record.validationError && /otp/i.test(record.validationError) ? null : "Provided") : null),
    phone: record.entities?.phone || record.phone || null,
    language: record.entities?.language || record.detectedLanguage || guessLanguage(record.subject),
  };

  const execution = record.execution ||
    (!isLive && record.decision === "AUTO_EXECUTE"
      ? {
          action: record.intent,
          endpointLabel: endpointLabelFor(record.intent),
          requestPayload: { posCode: record.posCode, intent: record.intent, emailId: record.emailId },
          responsePayload: isResolved ? { status: "success" } : null,
          status: isResolved ? "Success" : "N/A",
          durationMs: record.processingDurationMs || null,
        }
      : null);

  const reply = record.reply ||
    (!isLive && isResolved
      ? { to: record.sender, subject: `Re: ${record.subject}`, body: null, status: "Sent" }
      : { to: record.sender, subject: null, body: null, status: "Not applicable" });

  const auditHistory = [
    {
      timestamp: record.timestamp,
      decision: record.decision,
      agent: record.agent,
      user: record.user && record.user !== "—" ? record.user : null,
    },
  ];

  return {
    ...record,
    stoppedAtStep,
    pipeline: record.pipeline?.length ? record.pipeline : isLive ? [] : pipeline,
    entities,
    execution,
    reply,
    auditHistory: record.auditHistory?.length ? record.auditHistory : auditHistory,
    cleanedBody: record.cleanedBody || record.cleaned_text || null,
    originalBody: record.originalBody || record.bodyText || record.body_text || null,
    attachments: record.attachments || [],
  };
}

function stage(id, label, agent, status, error = null, note = null) {
  return { id, label, agent, status, error, note, durationMs: null };
}

const STAGE_ORDER = ["ingress", "security", "cleaning", "classification", "extraction", "validation", "execution", "response", "audit"];
function isDownstreamOf(stageId, stoppedAtStep) {
  const stopIdx = STAGE_ORDER.indexOf(String(stoppedAtStep).toLowerCase());
  const idx = STAGE_ORDER.indexOf(stageId);
  if (stopIdx === -1) return false;
  return idx > stopIdx;
}

function formatConfNote(confidence) {
  const pct = toConfidencePercent(confidence);
  return pct === null ? "Confidence unavailable" : null;
}

function endpointLabelFor(intent) {
  const map = {
    "Create VPN": "POST /api/vpn/access",
    "Locked Account": "POST /api/account/unlock",
    "OTP Change": "POST /api/otp/change",
    "Password Reset": "POST /api/account/password-reset",
  };
  return map[intent] || null;
}

function guessLanguage(subject) {
  if (!subject) return null;
  return /[a-zA-Z]/.test(subject) && !/[éèàçùâêîôû]/i.test(subject) ? "English" : "French";
}

const COLORS = ["#2563eb", "#4caf50", "#ea8b00", "#f44336", "#7e22ce", "#0891b2"];

function numberOr(value, fallback = null) {
  if (value === null || value === undefined || value === "") return fallback;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function arrayOr(value) {
  if (Array.isArray(value)) return value;
  if (value === null || value === undefined) return [];
  return [value];
}

function titleCase(value) {
  return String(value || "Unknown")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

const ACTION_LABELS = {
  account_unblock: "Locked Account",
  password_reset: "Password Reset",
  otp_number_change: "OTP Change",
  vpn_access: "Create VPN",
};

function normalizeIntent(rawIntent, rawType) {
  const direct = String(rawIntent || "").trim();
  const aliases = {
    "Account Unblock": "Locked Account",
    "Locked Account": "Locked Account",
    "OTP Update": "OTP Change",
    "OTP Change": "OTP Change",
    "VPN Creation": "Create VPN",
    "VPN Access": "Create VPN",
    "Create VPN": "Create VPN",
    "Password Reset": "Password Reset",
  };
  if (aliases[direct]) return aliases[direct];
  const type = String(rawType || "").toLowerCase();
  return ACTION_LABELS[type] || direct || "Unknown";
}

function normalizeDecision(raw) {
  const value = String(raw || "").toUpperCase();
  if (value === "REQUEST_INFORMATION") return "ASK_FOR_INFORMATION";
  return value || "UNKNOWN";
}

function normalizeProcessingStatus(row) {
  const requestStatus = String(row.request_status ?? row.requestStatus ?? "").toUpperCase();
  const executionStatus = String(row.execution_status ?? row.executionStatus ?? "").toUpperCase();
  const decision = normalizeDecision(row.decision);

  if (executionStatus === "COMPLETED" || requestStatus === "SUCCESS" || requestStatus === "COMPLETED") {
    return "Automatically Resolved";
  }
  if (decision === "ASK_FOR_INFORMATION" || executionStatus === "NEEDS_INFORMATION" || requestStatus === "NEEDS_INFORMATION") {
    return "Needs Information";
  }
  if (decision === "ESCALATE" || executionStatus === "ESCALATED" || requestStatus === "ESCALATED") {
    return "Escalated";
  }
  if (requestStatus === "UNAUTHORIZED" || executionStatus === "UNAUTHORIZED") return "Unauthorized";
  if (decision === "REJECT" || requestStatus === "REJECTED" || executionStatus === "REJECTED") return "Rejected";
  if (requestStatus === "FAILED" || executionStatus === "FAILED") return "Failed";
  if (["PROCESSING", "EXECUTING", "READY_FOR_VALIDATION", "IN_PROGRESS"].includes(executionStatus) || requestStatus === "IN_PROGRESS") {
    return "Processing";
  }
  return "Received";
}

function agentFor(decision, status) {
  if (status === "Unauthorized") return "Security";
  if (decision === "AUTO_EXECUTE") return "Fulfilment";
  if (["ASK_FOR_INFORMATION", "ESCALATE", "REJECT"].includes(decision)) return "Policy";
  return "Audit";
}

export function normalizeSummary(raw, legacy = null) {
  const source = raw || {};
  const operational = source.operational || source.summary?.operational || {};
  const legacyStats = legacy?.stats || {};
  const successful = numberOr(
    operational.automaticallyResolvedRequests ??
      operational.automatically_resolved_requests ??
      operational.successfulExecutions ??
      operational.successful_executions ??
      operational.autoResolved ??
      operational.auto_resolved ??
      legacyStats.successful_executions,
    0,
  );
  const escalated = numberOr(operational.escalated ?? operational.manualReview ?? operational.manual_review ?? legacyStats.escalated, 0);
  return {
    generatedAt: source.generatedAt || source.generated_at || new Date().toISOString(),
    operational: {
      totalRequests: numberOr(operational.totalRequests ?? operational.total_requests ?? legacyStats.total_requests, 0),
      successfulExecutions: successful,
      autoResolved: successful,
      escalated,
      manualReview: escalated,
      rejected: numberOr(operational.rejected ?? legacyStats.rejected, 0),
      pendingRequests: numberOr(operational.pendingRequests ?? operational.pending_requests ?? legacyStats.pending_requests, 0),
      inProgress: numberOr(operational.inProgress ?? operational.in_progress ?? legacyStats.in_progress, 0),
      failed: numberOr(operational.failed ?? legacyStats.failed, 0),
      unauthorized: numberOr(operational.unauthorized ?? legacyStats.unauthorized, 0),
      lowConfidence: numberOr(operational.lowConfidence ?? operational.low_confidence ?? legacyStats.low_confidence, 0),
    },
    dataQuality: source.dataQuality ?? source.data_quality ?? null,
  };
}

export function normalizeRecentRows(raw) {
  const items = arrayOr(raw?.items ?? raw?.requests ?? raw);
  return items.map((row, index) => {
    const recordType = row.record_type ?? row.recordType ?? "business_operation";
    const requestReference =
      row.request_id ??
      row.public_reference ??
      row.publicReference ??
      null;
    const emailMessageId =
      row.email_message_id ??
      row.emailMessageId ??
      null;
    const operationId = row.operation_id ?? row.operationId ?? null;
    const publicReference = requestReference;
    const intent = normalizeIntent(row.intent, row.request_type ?? row.requestType);
    const decision = normalizeDecision(row.decision);
    const status = normalizeProcessingStatus(row);
    const entities = row.entities || {};
    const executionDetails = row.metadata?.execution_details ?? row.metadata?.executionDetails ?? null;
    const posCode = entities.pdv_code ?? entities.pdv ?? row.pdv_code ?? row.posCode ?? null;
    const phone = entities.phone_number ?? entities.phone ?? row.phone ?? null;
    const missingFields = arrayOr(row.missing_fields ?? row.missingFields);
    const missingFieldMessage = missingFields.length
      ? `Missing required fields: ${missingFields
          .map((field) => (["phone", "phone_number", "otp"].includes(field) ? "OTP/phone" : field))
          .join(", ")}`
      : null;
    const validationError =
      row.validation_error ??
      row.validationError ??
      missingFieldMessage ??
      (status === "Needs Information" ? "Additional information required" : null);
    const id =
      operationId ||
      (recordType === "email_security_event" && emailMessageId) ||
      `${publicReference || "request"}:${row.request_type || row.requestType || intent}:${index}`;
    return {
      id,
      liveSource: true,
      recordType,
      nonOperational: recordType === "email_security_event",
      operationId,
      executionOccurred:
        row.execution_occurred ??
        row.executionOccurred ??
        Boolean(executionDetails),
      emailId: emailMessageId || publicReference || id,
      publicReference:
        typeof publicReference === "string" && publicReference.trim()
          ? publicReference
          : null,
      rawRequestId: publicReference,
      timestamp: row.created_at ?? row.createdAt ?? row.timestamp ?? null,
      time: row.created_at ?? row.createdAt ?? row.timestamp ?? null,
      sender: row.sender || "—",
      recipient: row.recipient ?? null,
      region: row.zone || row.region || "Unknown",
      subject: row.subject || "—",
      intent,
      confidence: numberOr(row.confidence, null),
      decision,
      agent: agentFor(decision, status),
      user: row.assigned_user ?? row.assignedUser ?? "—",
      status,
      posCode,
      phone,
      validationError,
      originalBody: row.body_text ?? row.bodyText ?? null,
      cleanedBody: row.cleaned_text ?? row.cleanedText ?? null,
      bodyText: row.body_text ?? row.bodyText ?? null,
      detectedLanguage: row.detected_language ?? row.detectedLanguage ?? null,
      entities: {
        pdvCode: posCode,
        phone,
        language: row.detected_language ?? row.detectedLanguage ?? null,
        missingFields,
      },
      execution: executionDetails
        ? {
            action: intent,
            endpointLabel: executionDetails.endpoint ?? null,
            requestPayload:
              executionDetails.request_payload ??
              executionDetails.requestPayload ??
              null,
            responsePayload:
              executionDetails.response_body ??
              executionDetails.responseBody ??
              null,
            status: executionDetails.status ?? row.execution_status ?? null,
            durationMs: numberOr(
              executionDetails.latency_ms ?? executionDetails.latencyMs,
              null,
            ),
          }
        : null,
      reply: {
        to:
          row.reply_recipient ??
          row.replyRecipient ??
          null,
        subject: row.reply_subject ?? row.replySubject ?? null,
        body: row.reply_text ?? row.replyText ?? null,
        status:
          row.reply_status ??
          row.replyStatus ??
          (row.reply_text || row.replyText ? "Sent" : "Not sent"),
      },
      requestStatus: row.request_status ?? row.requestStatus ?? null,
      executionStatus: row.execution_status ?? row.executionStatus ?? null,
      processingDurationMs: numberOr(row.duration_ms ?? row.durationMs, null),
      attachments: arrayOr(row.attachments),
      raw: row,
    };
  });
}

export function normalizeTrends(raw) {
  return arrayOr(raw?.items ?? raw).map((item) => ({
    label: item.label ?? item.date ?? "—",
    date: item.date ?? null,
    received: numberOr(item.received ?? item.requests, 0),
    requests: numberOr(item.requests ?? item.received, 0),
    autoResolved: numberOr(item.autoResolved ?? item.auto_resolved ?? item.resolved, 0),
    resolved: numberOr(item.resolved ?? item.autoResolved ?? item.auto_resolved, 0),
    escalated: numberOr(item.escalated, 0),
    failed: numberOr(item.failed, 0),
  }));
}

export function normalizeIntents(raw) {
  return arrayOr(raw?.items ?? raw).map((item, index) => ({
    name: normalizeIntent(item.intent ?? item.name, item.request_type),
    value: numberOr(item.count ?? item.value, 0),
    percentage: numberOr(item.percentage, null),
    color: item.color || COLORS[index % COLORS.length],
  }));
}

export function normalizeAlerts(raw) {
  return arrayOr(raw?.alerts ?? raw).map((item, index) => ({
    id: item.id || `backend-alert-${index}`,
    severity: titleCase(item.severity || "Information"),
    category: item.category || "Backend",
    title: item.title || item.message || "Backend alert",
    description: item.description || (item.region && item.region !== "Unknown" ? `Region: ${item.region}` : ""),
    time: item.time || "—",
    status: item.status || "Open",
    target: item.target || (/escalat/i.test(item.message || "") ? "emails" : "audit"),
  }));
}

export function normalizeEscalations(raw, auditRows = []) {
  const formal = arrayOr(raw?.escalations ?? raw).map((item, index) => ({
    id: item.id || `escalation-${index}`,
    backendEscalationId: item.id || null,
    internalRequestId: item.request_id ?? item.requestId ?? null,
    publicReference: item.public_reference ?? item.publicReference ?? null,
    emailId: item.public_reference ?? item.publicReference ?? item.request_id ?? item.requestId ?? null,
    time: item.created_at ?? item.createdAt ?? null,
    sender: item.sender || "—",
    region: item.zone || item.region || "Unknown",
    intent: normalizeIntent(item.intent, item.request_type ?? item.requestType),
    confidence: numberOr(item.confidence, null),
    entity: item.pdv_code ?? item.pdvCode ?? item.phone ?? item.entity ?? "—",
    reason: item.summary || item.reason_code || "Manual review required",
    treatmentStatus: String(item.status || "").toLowerCase() === "resolved" ? "Treated" : "Not Yet Treated",
    updatedBy: item.updated_by || "—",
    note: "",
    formalEscalation: true,
    raw: item,
  }));

  const formalReferences = new Set(
    formal
      .filter((item) => item.publicReference)
      .map((item) => `${item.publicReference}:${item.intent}`),
  );
  const clarificationRows = auditRows
    .filter((row) => row.status === "Needs Information" || row.status === "Escalated")
    .filter((row) => !formalReferences.has(`${row.publicReference}:${row.intent}`))
    .map((row) => ({
      id: `queue:${row.id}`,
      publicReference: row.publicReference,
      emailId: row.emailId,
      time: row.timestamp,
      sender: row.sender,
      region: row.region,
      intent: row.intent,
      confidence: row.confidence,
      entity: row.posCode || row.phone || "—",
      reason: row.validationError || (row.status === "Needs Information" ? "Additional information required" : "Manual review required"),
      treatmentStatus: "Not Yet Treated",
      updatedBy: "—",
      note: "",
      sourceRecord: row,
      formalEscalation: row.status === "Escalated",
    }));

  const seen = new Set();
  return [...formal, ...clarificationRows].filter((item) => {
    const key = item.publicReference ? `${item.publicReference}:${item.intent}:${item.reason}` : item.id;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function normalizeDq(executive, dimensions, rules, summaryQuality = null) {
  const executiveData = executive || summaryQuality || null;
  return {
    executive: executiveData,
    dimensions: arrayOr(dimensions?.items ?? dimensions),
    rules: arrayOr(rules?.items ?? rules),
  };
}

export function normalizeModel(raw) {
  if (!raw) {
    return {
      available: false,
      accuracy: null,
      precision: null,
      recall: null,
      macroF1: null,
      weightedF1: null,
      f1Score: null,
      datasetRows: null,
      latencyMs: null,
      throughputPerMinute: null,
      classes: [],
      metrics: [],
    };
  }
  return {
    available: raw.available !== false,
    provider: raw.provider ?? null,
    modelName: raw.modelName ?? raw.model_name ?? null,
    accuracy: numberOr(
      raw.accuracy ??
        raw.successRate ??
        raw.success_rate ??
        raw.structuredOutputValidityRate ??
        raw.structured_output_validity_rate,
      null,
    ),
    precision: numberOr(raw.precision, null),
    recall: numberOr(raw.recall, null),
    macroF1: numberOr(raw.macroF1 ?? raw.macro_f1 ?? raw.f1Score ?? raw.f1_score, null),
    weightedF1: numberOr(raw.weightedF1 ?? raw.weighted_f1, null),
    f1Score: numberOr(raw.f1Score ?? raw.f1_score, null),
    datasetRows: numberOr(raw.datasetRows ?? raw.dataset_rows, null),
    latencyMs: numberOr(raw.latencyMs ?? raw.latency_ms, null),
    throughputPerMinute: numberOr(raw.throughputPerMinute ?? raw.throughput_per_minute, null),
    lastUpdated: raw.lastUpdated ?? raw.last_updated ?? null,
    classes: arrayOr(raw.classes),
    metrics: arrayOr(raw.metrics),
    raw,
  };
}

export function normalizeWorkflow(raw) {
  if (Array.isArray(raw?.items)) return raw.items;
  if (Array.isArray(raw)) return raw;
  if (!raw || typeof raw !== "object") return [];
  const agents = raw.agents || {};
  const rows = Object.entries(agents).map(([id, details]) => ({
    id,
    title: `${titleCase(id)} agent`,
    status: titleCase(details?.status || "unknown"),
    processed: numberOr(details?.processed, 0),
    errors: numberOr(details?.errors, 0),
    averageMs: numberOr(details?.latencyMs ?? details?.latency_ms, null),
    lastSuccess: details?.lastSuccess ?? details?.last_success ?? null,
  }));
  return rows;
}

export function normalizeRuntime(raw, health = null) {
  return {
    available: Boolean(raw || health),
    executionMode: String(raw?.mode ?? health?.mode ?? "unknown").toLowerCase(),
    provider: raw?.provider ?? null,
    analyzerModel: raw?.analyzer_model ?? raw?.analyzerModel ?? null,
    verifierModel: raw?.verifier_model ?? raw?.verifierModel ?? null,
    imapConfigured: raw?.imap_configured ?? raw?.imapConfigured ?? null,
    inboxAddress: raw?.inbox_address ?? raw?.inboxAddress ?? null,
    imapMailbox: raw?.imap_mailbox ?? raw?.imapMailbox ?? null,
    imapSearchCriterion: raw?.imap_search_criterion ?? raw?.imapSearchCriterion ?? null,
    smtpMode: raw?.smtp_mode ?? raw?.smtpMode ?? null,
    businessApiMode: raw?.business_api_mode ?? raw?.businessApiMode ?? null,
    authenticationConfigured: raw?.authentication_configured ?? raw?.authenticationConfigured ?? null,
    analyzerThreshold: numberOr(raw?.analyzer_min_raw_confidence ?? raw?.analyzerThreshold, null),
    verifierThreshold: numberOr(raw?.verifier_min_raw_confidence ?? raw?.verifierThreshold, null),
    raw: raw || health,
  };
}

export function normalizeConfidenceAnalytics(raw, auditRows = []) {
  if (raw) {
    return {
      totalOperations: numberOr(raw.total_operations ?? raw.totalOperations, 0),
      measuredOperations: numberOr(raw.measured_operations ?? raw.measuredOperations, 0),
      unmeasuredOperations: numberOr(raw.unmeasured_operations ?? raw.unmeasuredOperations, 0),
      averageAnalyzerConfidence: numberOr(raw.average_confidence ?? raw.averageAnalyzerConfidence, null),
      lowConfidenceCount: numberOr(raw.low_confidence_count ?? raw.lowConfidenceCount, 0),
      threshold: numberOr(raw.threshold, null),
      buckets: arrayOr(raw.buckets),
    };
  }
  const values = auditRows
    .map((row) => numberOr(row.confidence, null))
    .filter((value) => value !== null && value >= 0 && value <= 100)
    .map((value) => (value <= 1 ? value : value / 100));
  return {
    totalOperations: auditRows.length,
    measuredOperations: values.length,
    unmeasuredOperations: auditRows.length - values.length,
    averageAnalyzerConfidence: values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null,
    lowConfidenceCount: 0,
    threshold: null,
    buckets: [],
  };
}

export function normalizeMissingEntityAnalytics(raw, auditRows = []) {
  if (raw) {
    return arrayOr(raw.rows ?? raw).map((row) => {
      const action = normalizeIntent(null, row.action ?? row.type);
      const totalRequests = numberOr(row.total_requests ?? row.totalRequests ?? row.total, 0);
      const missingPdv = numberOr(row.missing_pdv ?? row.missingPdv, 0);
      const missingPhone = numberOr(row.missing_phone ?? row.missingPhone, 0);
      return {
        action,
        rawAction: row.action ?? row.type ?? null,
        totalRequests,
        missingPdv,
        missingPhone,
        missingPdvPercent: numberOr(
          row.missing_pdv_percent ?? row.missingPdvPercent,
          totalRequests ? (missingPdv / totalRequests) * 100 : null,
        ),
        missingPhonePercent: numberOr(
          row.missing_phone_percent ?? row.missingPhonePercent,
          totalRequests ? (missingPhone / totalRequests) * 100 : null,
        ),
        phoneApplicable:
          row.phone_applicable ??
          row.phoneApplicable ??
          action === "OTP Change",
      };
    });
  }
  const groups = {};
  auditRows.forEach((row) => {
    const action = row.intent || "Unknown";
    groups[action] ||= { action, totalRequests: 0, missingPdv: 0, missingPhone: 0 };
    groups[action].totalRequests += 1;
    if (!row.posCode) groups[action].missingPdv += 1;
    if (row.intent === "OTP Change" && !row.phone) groups[action].missingPhone += 1;
  });
  return Object.values(groups).map((row) => ({
    ...row,
    missingPdvPercent: row.totalRequests ? (row.missingPdv / row.totalRequests) * 100 : null,
    missingPhonePercent: row.totalRequests ? (row.missingPhone / row.totalRequests) * 100 : null,
    phoneApplicable: row.action === "OTP Change",
  }));
}

export function normalizeExecutionAnalytics(raw, auditRows = []) {
  if (raw) {
    return arrayOr(raw.rows ?? raw).map((row) => ({
      action: normalizeIntent(row.action, row.action),
      attempts: numberOr(row.attempts, 0),
      succeeded: numberOr(row.succeeded ?? row.success, 0),
      failed: numberOr(row.failed, 0),
      unknown: numberOr(row.unknown, 0),
      successRate: numberOr(row.success_rate ?? row.successRate, null),
      averageLatencyMs: numberOr(row.average_latency_ms ?? row.averageLatencyMs, null),
    }));
  }
  const groups = {};
  auditRows.filter((row) => row.decision === "AUTO_EXECUTE").forEach((row) => {
    groups[row.intent] ||= { action: row.intent, attempts: 0, succeeded: 0, failed: 0, unknown: 0, averageLatencyMs: null };
    groups[row.intent].attempts += 1;
    if (row.status === "Automatically Resolved") groups[row.intent].succeeded += 1;
    else if (row.status === "Failed" || row.status === "Rejected") groups[row.intent].failed += 1;
    else groups[row.intent].unknown += 1;
  });
  return Object.values(groups).map((row) => ({
    ...row,
    successRate: row.attempts ? row.succeeded / row.attempts : null,
  }));
}

export function deriveOperations(auditRows, executionRows = []) {
  const hourly = Array.from({ length: 24 }, (_, hour) => ({ label: `${String(hour).padStart(2, "0")}h`, value: 0 }));
  auditRows.forEach((row) => {
    const date = new Date(row.timestamp);
    if (!Number.isNaN(date.getTime())) hourly[date.getUTCHours()].value += 1;
  });
  const actions = executionRows.map((row) => ({
    label: row.action,
    success: row.succeeded,
    failed: row.failed,
  }));
  return { hourly, actions };
}

export function normalizeWhitelist(raw) {
  return arrayOr(raw?.entries ?? raw).map((entry) => ({
    email: entry.email,
    region: entry.zone || entry.region || "Unknown",
    addedBy: entry.addedBy || "Backend configuration",
    addedAt: entry.addedAt || null,
    expiresAt: entry.expiresAt || null,
  }));
}

export function normalizeAccounts(raw) {
  return arrayOr(raw?.accounts ?? raw).map((account) => ({
    id: account.id || account.username,
    username: account.username,
    fullname: account.fullname || account.username,
    email: account.email || "",
    role: account.role === "normal" ? "user" : account.role || "user",
    status: account.active === false ? "Inactive" : "Active",
    lastLogin: account.lastLogin || "—",
    managedByEnv: Boolean(account.managedByEnv),
  }));
}

export function normalizeRequestTrace(raw) {
  if (!raw || typeof raw !== "object") return null;
  const request = raw.request || {};
  const email = raw.email || {};
  const operations = arrayOr(raw.operations).map((operation) => ({
    operationId: operation.operation_id ?? operation.operationId ?? null,
    action: operation.action ?? null,
    actionLabel: normalizeIntent(null, operation.action),
    status: operation.status ?? null,
    pdvCode: operation.pdv_code ?? operation.pdvCode ?? null,
    phone: operation.phone ?? null,
    missingFields: arrayOr(operation.missing_fields ?? operation.missingFields),
    analyzerConfidence: numberOr(
      operation.analyzer_confidence?.raw_action_confidence ??
        operation.analyzerConfidence?.rawActionConfidence ??
        operation.analyzer_confidence ??
        operation.analyzerConfidence ??
        operation.confidence,
      null,
    ),
    verifierConfidence: numberOr(
      operation.verifier_confidence?.raw_confidence ??
        operation.verifierConfidence?.rawConfidence ??
        operation.verifier_confidence ??
        operation.verifierConfidence,
      null,
    ),
    finalDecision: operation.final_decision ?? operation.finalDecision ?? null,
    raw: operation,
  }));
  return {
    publicReference: request.public_reference ?? request.publicReference ?? raw.public_reference ?? raw.publicReference ?? null,
    request,
    email: {
      sender: email.sender ?? email.from ?? null,
      recipients: arrayOr(email.recipients ?? email.to),
      subject: email.subject ?? null,
      date: email.date ?? email.sent_at ?? email.created_at ?? null,
      body:
        email.body ??
        email.body_text ??
        email.bodyText ??
        email.text_body ??
        email.cleaned_text ??
        email.cleanedText ??
        email.raw_text ??
        null,
      processingStatus: email.processing_status ?? email.processingStatus ?? null,
      authorizationAllowed:
        email.authorization_allowed ?? email.authorizationAllowed ?? null,
      authorizationReason:
        email.authorization_reason ?? email.authorizationReason ?? null,
      attachments: arrayOr(email.attachments),
      parsingWarnings: arrayOr(email.parsing_warnings ?? email.parsingWarnings),
      raw: email,
    },
    operations,
    decisions: arrayOr(raw.decisions),
    executions: arrayOr(raw.executions),
    clarifications: arrayOr(raw.clarifications),
    escalations: arrayOr(raw.escalations),
    outbox: arrayOr(raw.outbox),
    modelRuns: arrayOr(raw.model_runs ?? raw.modelRuns),
    pipeline: arrayOr(raw.pipeline),
    raw,
  };
}

export default {
  normalizeSummary,
  normalizeRecentRows,
  normalizeTrends,
  normalizeIntents,
  normalizeAlerts,
  normalizeEscalations,
  normalizeDq,
  normalizeModel,
  normalizeWorkflow,
  normalizeRuntime,
  normalizeConfidenceAnalytics,
  normalizeMissingEntityAnalytics,
  normalizeExecutionAnalytics,
  deriveOperations,
  normalizeWhitelist,
  normalizeAccounts,
  normalizeRequestTrace,
};

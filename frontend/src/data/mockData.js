export const DEMO_DASHBOARD = {
  mode: "demo",
  generatedAt: "2026-07-24T00:56:46Z",
  summary: {
    operational: {
      totalRequests: 42,
      autoResolved: 28,
      inProgress: 5,
      manualReview: 7,
      rejected: 1,
      failed: 1,
      averageProcessingMs: 1480,
      readinessRate: 96.98,
    },
    dataQuality: {
      overallQualityScore: 84.7,
      totalRules: 48,
      passedRules: 39,
      failedRules: 8,
      errorRules: 1,
      criticalFatalOpenIssues: 3,
      failedChecks: 216,
      tablesMonitored: 6,
      lastExecutionAt: "2026-07-23T23:48:00Z",
    },
  },
  trends: [
    { label: "Thu", received: 31, autoResolved: 21, escalated: 6, failed: 1, averageProcessingMs: 1550 },
    { label: "Fri", received: 36, autoResolved: 25, escalated: 7, failed: 1, averageProcessingMs: 1510 },
    { label: "Sat", received: 28, autoResolved: 20, escalated: 5, failed: 0, averageProcessingMs: 1490 },
    { label: "Sun", received: 25, autoResolved: 17, escalated: 5, failed: 1, averageProcessingMs: 1460 },
    { label: "Mon", received: 44, autoResolved: 31, escalated: 8, failed: 1, averageProcessingMs: 1480 },
    { label: "Tue", received: 47, autoResolved: 33, escalated: 9, failed: 2, averageProcessingMs: 1470 },
    { label: "Wed", received: 42, autoResolved: 28, escalated: 7, failed: 1, averageProcessingMs: 1430 },
  ],
  intents: [
    { name: "Locked", value: 110, color: "#4caf50" },
    { name: "OTP", value: 100, color: "#ff9800" },
    { name: "VPN", value: 50, color: "#2563eb" },
    { name: "Reset", value: 5, color: "#f44336" },
  ],
  outcomes: [
    { name: "Completed", value: 28, color: "#4caf50" },
    { name: "Manual Review", value: 7, color: "#ff9800" },
    { name: "In Progress", value: 5, color: "#2563eb" },
    { name: "Rejected / Failed", value: 2, color: "#f44336" },
  ],
  recent: [
    { id: "SNOC-REQ-54A8", timestamp: "00:54:18", sender: "f***@gmail.com", intent: "VPN", confidence: 0.96, posCode: "12345678", action: "Create VPN access", status: "Completed", durationMs: 1380, validationError: "—" },
    { id: "SNOC-REQ-54A7", timestamp: "00:49:42", sender: "a***@djezzy.dz", intent: "OTP", confidence: 0.91, posCode: "23001462", action: "Update OTP", status: "Manual Review", durationMs: 1670, validationError: "Phone evidence missing" },
    { id: "SNOC-REQ-54A6", timestamp: "00:42:06", sender: "m***@djezzy.dz", intent: "Locked", confidence: 0.98, posCode: "31548720", action: "Unlock account", status: "Completed", durationMs: 1210, validationError: "—" },
    { id: "SNOC-REQ-54A5", timestamp: "00:35:39", sender: "s***@djezzy.dz", intent: "Reset", confidence: 0.72, posCode: "—", action: "Reset password", status: "Escalated", durationMs: 1900, validationError: "POS code missing" },
  ],
  operations: {
    emailsReceived: 42,
    whitelistPassRate: 97.6,
    classificationConfidence: 93.4,
    extractionSuccessRate: 96.2,
    validationPassRate: 88.1,
    apiSuccessRate: 96.7,
    manualReviewRate: 16.7,
    medianProcessingMs: 1320,
    p95ProcessingMs: 2410,
    hourly: [
      { label: "08h", value: 7 }, { label: "10h", value: 15 }, { label: "12h", value: 11 },
      { label: "14h", value: 19 }, { label: "16h", value: 23 }, { label: "18h", value: 12 },
    ],
    actions: [
      { label: "Create account", success: 7, failed: 1 },
      { label: "Reset password", success: 6, failed: 1 },
      { label: "Unlock account", success: 9, failed: 0 },
      { label: "Update OTP", success: 6, failed: 1 },
    ],
  },
  dq: {
    dimensions: [
      { dimension: "Completeness", score: 93.4, totalRules: 12, failedRules: 2, failedChecks: 44 },
      { dimension: "Consistency", score: 87.6, totalRules: 9, failedRules: 2, failedChecks: 63 },
      { dimension: "Uniqueness", score: 37.2, totalRules: 6, failedRules: 2, failedChecks: 94 },
      { dimension: "Validity", score: 91.8, totalRules: 21, failedRules: 2, failedChecks: 15 },
    ],
    rules: [
      { ruleId: "DQ-428", dimension: "Validity", table: "original_data_cleaned", column: "code_otp_number", severity: "Critical", score: 72.4, failedRows: 73, status: "Failed" },
      { ruleId: "DQ-385", dimension: "Completeness", table: "original_data_cleaned", column: "code_otp_number", severity: "High", score: 81.9, failedRows: 48, status: "Failed" },
      { ruleId: "DQ-144", dimension: "Uniqueness", table: "merged_dataset", column: "objet", severity: "Medium", score: 37.2, failedRows: 87, status: "Failed" },
      { ruleId: "DQ-302", dimension: "Consistency", table: "features_dataset", column: "label", severity: "High", score: 89.7, failedRows: 8, status: "Failed" },
    ],
  },
  model: {
    datasetRows: 265,
    readyRows: 257,
    reviewRows: 8,
    accuracy: 96.2,
    macroF1: 89.2,
    weightedF1: 96.5,
    classes: [
      { name: "Locked", value: 110 }, { name: "OTP", value: 100 }, { name: "VPN", value: 50 }, { name: "Reset", value: 5 },
    ],
    metrics: [
      { name: "Locked", precision: 1.0, recall: 0.955, f1: 0.977 },
      { name: "OTP", precision: 0.952, recall: 1.0, f1: 0.976 },
      { name: "VPN", precision: 1.0, recall: 0.9, f1: 0.947 },
      { name: "Reset", precision: 0.5, recall: 1.0, f1: 0.667 },
    ],
  },
  workflow: [
    { id: "imap", title: "IMAP email reception", status: "Healthy", processed: 42, errors: 0, averageMs: 82, lastSuccess: "00:55:52" },
    { id: "whitelist", title: "LDAP / whitelist security", status: "Healthy", processed: 42, errors: 1, averageMs: 31, lastSuccess: "00:55:51" },
    { id: "ai", title: "AI structured extraction", status: "Healthy", processed: 41, errors: 1, averageMs: 780, lastSuccess: "00:55:50" },
    { id: "routing", title: "Validation and API routing", status: "Degraded", processed: 35, errors: 2, averageMs: 302, lastSuccess: "00:55:49" },
    { id: "smtp", title: "SMTP HTML response", status: "Healthy", processed: 39, errors: 0, averageMs: 196, lastSuccess: "00:55:48" },
    { id: "observability", title: "Audit and observability", status: "Healthy", processed: 42, errors: 0, averageMs: 91, lastSuccess: "00:55:47" },
  ],
};

// -----------------------------------------------------------------------
// SNOC business data: deterministic fixtures (no Math.random at render
// time). This is the single normalized dataset that Home, Emails, Audit,
// Business Analysis and Operation Analysis all read from, so KPI totals,
// counters and chart totals always agree with one another.
// -----------------------------------------------------------------------

export const REGIONS = ["East Region", "West Region", "Central Region", "South Region"];
export const INTENT_NAMES = ["Locked Account", "Create VPN", "OTP Change", "Password Reset", "Unknown"];
export const AGENTS = ["Ingress", "Security", "NLU", "Policy", "Fulfilment", "Audit"];
export const OPERATIONS = ["Create VPN", "Unlock Account", "OTP Change", "Password Reset", "Escalation"];

export const AUDIT_LOGS = [
  { id: "AUD-2001", emailId: "EML-1001", timestamp: "2026-07-24T08:12:00Z", sender: "ahmed.east@djezzy.dz", recipient: "snoc-inbox@djezzy.dz", region: "East Region", subject: "Compte bloqué #4821", intent: "Locked Account", confidence: 0.98, decision: "AUTO_EXECUTE", agent: "Fulfilment", user: "—", status: "Automatically Resolved", posCode: "31548720", validationError: null },
  { id: "AUD-2002", emailId: "EML-1002", timestamp: "2026-07-24T08:04:00Z", sender: "fatima.west@djezzy.dz", recipient: "snoc-inbox@djezzy.dz", region: "West Region", subject: "Demande VPN #7742", intent: "Create VPN", confidence: 0.96, decision: "AUTO_EXECUTE", agent: "Fulfilment", user: "—", status: "Automatically Resolved", posCode: "12345678", validationError: null },
  { id: "AUD-2003", emailId: "EML-1003", timestamp: "2026-07-24T07:55:00Z", sender: "karim.central@djezzy.dz", recipient: "snoc-inbox@djezzy.dz", region: "Central Region", subject: "Changement OTP #1190", intent: "OTP Change", confidence: 0.91, decision: "AUTO_EXECUTE", agent: "Fulfilment", user: "—", status: "Automatically Resolved", posCode: "23001462", validationError: null },
  { id: "AUD-2004", emailId: "EML-1004", timestamp: "2026-07-24T07:41:00Z", sender: "s.reset@djezzy.dz", recipient: "snoc-inbox@djezzy.dz", region: "South Region", subject: "Réinitialisation mot de passe #3305", intent: "Password Reset", confidence: 0.72, decision: "ESCALATE", agent: "Policy", user: "Safa Miloudi", status: "Escalated", posCode: null, validationError: "POS code missing" },
  { id: "AUD-2005", emailId: "EML-1005", timestamp: "2026-07-24T07:33:00Z", sender: "noureddine@djezzy.dz", recipient: "snoc-inbox@djezzy.dz", region: "Central Region", subject: "Escalade requise #9981", intent: "Unknown", confidence: 0.41, decision: "ESCALATE", agent: "NLU", user: "Amine K.", status: "Escalated", posCode: null, validationError: "Intent below confidence threshold" },
  { id: "AUD-2006", emailId: "EML-1006", timestamp: "2026-07-24T07:20:00Z", sender: "unknown.sender@mailx.com", recipient: "snoc-inbox@djezzy.dz", region: "East Region", subject: "Demande VPN #5510", intent: "Create VPN", confidence: 0.88, decision: "REJECT", agent: "Security", user: "—", status: "Unauthorized", posCode: null, validationError: "Sender not on whitelist" },
  { id: "AUD-2007", emailId: "EML-1007", timestamp: "2026-07-24T07:09:00Z", sender: "yacine.south@djezzy.dz", recipient: "snoc-inbox@djezzy.dz", region: "South Region", subject: "Compte bloqué #6642", intent: "Locked Account", confidence: 0.65, decision: "REJECT", agent: "Policy", user: "—", status: "Rejected", posCode: "88410032", validationError: "Business validation failed" },
  { id: "AUD-2008", emailId: "EML-1008", timestamp: "2026-07-24T06:58:00Z", sender: "ahmed.east@djezzy.dz", recipient: "snoc-inbox@djezzy.dz", region: "East Region", subject: "Changement OTP #2217", intent: "OTP Change", confidence: 0.35, decision: "ESCALATE", agent: "NLU", user: "Safa Miloudi", status: "Escalated", posCode: "31548720", validationError: "Missing OTP" },
  { id: "AUD-2009", emailId: "EML-1009", timestamp: "2026-07-24T06:47:00Z", sender: "karim.central@djezzy.dz", recipient: "snoc-inbox@djezzy.dz", region: "Central Region", subject: "Réinitialisation mot de passe #1102", intent: "Password Reset", confidence: 0.94, decision: "AUTO_EXECUTE", agent: "Fulfilment", user: "—", status: "Automatically Resolved", posCode: "23001462", validationError: null },
  { id: "AUD-2010", emailId: "EML-1010", timestamp: "2026-07-24T06:30:00Z", sender: "fatima.west@djezzy.dz", recipient: "snoc-inbox@djezzy.dz", region: "West Region", subject: "Compte bloqué #4409", intent: "Locked Account", confidence: null, decision: "ESCALATE", agent: "Ingress", user: "Amine K.", status: "Escalated", posCode: "12345678", validationError: "Confidence unavailable" },
];

// Escalations are the subset of AUDIT_LOGS with status "Escalated", carried
// through the escalation-treatment workflow. treatmentStatus is intentionally
// distinct from the AUDIT_LOGS `status` (processing status) field above.
export const ESCALATIONS = [
  { id: "ESC-3001", auditId: "AUD-2004", emailId: "EML-1004", time: "2026-07-24T07:41:00Z", sender: "s.reset@djezzy.dz", region: "South Region", intent: "Password Reset", confidence: 0.72, entity: "PDV missing", reason: "Missing PDV", treatmentStatus: "Not Yet Treated", updatedBy: "—", updatedAt: null, note: "" },
  { id: "ESC-3002", auditId: "AUD-2005", emailId: "EML-1005", time: "2026-07-24T07:33:00Z", sender: "noureddine@djezzy.dz", region: "Central Region", intent: "Unknown", confidence: 0.41, entity: "All present", reason: "Unknown intent", treatmentStatus: "Pending", updatedBy: "Amine K.", updatedAt: "2026-07-24T07:50:00Z", note: "Investigating sender history." },
  { id: "ESC-3003", auditId: "AUD-2008", emailId: "EML-1008", time: "2026-07-24T06:58:00Z", sender: "ahmed.east@djezzy.dz", region: "East Region", intent: "OTP Change", confidence: 0.35, entity: "OTP missing", reason: "Missing OTP", treatmentStatus: "Treated", updatedBy: "Safa Miloudi", updatedAt: "2026-07-24T07:15:00Z", note: "Contacted requester, OTP confirmed by phone." },
  { id: "ESC-3004", auditId: "AUD-2010", emailId: "EML-1010", time: "2026-07-24T06:30:00Z", sender: "fatima.west@djezzy.dz", region: "West Region", intent: "Locked Account", confidence: null, entity: "PDV & OTP missing", reason: "Confidence below threshold", treatmentStatus: "Canceled", updatedBy: "Amine K.", updatedAt: "2026-07-24T06:50:00Z", note: "Duplicate of ESC-3001." },
];

export const USERS = [
  { id: "U1", username: "safa.miloudi", fullname: "Safa Miloudi", email: "safa.miloudi@djezzy.dz", role: "admin", status: "Active", lastLogin: "2026-07-24T08:12:00Z" },
  { id: "U2", username: "amine.k", fullname: "Amine Kaddour", email: "amine.k@djezzy.dz", role: "user", status: "Active", lastLogin: "2026-07-23T17:40:00Z" },
  { id: "U3", username: "lina.b", fullname: "Lina Belkacem", email: "lina.b@djezzy.dz", role: "user", status: "Inactive", lastLogin: "2026-07-10T09:02:00Z" },
  { id: "U4", username: "omar.t", fullname: "Omar Tahar", email: "omar.t@djezzy.dz", role: "user", status: "Active", lastLogin: "2026-07-24T07:55:00Z" },
];

export const WHITELIST_REGIONS = ["East", "West", "North", "South", "Central", "All"];

export const WHITELIST = [
  { email: "support@djezzy.dz", addedBy: "Safa Miloudi", addedAt: "2026-06-01T09:00:00Z", expiresAt: null, region: "All" },
  { email: "admin@djezzy.dz", addedBy: "Safa Miloudi", addedAt: "2026-06-01T09:00:00Z", expiresAt: null, region: "All" },
  { email: "noc@djezzy.dz", addedBy: "Amine Kaddour", addedAt: "2026-06-14T11:20:00Z", expiresAt: null, region: "Central" },
];

export const RULES = [
  { id: "R1", name: "Low Confidence Escalation", description: "Escalate when AI confidence is below the configured threshold.", condition: "Confidence < Threshold", threshold: 85, action: "ESCALATE", enabled: true, updatedBy: "Safa Miloudi", updatedAt: "2026-07-20T10:00:00Z" },
  { id: "R2", name: "Missing PDV", description: "Request missing PDV information before proceeding.", condition: "PDV not extracted", threshold: null, action: "REQUEST_INFORMATION", enabled: true, updatedBy: "Safa Miloudi", updatedAt: "2026-07-18T10:00:00Z" },
  { id: "R3", name: "Missing OTP", description: "Request missing OTP information before proceeding.", condition: "OTP not extracted", threshold: null, action: "REQUEST_INFORMATION", enabled: true, updatedBy: "Safa Miloudi", updatedAt: "2026-07-18T10:00:00Z" },
  { id: "R4", name: "Unauthorized Sender", description: "Reject requests coming from a sender not on the whitelist.", condition: "Sender not whitelisted", threshold: null, action: "REJECT", enabled: true, updatedBy: "Amine Kaddour", updatedAt: "2026-07-15T10:00:00Z" },
  { id: "R5", name: "Unknown Intent", description: "Escalate to a human agent when intent cannot be classified.", condition: "Intent = Unknown", threshold: null, action: "ESCALATE", enabled: true, updatedBy: "Safa Miloudi", updatedAt: "2026-07-15T10:00:00Z" },
  { id: "R6", name: "Sensitive Operation", description: "Escalate sensitive operations such as account deletion.", condition: "Operation flagged sensitive", threshold: null, action: "ESCALATE", enabled: false, updatedBy: "Safa Miloudi", updatedAt: "2026-07-11T10:00:00Z" },
];

export const ALERTS = [
  { id: "AL1", severity: "Critical", category: "Escalation", title: "Escalation queue increasing rapidly", description: "Unresolved escalations rose sharply in the last hour.", time: "5 min ago", status: "Open", target: "emails" },
  { id: "AL2", severity: "Warning", category: "Processing", title: "Inbox processing backlog", description: "Emails pending in the ingress queue above normal levels.", time: "12 min ago", status: "Open", target: "emails" },
  { id: "AL3", severity: "Critical", category: "Security", title: "Unauthorized sender detected", description: "Multiple requests from a non-whitelisted domain.", time: "22 min ago", status: "Open", target: "audit" },
  { id: "AL4", severity: "Information", category: "Agent", title: "Agent response timeout", description: "NLU agent response exceeded expected latency once.", time: "40 min ago", status: "Monitoring", target: "configuration" },
  { id: "AL5", severity: "Warning", category: "System", title: "Rule threshold nearing trigger", description: "Confidence scores trending toward the escalation threshold.", time: "1 hr ago", status: "Open", target: "parametre" },
];

// Operation Analysis — entity extraction (missing PDV / OTP), broken down
// by operation type. Deterministic, matches OPERATIONS list above.
export const ENTITY_EXTRACTION = [
  { operation: "Create VPN", total: 96, missingPdv: 12, missingOtp: 4 },
  { operation: "Unlock Account", total: 118, missingPdv: 9, missingOtp: 0 },
  { operation: "OTP Change", total: 84, missingPdv: 3, missingOtp: 21 },
  { operation: "Password Reset", total: 61, missingPdv: 14, missingOtp: 8 },
  { operation: "Escalation", total: 27, missingPdv: 6, missingOtp: 5 },
];

// -----------------------------------------------------------------------
// Model / dataset metadata for Configuration → "Model and Dataset
// Metadata". Values are only shown when actually known; anything not
// supplied by the backend/config layer stays null and renders as N/A or
// "Configured externally" in the UI. Do NOT hardcode a specific vendor
// model name here — this project may run Qwen, Gemma, or another model
// depending on deployment, and that choice belongs to the backend config.
// -----------------------------------------------------------------------
export const MODEL_METADATA = {
  llmProvider: null,
  analyzerModel: null,
  verifierModel: null,
  pipelineVersion: null,
  lastRetrainAt: null,
  datasetSize: null,
  supportedLanguages: ["French", "English"],
  confidenceThreshold: 85,
  executionMode: null, // "dry-run" | "live" | null
};

// Six-stage SNOC processing architecture shown in Configuration. Text is
// descriptive of intended behavior; it does not assert live health here —
// health comes from `workflow` (DEMO_DASHBOARD.workflow) separately.
export const SNOC_ARCHITECTURE_STAGES = [
  {
    id: "ingress",
    title: "Ingress",
    points: ["Receive email through mailbox / IMAP", "Parse sender, recipients, subject, date and body"],
  },
  {
    id: "security",
    title: "Security",
    points: ["Sender authorization", "Whitelist / group validation", "Unauthorized rejection"],
  },
  {
    id: "ai",
    title: "AI Analysis",
    points: ["Classify intent", "Extract PDV / OTP / phone", "Structured output with confidence when measured"],
  },
  {
    id: "policy",
    title: "Policy and Routing",
    points: ["Validate required fields", "Apply rules", "Choose auto-execute, request information, escalate or reject"],
  },
  {
    id: "fulfilment",
    title: "Fulfilment and Notification",
    points: ["Execute supported operation", "Create response", "Queue / send email"],
  },
  {
    id: "audit",
    title: "Audit and Observability",
    points: ["Persist decisions", "Durations and errors", "Manual treatment", "Operational analytics"],
  },
];


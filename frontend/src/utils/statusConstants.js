// Centralized status vocabulary. Do not mix processing status with
// escalation treatment status anywhere in the app — always import
// these constants instead of writing string literals.

export const PROCESSING_STATUSES = [
  "Received",
  "Processing",
  "Automatically Resolved",
  "Escalated",
  "Needs Information",
  "Partially Completed",
  "Failed",
  "Canceled",
  "Rejected",
  "Unauthorized",
];

export const TREATMENT_STATUSES = ["Pending", "Not Yet Treated", "Treated", "Canceled"];

export const RULE_ACTIONS = ["AUTO_EXECUTE", "REQUEST_INFORMATION", "ESCALATE", "REJECT"];

// Emails page pipeline counters -> treatmentStatus mapping (explicit, single
// source of truth so Emails / Operation Analysis / Home never disagree).
export function countByTreatment(escalations) {
  return {
    total: escalations.length,
    notYetTreated: escalations.filter((e) => e.treatmentStatus === "Not Yet Treated").length,
    inTreatment: escalations.filter((e) => e.treatmentStatus === "Pending").length,
    completed: escalations.filter((e) => e.treatmentStatus === "Treated").length,
    canceled: escalations.filter((e) => e.treatmentStatus === "Canceled").length,
  };
}

export function countByProcessingStatus(records) {
  return {
    total: records.length,
    resolved: records.filter((r) => r.status === "Automatically Resolved").length,
    escalated: records.filter((r) => r.status === "Escalated").length,
    needsInformation: records.filter((r) => r.status === "Needs Information").length,
    rejected: records.filter((r) => r.status === "Rejected").length,
    unauthorized: records.filter((r) => r.status === "Unauthorized").length,
  };
}

// One shared badge class map for every status/severity token used across pages.
const BADGE_PALETTE = {
  received: "bg-[#eaf1ff] text-[#2563eb]",
  processing: "bg-[#eaf1ff] text-[#2563eb]",
  "automatically-resolved": "bg-[#eaf8f0] text-[#249c62]",
  escalated: "bg-[#fff3df] text-[#ad6800]",
  "needs-information": "bg-[#fff3df] text-[#ad6800]",
  "partially-completed": "bg-[#eaf1ff] text-[#2563eb]",
  failed: "bg-[#fdebec] text-[#cb3444]",
  rejected: "bg-[#fdebec] text-[#cb3444]",
  unauthorized: "bg-[#fdebec] text-[#cb3444]",
  pending: "bg-[#eaf1ff] text-[#2563eb]",
  "not-yet-treated": "bg-[#edf0f4] text-[#596273]",
  treated: "bg-[#eaf8f0] text-[#249c62]",
  canceled: "bg-[#fdebec] text-[#cb3444]",
  active: "bg-[#eaf8f0] text-[#249c62]",
  inactive: "bg-[#edf0f4] text-[#596273]",
  critical: "bg-[#fdebec] text-[#cb3444]",
  warning: "bg-[#fff3df] text-[#ad6800]",
  information: "bg-[#eaf1ff] text-[#2563eb]",
  healthy: "bg-[#eaf8f0] text-[#249c62]",
  degraded: "bg-[#fff3df] text-[#ad6800]",
};

export function badgeClasses(value) {
  const normalized = String(value || "unknown")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");
  return BADGE_PALETTE[normalized] || "bg-[#edf0f4] text-[#596273]";
}

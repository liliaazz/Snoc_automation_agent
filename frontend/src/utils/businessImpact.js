// -----------------------------------------------------------------------
// Business-impact / ROI estimates. Every number here is either derived
// directly from records, or requires a documented, centrally-defined
// assumption. If an assumption is not configured, the corresponding
// metric returns null so the UI can render "N/A — assumption not
// configured" instead of a fabricated figure. Nothing is invented.
// -----------------------------------------------------------------------

// Centralized, documented assumptions. Left undefined/null on purpose —
// this project has not been given a validated per-operation cost figure.
// When Configuration supplies one (e.g. via backend config), pass it into
// these functions instead of relying on the default below.
export const ASSUMPTIONS = {
  // Average minutes a human agent spends manually handling one escalated
  // or manually-processed request. Used only to estimate hours saved.
  minutesPerManualOperation: 6,
  // Cost per manual operation in local currency. Not configured by
  // default — must come from backend/config before financial gain is
  // calculated.
  costPerManualOperation: null,
};

export function getBusinessImpact(records, assumptions = ASSUMPTIONS) {
  const total = records.length;
  const autoResolved = records.filter((r) => r.status === "Automatically Resolved").length;
  const escalated = records.filter((r) => r.status === "Escalated").length;

  // Manual operations avoided = requests the agent resolved automatically,
  // each of which would otherwise have required a human operation.
  const manualOperationsAvoided = autoResolved;

  const estimatedHoursSaved =
    typeof assumptions.minutesPerManualOperation === "number"
      ? (manualOperationsAvoided * assumptions.minutesPerManualOperation) / 60
      : null;

  const automationRate = total ? autoResolved / total : null;

  const estimatedFinancialGain =
    typeof assumptions.costPerManualOperation === "number" && assumptions.costPerManualOperation > 0
      ? manualOperationsAvoided * assumptions.costPerManualOperation
      : null;

  return {
    manualOperationsAvoided,
    estimatedHoursSaved,
    automationRate,
    escalationsRequiringHumanWork: escalated,
    estimatedFinancialGain,
    financialGainUnavailableReason: estimatedFinancialGain === null ? "costPerManualOperation not configured" : null,
    assumptions,
  };
}

// Formula descriptions surfaced via MetricInfoTooltip. Keep in sync with
// the calculations above.
export const IMPACT_FORMULAS = {
  manualOperationsAvoided: "Count of requests with status = Automatically Resolved.",
  estimatedHoursSaved: "manualOperationsAvoided × minutesPerManualOperation ÷ 60 (assumption, configurable).",
  automationRate: "Automatically Resolved ÷ Total requests.",
  estimatedFinancialGain: "manualOperationsAvoided × costPerManualOperation (requires configured cost assumption).",
};

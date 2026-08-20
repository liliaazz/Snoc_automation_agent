// -----------------------------------------------------------------------
// Reusable analytics selectors. Every chart/KPI on Business Analysis,
// Operation Analysis, Home and Configuration derives from these functions
// operating on the SAME `state.auditLogs` array, so totals never disagree
// across pages. Nothing here uses Math.random() or any non-deterministic
// value — every number is computed from the records passed in.
//
// CONFIDENCE CONTRACT (see AGENT prompt §7):
//   null / undefined / "" / non-finite / out-of-range -> N/A (excluded)
//   0            -> 0%
//   0 < v <= 1    -> v * 100 (fraction)
//   1 < v <= 100  -> v as-is (already a percentage)
// -----------------------------------------------------------------------

export function hasMeasuredConfidence(value) {
  if (value === null || value === undefined || value === "") return false;
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0) return false;
  const pct = n <= 1 ? n * 100 : n;
  return pct <= 100;
}

export function toConfidencePercent(value) {
  if (!hasMeasuredConfidence(value)) return null;
  const n = Number(value);
  return n <= 1 ? n * 100 : n;
}

export function getStatusCounts(records) {
  const counts = {};
  records.forEach((r) => {
    counts[r.status] = (counts[r.status] || 0) + 1;
  });
  return counts;
}

export function getIntentCounts(records) {
  const counts = {};
  records.forEach((r) => {
    const key = r.intent || "Unknown";
    counts[key] = (counts[key] || 0) + 1;
  });
  return counts;
}

export function getRegionCounts(records) {
  const counts = {};
  records.forEach((r) => {
    const key = r.region || "Unknown";
    counts[key] = (counts[key] || 0) + 1;
  });
  return counts;
}

// Average confidence: only records with real numeric confidence.
// N/A (null) when nothing measured. Never treats N/A as zero.
export function getConfidenceMetrics(records, threshold = 85) {
  const measured = records
    .map((r) => toConfidencePercent(r.confidence))
    .filter((v) => v !== null);

  const unmeasuredCount = records.length - measured.length;
  const average = measured.length
    ? measured.reduce((sum, v) => sum + v, 0) / measured.length
    : null;

  const lowConfidenceCount = measured.filter((v) => v < threshold).length;

  return {
    average, // number 0-100 or null
    unmeasuredCount,
    measuredCount: measured.length,
    lowConfidenceCount,
    threshold,
  };
}

// Distribution buckets. N/A bucket is kept separate, never merged into 0%.
export function getConfidenceBuckets(records) {
  const buckets = {
    na: 0,
    below70: 0,
    b70_80: 0,
    b80_90: 0,
    b90_95: 0,
    b95_100: 0,
  };
  records.forEach((r) => {
    const pct = toConfidencePercent(r.confidence);
    if (pct === null) {
      buckets.na += 1;
    } else if (pct < 70) buckets.below70 += 1;
    else if (pct < 80) buckets.b70_80 += 1;
    else if (pct < 90) buckets.b80_90 += 1;
    else if (pct < 95) buckets.b90_95 += 1;
    else buckets.b95_100 += 1;
  });
  return buckets;
}

function safeDate(record) {
  const d = new Date(record.timestamp);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function getHourlyVolume(records) {
  const hours = Array.from({ length: 24 }, (_, h) => ({ label: `${String(h).padStart(2, "0")}h`, value: 0 }));
  records.forEach((r) => {
    const d = safeDate(r);
    if (!d) return;
    hours[d.getUTCHours()].value += 1;
  });
  return hours;
}

const DAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export function getDailyVolume(records) {
  const map = {};
  records.forEach((r) => {
    const d = safeDate(r);
    if (!d) return;
    const key = d.toISOString().slice(0, 10);
    map[key] = (map[key] || 0) + 1;
  });
  return Object.entries(map)
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([key, value]) => ({ label: key, value }));
}

export function getMonthlyVolume(records) {
  const map = {};
  records.forEach((r) => {
    const d = safeDate(r);
    if (!d) return;
    const key = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
    map[key] = (map[key] || 0) + 1;
  });
  return Object.entries(map)
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([key, value]) => ({ label: key, value }));
}

export function getYearlyVolume(records) {
  const map = {};
  records.forEach((r) => {
    const d = safeDate(r);
    if (!d) return;
    const key = String(d.getUTCFullYear());
    map[key] = (map[key] || 0) + 1;
  });
  return Object.entries(map)
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([key, value]) => ({ label: key, value }));
}

export function getVolumeByPeriod(records, period) {
  if (period === "hour") return getHourlyVolume(records);
  if (period === "month") return getMonthlyVolume(records);
  if (period === "year") return getYearlyVolume(records);
  return getDailyVolume(records);
}

export function getPeakHours(records, top = 3) {
  const hourly = getHourlyVolume(records).filter((h) => h.value > 0);
  return [...hourly].sort((a, b) => b.value - a.value).slice(0, top);
}

// rows: Mon-Sun (ISO order), columns: 0-23
export function getSupportHeatmap(records) {
  const grid = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => 0));
  let hasData = false;
  records.forEach((r) => {
    const d = safeDate(r);
    if (!d) return;
    hasData = true;
    const isoDay = (d.getUTCDay() + 6) % 7; // 0 = Monday
    grid[isoDay][d.getUTCHours()] += 1;
  });
  return { grid, hasData, dayLabels: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] };
}

// Group by posCode / PDV to find repeated incidents.
export function getUnstablePos(records, minIncidents = 2) {
  const groups = {};
  records.forEach((r) => {
    if (!r.posCode) return;
    if (!groups[r.posCode]) {
      groups[r.posCode] = { posCode: r.posCode, count: 0, region: r.region, intents: {}, statuses: {} };
    }
    const g = groups[r.posCode];
    g.count += 1;
    g.intents[r.intent] = (g.intents[r.intent] || 0) + 1;
    g.statuses[r.status] = (g.statuses[r.status] || 0) + 1;
  });
  return Object.values(groups)
    .filter((g) => g.count >= minIncidents)
    .map((g) => ({
      posCode: g.posCode,
      region: g.region,
      count: g.count,
      topIntent: Object.entries(g.intents).sort((a, b) => b[1] - a[1])[0]?.[0] || "—",
      topStatus: Object.entries(g.statuses).sort((a, b) => b[1] - a[1])[0]?.[0] || "—",
    }))
    .sort((a, b) => b.count - a.count);
}

export function getMissingEntityAnalysis(records) {
  const total = records.length;
  const missingPdv = records.filter((r) => !r.posCode).length;
  const missingOtp = records.filter((r) => r.intent === "OTP Change" && r.validationError && /otp/i.test(r.validationError)).length;
  const missingBoth = records.filter(
    (r) => !r.posCode && r.intent === "OTP Change" && r.validationError && /otp/i.test(r.validationError),
  ).length;

  // Breakdown by request type / region / sender for missing PDV or OTP records.
  const byType = {};
  records.forEach((r) => {
    const missing = !r.posCode || (r.intent === "OTP Change" && /otp/i.test(r.validationError || ""));
    if (!missing) return;
    const key = r.intent || "Unknown";
    if (!byType[key]) byType[key] = { type: key, count: 0, regions: {}, senders: {} };
    byType[key].count += 1;
    byType[key].regions[r.region] = (byType[key].regions[r.region] || 0) + 1;
    byType[key].senders[r.sender] = (byType[key].senders[r.sender] || 0) + 1;
  });

  const rows = Object.values(byType).map((entry) => ({
    type: entry.type,
    missingCount: entry.count,
    topRegion: Object.entries(entry.regions).sort((a, b) => b[1] - a[1])[0]?.[0] || "—",
    topSender: Object.entries(entry.senders).sort((a, b) => b[1] - a[1])[0]?.[0] || "—",
  }));

  return {
    total,
    missingPdv,
    missingOtp,
    missingBoth,
    missingPdvRate: total ? missingPdv / total : null,
    missingOtpRate: total ? missingOtp / total : null,
    rows,
  };
}

// totalProcessed = every record that reached a terminal processing state.
// totalEligible  = records that were eligible for automatic execution
//                  (i.e. not rejected/unauthorized for authorization reasons).
export function getProcessingMetrics(records) {
  const totalProcessed = records.length;
  const resolved = records.filter((r) => r.status === "Automatically Resolved").length;
  const escalated = records.filter((r) => ["Escalated", "Needs Information"].includes(r.status)).length;
  const rejected = records.filter((r) => r.status === "Rejected").length;
  const unauthorized = records.filter((r) => r.status === "Unauthorized").length;
  const failed = records.filter((r) => r.status === "Failed").length;
  const totalEligible = records.filter((r) => r.status !== "Unauthorized").length;

  const successRate = totalProcessed ? resolved / totalProcessed : null;
  const failureRate = totalProcessed ? failed / totalProcessed : null;
  const automationRate = totalEligible ? resolved / totalEligible : null;
  const escalationRate = totalProcessed ? escalated / totalProcessed : null;

  return {
    totalProcessed,
    totalEligible,
    resolved,
    escalated,
    rejected,
    unauthorized,
    successRate,
    failureRate,
    automationRate,
    escalationRate,
  };
}

// API health per operation, derived only from records that actually carry
// execution telemetry (decision === AUTO_EXECUTE and agent === Fulfilment).
// When no such records exist for an operation, returns null metrics so the
// UI can show "No execution telemetry available" instead of a fake number.
export function getApiMetrics(records, operation) {
  const relevant = records.filter((r) => r.intent === operation && r.decision === "AUTO_EXECUTE");
  if (relevant.length === 0) {
    return {
      operation,
      attempts: 0,
      successful: 0,
      failed: 0,
      successRate: null,
      hasTelemetry: false,
    };
  }
  const successful = relevant.filter((r) => r.status === "Automatically Resolved").length;
  const failed = relevant.length - successful;
  return {
    operation,
    attempts: relevant.length,
    successful,
    failed,
    successRate: successful / relevant.length,
    hasTelemetry: true,
  };
}

export function getClassificationVolume(records) {
  const counts = getIntentCounts(records);
  const unresolved = records.filter((r) => r.intent === "Unknown").length;
  const escalatedOrClarify = records.filter((r) => ["Escalated", "Needs Information"].includes(r.status)).length;
  return {
    total: records.length,
    byIntent: counts,
    unresolved,
    escalatedOrClarify,
  };
}

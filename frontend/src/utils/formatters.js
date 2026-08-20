export function formatDateTime(iso, locale = "en-US") {
  if (!iso || iso === "—") return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(locale === "fr" ? "fr-FR" : "en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function formatPercent(numerator, denominator, digits = 1) {
  if (!denominator) return "N/A";
  return `${((numerator / denominator) * 100).toFixed(digits)}%`;
}

export function formatConfidence(value) {
  if (
    value === null ||
    value === undefined ||
    value === ""
  ) {
    return "N/A";
  }

  const numericValue = Number(value);

  if (!Number.isFinite(numericValue) || numericValue < 0) {
    return "N/A";
  }

  const percentage =
    numericValue <= 1
      ? numericValue * 100
      : numericValue;

  if (percentage > 100) {
    return "N/A";
  }

  return `${Math.round(percentage)}%`;
}

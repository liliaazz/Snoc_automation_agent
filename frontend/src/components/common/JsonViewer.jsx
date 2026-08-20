const SECRET_KEY_PATTERN = /(token|secret|password|apikey|api_key|authorization)/i;

function redact(value) {
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, v]) => [key, SECRET_KEY_PATTERN.test(key) ? "••••••" : redact(v)]),
    );
  }
  return value;
}

export default function JsonViewer({ value, emptyLabel = "N/A" }) {
  if (value === null || value === undefined) {
    return <p className="text-xs text-gray-400">{emptyLabel}</p>;
  }
  const safe = redact(value);
  return (
    <pre className="max-h-64 overflow-auto rounded-lg bg-gray-50 p-3 font-mono text-[11px] leading-relaxed text-gray-700">
      {JSON.stringify(safe, null, 2)}
    </pre>
  );
}

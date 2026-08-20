// Shared building blocks, restyled with the exact ESI Logis Tailwind
// look (rounded-2xl / shadow-2xl bg-gray-100 cards, font-outfit) so the
// look is consistent everywhere these primitives are used.

// Mirrors ESI Logis' "Stats Overview Cards" block from DashboardContent.jsx
export function MetricRibbon({ items }) {
  return (
    <div className="mb-6 rounded-2xl bg-gray-100 shadow-2xl">
      <div className="flex w-full items-center justify-center">
        <h1 className="p-2 font-outfit text-xs text-[#757575]">By numbers</h1>
      </div>
      <div className="grid grid-cols-2 divide-y md:grid-cols-4 md:divide-y-0">
        {items.map((item) => (
          <StatCard key={item.label} label={item.label} value={item.value} />
        ))}
      </div>
    </div>
  );
}

function StatCard({ label, value }) {
  return (
    <div className="p-5 text-center">
      <div className="font-outfit text-4xl font-normal sm:text-5xl md:text-6xl">{value}</div>
      <div className="mt-1 text-gray-500">{label}</div>
    </div>
  );
}

// Mirrors the small white "Resolution Time / Staff" KPI cards
export function KpiCard({ value, label, suffix = "", tone = "default" }) {
  const toneClasses = tone === "warning" ? "text-[#ea8b00]" : "text-black";
  return (
    <article className="flex h-full min-h-[150px] flex-col items-center justify-center rounded-2xl bg-white p-6 shadow-xl">
      <div className={`font-outfit text-[32px] font-bold ${toneClasses}`}>
        {value}
        <span className="text-base text-[#757575]"> {suffix}</span>
      </div>
      <div className="mt-1 text-center text-xs text-[#757575]">{label}</div>
    </article>
  );
}

// Mirrors the gray-100 rounded-2xl shadow-2xl chart cards
export function ChartCard({ title, subtitle, children, className = "" }) {
  return (
    <article className={`flex min-h-[370px] flex-col rounded-2xl bg-gray-100 p-6 shadow-2xl ${className}`}>
      <div className="mb-2 min-h-[40px] text-center">
        <h2 className="font-outfit text-lg font-medium text-[#757575]">{title}</h2>
        {subtitle ? <p className="mt-1 text-[11px] text-[#9a9a9a]">{subtitle}</p> : null}
      </div>
      <div className="min-h-[260px] flex-1">{children}</div>
    </article>
  );
}

export function StatusBadge({ value }) {
  const normalized = String(value || "Unknown").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
  const palette = {
    completed: "bg-[#eaf8f0] text-[#249c62]",
    healthy: "bg-[#eaf8f0] text-[#249c62]",
    "manual-review": "bg-[#fff3df] text-[#ad6800]",
    escalated: "bg-[#fff3df] text-[#ad6800]",
    degraded: "bg-[#fff3df] text-[#ad6800]",
    failed: "bg-[#fdebec] text-[#cb3444]",
    rejected: "bg-[#fdebec] text-[#cb3444]",
    "in-progress": "bg-[#eaf1ff] text-[#2563eb]",
  };
  const classes = palette[normalized] || "bg-[#edf0f4] text-[#596273]";
  return (
    <span className={`inline-flex rounded-full px-2.5 py-1 text-[10px] font-semibold ${classes}`}>{value}</span>
  );
}

export function EmptyState({ title, description }) {
  return (
    <div className="flex min-h-[178px] flex-col items-center justify-center rounded-2xl border border-dashed border-[#cdd2d8] bg-white p-6 text-center">
      <strong className="text-lg">{title}</strong>
      <span className="mt-2 text-xs text-[#7d838a]">{description}</span>
    </div>
  );
}

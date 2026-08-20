const STATUS_DOT = {
  completed: "bg-[#249c62]",
  partial: "bg-[#ad6800]",
  failed: "bg-[#cb3444]",
  skipped: "bg-[#c9ccd1]",
};

export default function PipelineTimeline({ stages }) {
  if (!stages || stages.length === 0) {
    return <p className="text-xs text-gray-400">No pipeline telemetry available for this record.</p>;
  }

  return (
    <ol className="flex flex-col gap-0">
      {stages.map((stage, index) => (
        <li key={stage.id} className="relative flex gap-3 pb-5 pl-1 last:pb-0">
          {index < stages.length - 1 ? (
            <span className="absolute left-[9px] top-4 h-full w-px bg-gray-200" aria-hidden="true" />
          ) : null}
          <span
            className={`relative z-10 mt-1 h-[10px] w-[10px] shrink-0 rounded-full ${STATUS_DOT[stage.status] || "bg-gray-300"}`}
            aria-hidden="true"
          />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-outfit text-xs font-semibold text-gray-800">{stage.label}</span>
              <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium text-gray-500">{stage.agent}</span>
              <span className="text-[10px] uppercase tracking-wide text-gray-400">{stage.status}</span>
              {stage.stoppedDownstream ? (
                <span className="text-[10px] italic text-gray-400">not reached</span>
              ) : null}
            </div>
            {stage.note ? <p className="mt-0.5 text-[11px] text-gray-500">{stage.note}</p> : null}
            {stage.error ? <p className="mt-0.5 text-[11px] text-[#cb3444]">{stage.error}</p> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

import { Info } from "lucide-react";
import { useState } from "react";

export default function MetricInfoTooltip({ text }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-flex">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        onBlur={() => setOpen(false)}
        aria-label="Formula details"
        aria-expanded={open}
        className="text-gray-400 hover:text-gray-600"
      >
        <Info size={13} />
      </button>
      {open ? (
        <span
          role="tooltip"
          className="absolute bottom-full left-1/2 z-20 mb-2 w-52 -translate-x-1/2 rounded-lg bg-gray-900 p-2 text-left text-[10px] leading-relaxed text-white shadow-xl"
        >
          {text}
        </span>
      ) : null}
    </span>
  );
}

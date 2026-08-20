import { useState } from "react";

// Accessible, keyboard-operable tab bar. Reuses existing color tokens
// (black active state / orange accent) rather than introducing new ones.
// `tabs` = [{ id, label }]; `children` is a function (activeId) => node.
export default function PageTabs({ tabs, defaultTab, children, className = "" }) {
  const [active, setActive] = useState(defaultTab || tabs[0]?.id);

  function onKeyDown(event, index) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    const dir = event.key === "ArrowRight" ? 1 : -1;
    const next = tabs[(index + dir + tabs.length) % tabs.length];
    setActive(next.id);
  }

  return (
    <div className={className}>
      <div
        role="tablist"
        aria-label="Section tabs"
        className="mb-4 flex flex-wrap gap-1 overflow-x-auto rounded-xl bg-gray-100 p-1"
      >
        {tabs.map((tab, index) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`tab-${tab.id}`}
            aria-selected={active === tab.id}
            aria-controls={`panel-${tab.id}`}
            tabIndex={active === tab.id ? 0 : -1}
            onKeyDown={(event) => onKeyDown(event, index)}
            onClick={() => setActive(tab.id)}
            className={`whitespace-nowrap rounded-lg px-3 py-2 text-xs font-semibold transition sm:text-sm ${
              active === tab.id ? "bg-black text-white shadow" : "text-gray-600 hover:bg-white"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`panel-${active}`} aria-labelledby={`tab-${active}`}>
        {children(active)}
      </div>
    </div>
  );
}

import { Globe } from "lucide-react";
import { useMemo, useState } from "react";
import { useApp } from "../../context/AppContext";
import { useTranslation } from "../../i18n/useTranslation";
import { rulesService } from "../../services/rulesService";
import { formatDateTime } from "../../utils/formatters";
import { canEditRuleThreshold } from "../../utils/permissions";
import { RULE_ACTIONS } from "../../utils/statusConstants";
import SearchInput from "../common/SearchInput";
import FilterSelect from "../common/FilterSelect";

// Rules aren't tagged with a category in the data model, so group them
// deterministically by name/condition keywords rather than inventing a
// new field on every rule record.
function groupOf(rule) {
  const text = `${rule.name} ${rule.condition}`.toLowerCase();
  if (text.includes("unauthorized") || text.includes("sender")) return "Security";
  if (text.includes("missing")) return "Missing Information";
  if (text.includes("confidence")) return "Confidence";
  if (text.includes("sensitive")) return "Sensitive Operations";
  return "Other";
}

const GROUP_ORDER = ["Security", "Missing Information", "Confidence", "Sensitive Operations", "Other"];

export default function Parametre() {
  const { state, dispatch, pushToast } = useApp();
  const { t, lang } = useTranslation();
  const canEditThreshold = canEditRuleThreshold(state.currentUser.role);
  const [query, setQuery] = useState("");
  const [actionFilter, setActionFilter] = useState("");
  const [enabledFilter, setEnabledFilter] = useState("");

  const filteredRules = useMemo(() => {
    const q = query.trim().toLowerCase();
    return state.rules.filter((rule) => {
      const matchesQuery = !q || `${rule.name} ${rule.description} ${rule.condition}`.toLowerCase().includes(q);
      const matchesAction = !actionFilter || rule.action === actionFilter;
      const matchesEnabled = !enabledFilter || (enabledFilter === "enabled" ? rule.enabled : !rule.enabled);
      return matchesQuery && matchesAction && matchesEnabled;
    });
  }, [state.rules, query, actionFilter, enabledFilter]);

  const grouped = useMemo(() => {
    const groups = {};
    filteredRules.forEach((rule) => {
      const key = groupOf(rule);
      if (!groups[key]) groups[key] = [];
      groups[key].push(rule);
    });
    return GROUP_ORDER.filter((g) => groups[g]?.length).map((g) => ({ name: g, rules: groups[g] }));
  }, [filteredRules]);

  async function toggleRule(id) {
    await rulesService.toggleRule(id);
    dispatch({ type: "TOGGLE_RULE", id });
    pushToast(t("toast.saved"), "success");
  }

  async function updateThreshold(id, value) {
    if (!canEditThreshold) return;
    const threshold = Number(value);
    if (Number.isNaN(threshold)) return;
    await rulesService.updateRuleThreshold(id, threshold);
    dispatch({ type: "UPDATE_RULE_THRESHOLD", id, threshold });
    pushToast(t("toast.saved"), "success");
  }

  return (
    <>
      <h3 className="section-title">{t("param.rules")}</h3>
      {!canEditThreshold ? <p className="mb-3 text-xs text-gray-500">{t("rule.adminOnlyThreshold")}</p> : null}

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <SearchInput value={query} onChange={setQuery} placeholder="Search rules…" />
        <FilterSelect value={actionFilter} onChange={setActionFilter} options={RULE_ACTIONS} allLabel="Action: All" ariaLabel="Filter by action" />
        <FilterSelect
          value={enabledFilter}
          onChange={setEnabledFilter}
          options={["enabled", "disabled"]}
          allLabel="Status: All"
          ariaLabel="Filter by enabled/disabled"
        />
      </div>

      {grouped.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-[#cdd2d8] bg-white p-6 text-center text-xs text-gray-500">
          {t("empty.noRules")}
        </div>
      ) : (
        grouped.map((group) => (
          <div key={group.name} className="mb-6">
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[#ea8b00]">{group.name}</h4>
            <div className="flex flex-col gap-3">
              {group.rules.map((rule) => (
                <article key={rule.id} className="rounded-2xl bg-white p-5 shadow-md">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h4 className="font-outfit text-sm font-semibold">{rule.name}</h4>
                      <p className="mt-1 text-xs text-gray-500">{rule.description}</p>
                    </div>
                    <label className="inline-flex items-center gap-2 text-xs font-medium text-gray-600">
                      <input
                        type="checkbox"
                        checked={rule.enabled}
                        onChange={() => toggleRule(rule.id)}
                        aria-label={`${t("rule.enabled")} — ${rule.name}`}
                        className="h-4 w-4 accent-black"
                      />
                      {t("rule.enabled")}
                    </label>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-4 text-xs text-gray-600">
                    <span>
                      <strong>{t("rule.condition")}:</strong> {rule.condition}
                    </span>
                    {rule.threshold !== null ? (
                      <span className="flex items-center gap-1.5">
                        <strong>{t("rule.threshold")}:</strong>
                        <input
                          type="number"
                          defaultValue={rule.threshold}
                          disabled={!canEditThreshold}
                          onBlur={(event) => updateThreshold(rule.id, event.target.value)}
                          aria-label={`${t("rule.threshold")} — ${rule.name}`}
                          className="w-16 rounded-md border border-gray-300 px-2 py-1 disabled:cursor-not-allowed disabled:bg-gray-100"
                        />
                      </span>
                    ) : null}
                    <span>
                      <strong>{t("rule.action")}:</strong> {rule.action}
                    </span>
                  </div>
                  <p className="mt-2 text-[11px] text-gray-400">
                    {t("rule.updatedBy")}: {rule.updatedBy} · {t("rule.updatedAt")}: {formatDateTime(rule.updatedAt, lang)}
                  </p>
                </article>
              ))}
            </div>
          </div>
        ))
      )}

      <h3 className="section-title mt-8">{t("config.general")}</h3>
      <div className="dashboard-card p-6" style={{ maxWidth: 360 }}>
        <p className="mb-3 text-xs text-gray-500">
          {lang === "en"
            ? "Controls the same language state used by the header language switcher."
            : "Contrôle le même paramètre de langue que le sélecteur de langue de l'en-tête."}
        </p>
        <button
          type="button"
          onClick={() => dispatch({ type: "SET_LANGUAGE", language: lang === "en" ? "fr" : "en" })}
          className="inline-flex items-center gap-2 rounded-md bg-white px-4 py-2 text-sm font-medium text-gray-800 shadow-md hover:bg-black hover:text-white"
        >
          <Globe size={16} />
          {lang === "en" ? "English" : "Français"} — {lang === "en" ? "click to switch" : "cliquer pour changer"}
        </button>
      </div>
    </>
  );
}

import { AnimatePresence, motion } from "framer-motion";
import { ChevronDown, Globe, Mail, PauseCircle, PlayCircle, RefreshCcw, User } from "lucide-react";
import { useRef, useState } from "react";
import { useApp } from "../context/AppContext";
import { useTranslation } from "../i18n/useTranslation";
import { emailService } from "../services/emailService";
import backendApi from "../services/backendApi.js";
import { isAdmin } from "../utils/permissions";
import FormModal, { FormField, inputClass } from "./common/FormModal";

export default function DashboardHeader({ title, mode, generatedAt, loading, onRefresh, onRequestLogout }) {
  const { state, dispatch, pushToast } = useApp();
  const { t, lang } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [activeForm, setActiveForm] = useState(null); // "username" | "password"
  const [fieldValue, setFieldValue] = useState("");
  const menuRef = useRef(null);

  const updated = generatedAt
    ? new Date(generatedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : "—";

  async function handleProcessInbox() {
    setProcessing(true);
    pushToast(t("toast.processing"), "info");
    try {
      const result = await emailService.processInbox();
      pushToast(`${t("toast.processed")} (${result.processed})`, "success");
      onRefresh?.();
    } catch {
      pushToast(t("toast.refreshFailed"), "error");
    } finally {
      setProcessing(false);
    }
  }

  async function handleToggleAgent() {
    try {
      const result = await backendApi.toggleAgent();
      const active = Boolean(result?.agent_active ?? result?.agentActive);
      dispatch({ type: "SET_AGENT_ACTIVE", agentActive: active });
      pushToast(active ? t("toast.agentActive") : t("toast.agentPaused"), active ? "success" : "info");
      onRefresh?.();
    } catch {
      pushToast(t("toast.refreshFailed"), "error");
    }
  }

  function openField(kind) {
    setActiveForm(kind);
    setFieldValue(kind === "username" ? state.currentUser.username : "");
    setMenuOpen(false);
  }

  function saveField() {
    if (!fieldValue.trim()) return;
    if (activeForm === "username") {
      dispatch({ type: "SET_CURRENT_USER_FIELD", fields: { username: fieldValue.trim() } });
    }
    pushToast(t("toast.saved"), "success");
    setActiveForm(null);
  }

  return (
    <header className="flex h-auto min-h-[86px] flex-wrap items-center justify-between gap-3 border-2 border-l-0 border-[#e2e2e2] bg-[#f4f4f5] px-4 py-3 sm:px-8 md:px-14">
      <div>
        <h1 className="m-0 font-oxanium text-lg font-semibold sm:text-xl md:text-2xl">{title}</h1>
        <p className="mt-1 hidden text-xs text-[#777] sm:block">{t("header.subtitle")}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2 sm:gap-3">
        <span
          className={`hidden rounded-full px-3 py-1.5 text-[11px] font-semibold sm:inline-block ${
            mode === "live" ? "bg-[#eaf8f0] text-[#249c62]" : "bg-[#fff4e4] text-[#a45e00]"
          }`}
        >
          {mode === "live" ? t("header.liveData") : t("header.demoData")}
        </span>
        <span className="hidden text-[11px] text-[#7b7b7b] md:inline-block">
          {t("header.updated")} {updated}
        </span>

        <button
          type="button"
          onClick={() => dispatch({ type: "SET_LANGUAGE", language: lang === "en" ? "fr" : "en" })}
          className="inline-flex items-center gap-1.5 rounded-md bg-white px-3 py-2 text-xs font-medium text-gray-800 shadow-md hover:bg-black hover:text-white"
          aria-label="Toggle language"
        >
          <Globe size={15} />
          {lang.toUpperCase()}
        </button>

        <button
          type="button"
          onClick={handleToggleAgent}
          className={`inline-flex items-center gap-2 rounded-md px-3 py-2 text-xs font-medium shadow-md sm:text-sm ${
            state.agentActive ? "bg-[#eaf8f0] text-[#249c62] hover:bg-[#d8f0e2]" : "bg-[#fdebec] text-[#cb3444] hover:bg-[#fadbe0]"
          }`}
        >
          {state.agentActive ? <PlayCircle size={16} /> : <PauseCircle size={16} />}
          {state.agentActive ? t("btn.agentActive") : t("btn.agentPaused")}
        </button>

        <button
          type="button"
          onClick={handleProcessInbox}
          disabled={processing}
          className="inline-flex items-center gap-2 rounded-md bg-white px-3 py-2 text-xs font-medium text-gray-800 shadow-md hover:bg-black hover:text-white disabled:cursor-progress disabled:opacity-60 sm:px-4 sm:text-sm"
        >
          <Mail size={16} className={processing ? "animate-pulse" : ""} />
          {t("btn.processInbox")}
        </button>

        <button
          type="button"
          onClick={onRefresh}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-md bg-white px-3 py-2 text-xs font-medium text-gray-800 shadow-md hover:bg-black hover:text-white disabled:cursor-progress disabled:opacity-60 sm:px-4 sm:text-sm"
        >
          <RefreshCcw size={16} className={loading ? "animate-spin" : ""} />
          {t("btn.refresh")}
        </button>

        <div className="relative" ref={menuRef}>
          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            className="inline-flex items-center gap-2 rounded-md bg-white px-3 py-2 text-xs font-medium text-gray-800 shadow-md hover:bg-black hover:text-white sm:text-sm"
          >
            <User size={16} />
            <ChevronDown size={14} />
          </button>
          <AnimatePresence>
            {menuOpen ? (
              <motion.div
                initial={{ opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                role="menu"
                className="absolute right-0 z-[90] mt-2 w-56 rounded-xl bg-white p-2 text-sm shadow-2xl"
              >
                <div className="border-b border-gray-100 px-3 py-2">
                  <p className="font-outfit text-xs font-semibold text-gray-900">{state.currentUser.username}</p>
                  {isAdmin(state.currentUser.role) ? (
                    <>
                      <p className="text-[11px] text-gray-500">{state.currentUser.name}</p>
                      <p className="text-[11px] text-gray-400">{t("role.admin")}</p>
                    </>
                  ) : (
                    <p className="text-[11px] text-gray-400">{t("role.user")}</p>
                  )}
                </div>
                <button type="button" onClick={() => openField("username")} className="menu-item">
                  {t("profile.changeUsername")}
                </button>
                <button type="button" onClick={() => openField("password")} className="menu-item">
                  {t("profile.changePassword")}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setMenuOpen(false);
                    onRequestLogout();
                  }}
                  className="menu-item text-[#cb3444]"
                >
                  {t("profile.logout")}
                </button>
              </motion.div>
            ) : null}
          </AnimatePresence>
        </div>
      </div>

      {activeForm ? (
        <FormModal
          title={activeForm === "username" ? t("modal.changeUsername") : t("modal.changePassword")}
          onClose={() => setActiveForm(null)}
          onSubmit={saveField}
        >
          <FormField label={activeForm === "username" ? t("field.username") : t("field.newPassword")}>
            <input
              type={activeForm === "password" ? "password" : "text"}
              value={fieldValue}
              onChange={(event) => setFieldValue(event.target.value)}
              className={inputClass}
              autoFocus
            />
          </FormField>
        </FormModal>
      ) : null}
    </header>
  );
}

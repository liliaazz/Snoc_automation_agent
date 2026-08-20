import { Globe, LogIn, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { translate } from "../../i18n/translations";
import { login } from "../../services/authService";

const STORAGE_KEY = "snoc.dashboard.state.v1";

function readPersistedLanguage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed.language === "fr" ? "fr" : "en";
  } catch {
    return "en";
  }
}

function persistLanguage(language) {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...parsed, language }));
  } catch {
    // Storage is optional.
  }
}

export default function Login({ onAuthenticated }) {
  const [lang, setLang] = useState(readPersistedLanguage);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const t = (key) => translate(lang, key);

  function toggleLang() {
    const next = lang === "en" ? "fr" : "en";
    setLang(next);
    persistLanguage(next);
  }

  async function handleLogin(event) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await login(username, password);
      onAuthenticated();
    } catch (reason) {
      setError(reason?.message || t("login.errorInvalid"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#0B0D0F] p-4">
      <div className="w-full max-w-sm rounded-2xl border border-[#292C31] bg-[#111317] p-8 shadow-2xl">
        <div className="mb-6 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="h-[10px] w-[10px] shrink-0 rounded-full bg-[#F20521] shadow-[0_0_12px_rgba(242,5,33,0.55)]" />
            <span className="font-oxanium text-sm font-extrabold tracking-[0.12em] text-white">SNOC</span>
          </div>
          <button
            type="button"
            onClick={toggleLang}
            className="inline-flex items-center gap-1.5 rounded-md border border-[#292C31] bg-[#15171A] px-2.5 py-1.5 text-xs font-medium text-[#A4A9B1] hover:border-[#F20521] hover:text-white"
            aria-label="Toggle language"
          >
            <Globe size={14} />
            {lang.toUpperCase()}
          </button>
        </div>

        <ShieldCheck size={28} className="mb-4 text-[#F20521]" />
        <h1 className="font-oxanium text-xl font-semibold text-white">{t("login.title")}</h1>
        <p className="mt-2 text-sm leading-6 text-[#858B94]">{t("login.subtitle")}</p>

        <form onSubmit={handleLogin} className="mt-6 space-y-4">
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-[#A4A9B1]">
              {t("login.username")}
            </span>
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              required
              autoFocus
              placeholder={t("login.usernamePlaceholder")}
              className="w-full rounded-md border border-[#292C31] bg-[#0B0D0F] px-3 py-2.5 text-sm text-white outline-none transition focus:border-[#F20521]"
            />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-[#A4A9B1]">
              {t("login.password")}
            </span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
              placeholder={t("login.passwordPlaceholder")}
              className="w-full rounded-md border border-[#292C31] bg-[#0B0D0F] px-3 py-2.5 text-sm text-white outline-none transition focus:border-[#F20521]"
            />
          </label>
          {error ? <p className="text-xs font-medium text-[#F20521]">{error}</p> : null}
          <button
            type="submit"
            disabled={submitting}
            className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-[#F20521] px-4 py-2.5 text-sm font-semibold text-white shadow-[0_7px_20px_rgba(242,5,33,0.22)] transition hover:bg-[#d40419] disabled:cursor-progress disabled:opacity-70"
          >
            <LogIn size={16} />
            {submitting ? t("login.submitting") : t("login.submit")}
          </button>
        </form>
      </div>
    </div>
  );
}

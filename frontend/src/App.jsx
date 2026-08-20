import { useCallback, useEffect, useState } from "react";
import DashboardHeader from "./components/DashboardHeader";
import Sidebar from "./components/Sidebar";
import AccountManagement from "./components/pages/AccountManagement";
import Audit from "./components/pages/Audit";
import BusinessAnalysis from "./components/pages/BusinessAnalysis";
import Configuration from "./components/pages/Configuration";
import Emails from "./components/pages/Emails";
import Home from "./components/pages/Home";
import Login from "./components/pages/Login";
import OperationAnalysis from "./components/pages/OperationAnalysis";
import Parametre from "./components/pages/Parametre";
import ConfirmDialog from "./components/common/ConfirmDialog";
import ToastContainer from "./components/common/ToastContainer";
import { AppProvider, useApp } from "./context/AppContext";
import { useTranslation } from "./i18n/useTranslation";
import { useDashboard } from "./hooks/useDashboard";
import { canAccessPage } from "./utils/permissions";
import { initializeAuth, logout } from "./services/authService";

const PAGES = {
  home: { titleKey: "nav.home", component: Home },
  emails: { titleKey: "nav.emails", component: Emails },
  audit: { titleKey: "nav.audit", component: Audit },
  bizanalysis: { titleKey: "nav.bizanalysis", component: BusinessAnalysis },
  opanalysis: { titleKey: "nav.opanalysis", component: OperationAnalysis },
  configuration: { titleKey: "nav.configuration", component: Configuration },
  parametre: { titleKey: "nav.parametre", component: Parametre },
  accounts: { titleKey: "nav.accounts", component: AccountManagement },
};

const RANGE_BUTTONS = [
  ["week", "This Week"],
  ["today", "Today"],
  ["month", "This Month"],
  ["year", "This Year"],
];

function AppShell({ onLogout }) {
  const { state, dispatch } = useApp();
  const { t } = useTranslation();
  const role = state.currentUser.role;

  const initialHash = window.location.hash.replace("#", "");
  const safeInitial = PAGES[initialHash] && canAccessPage(initialHash, role) ? initialHash : "home";
  const [activePage, setActivePage] = useState(safeInitial);
  const [pageParams, setPageParams] = useState(null);
  const [range, setRange] = useState("week");
  const [logoutOpen, setLogoutOpen] = useState(false);
  const { data, loading, error, partialErrors, refresh } = useDashboard({ range });

  useEffect(() => {
    if (data.mode !== "live") return;
    dispatch({ type: "SET_AGENT_ACTIVE", agentActive: data.agentActive });
    dispatch({ type: "SET_USERS", users: data.accounts || [] });
    dispatch({ type: "SET_WHITELIST", whitelist: data.whitelist || [] });
  }, [data.mode, data.agentActive, data.accounts, data.whitelist, dispatch]);

  // Route guard: block direct hash/URL navigation to admin-only pages too,
  // not just hide the sidebar entry. `params` carries optional navigation
  // context (e.g. a status filter to preset) for the destination page.
  const changePage = useCallback(
    (id, params = null) => {
      if (!PAGES[id]) return;
      if (!canAccessPage(id, role)) {
        setActivePage("home");
        setPageParams(null);
        window.location.hash = "home";
        return;
      }
      setActivePage(id);
      setPageParams(params);
    },
    [role],
  );

  useEffect(() => {
    function onHash() {
      const hash = window.location.hash.replace("#", "");
      if (PAGES[hash]) changePage(hash);
    }
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [changePage]);

  useEffect(() => {
    if (!canAccessPage(activePage, role)) {
      setActivePage("home");
      window.location.hash = "home";
    }
  }, [role, activePage]);

  function handleLogout() {
    window.location.hash = "";
    logout();
    onLogout();
  }

  const page = PAGES[activePage] || PAGES.home;
  const PageComponent = page.component;

  return (
    <div className="application-shell">
      <Sidebar
        activePage={activePage}
        onChange={changePage}
        currentUser={state.currentUser}
        onRequestLogout={() => setLogoutOpen(true)}
      />

      <main className="dashboard-main">
        <DashboardHeader
          title={t(page.titleKey)}
          mode={data.mode}
          generatedAt={data.generatedAt}
          loading={loading}
          onRefresh={refresh}
          onRequestLogout={() => setLogoutOpen(true)}
        />

        

        <div className="dashboard-page-content">
          <PageComponent data={data} onNavigate={changePage} params={pageParams} onRefresh={refresh} />
        </div>

        
      </main>

      {logoutOpen ? (
        <ConfirmDialog
          title={t("profile.logout")}
          message={t("logout.confirm")}
          confirmLabel={t("btn.logout")}
          danger
          onConfirm={handleLogout}
          onClose={() => setLogoutOpen(false)}
        />
      ) : null}

      <ToastContainer />
    </div>
  );
}

export default function App() {
  const [authState, setAuthState] = useState("loading");

  useEffect(() => {
    initializeAuth()
      .then((authenticated) => setAuthState(authenticated ? "authenticated" : "anonymous"))
      .catch(() => setAuthState("anonymous"));
  }, []);

  if (authState === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#0B0D0F] text-sm text-[#A4A9B1]">
        Checking local session…
      </div>
    );
  }

  if (authState !== "authenticated") {
    return <Login onAuthenticated={() => setAuthState("authenticated")} />;
  }

  return (
    <AppProvider>
      <AppShell onLogout={() => setAuthState("anonymous")} />
    </AppProvider>
  );
}

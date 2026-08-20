import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef } from "react";
import {
  AUDIT_LOGS,
  ESCALATIONS,
  RULES,
} from "../data/mockData";

const STORAGE_KEY = "snoc.dashboard.state.v1";

function loadPersisted() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function readCurrentUser() {
  try {
    const stored = JSON.parse(sessionStorage.getItem("user") || "{}");
    return {
      name: stored.name || stored.fullname || "Authenticated user",
      username: stored.username || stored.email || "unknown",
      role: stored.role || "user",
    };
  } catch {
    return { name: "SNOC Administrator", username: "admin", role: "admin" };
  }
}

function createInitialState() {
  const persisted = loadPersisted();
  return {
    currentUser: readCurrentUser(),
    language: persisted.language || "en",
    agentActive: persisted.agentActive ?? true,
    alerts: [],
    escalations: persisted.escalations || ESCALATIONS,
    auditLogs: AUDIT_LOGS,
    treatmentOverrides: persisted.treatmentOverrides || {},
    whitelist: persisted.whitelist || [],
    users: persisted.users || [],
    rules: persisted.rules || RULES,
    toasts: [],
  };
}

function reducer(state, action) {
  switch (action.type) {
    case "SET_LANGUAGE":
      return { ...state, language: action.language };
    case "SET_AGENT_ACTIVE":
      return { ...state, agentActive: Boolean(action.agentActive) };
    case "TOGGLE_AGENT": {
      const agentActive = !state.agentActive;
      const alerts = agentActive
        ? state.alerts
        : [
            {
              id: `AL-agent-${Date.now()}`,
              severity: "Warning",
              category: "Agent",
              title: "Agent paused by operator",
              description: "The SNOC AI agent was manually paused and is not processing new requests.",
              time: "just now",
              status: "Open",
              target: "configuration",
            },
            ...state.alerts,
          ];
      return { ...state, agentActive, alerts };
    }
    case "UPDATE_ESCALATION": {
      const { id, status, note, updatedBy, updatedAt } = action.payload;
      const override = {
        treatmentStatus: status,
        note: note ?? "",
        updatedBy: updatedBy ?? state.currentUser.name,
        updatedAt: updatedAt ?? new Date().toISOString(),
      };
      return {
        ...state,
        treatmentOverrides: { ...state.treatmentOverrides, [id]: override },
        escalations: state.escalations.map((esc) =>
          esc.id === id ? { ...esc, ...override } : esc,
        ),
      };
    }
    case "APPEND_AUDIT_LOGS":
      return { ...state, auditLogs: [...action.records, ...state.auditLogs] };
    case "ADD_WHITELIST":
      return { ...state, whitelist: [...state.whitelist, action.entry] };
    case "REMOVE_WHITELIST":
      return { ...state, whitelist: state.whitelist.filter((w) => w.email !== action.email) };
    case "SET_WHITELIST":
      return { ...state, whitelist: action.whitelist };
    case "SET_USERS":
      return { ...state, users: action.users };
    case "ADD_USER":
      return { ...state, users: [...state.users, action.user] };
    case "UPDATE_USER":
      return { ...state, users: state.users.map((u) => (u.id === action.user.id ? action.user : u)) };
    case "DELETE_USER":
      return { ...state, users: state.users.filter((u) => u.id !== action.id) };
    case "SET_USER_STATUS":
      return {
        ...state,
        users: state.users.map((u) => (u.id === action.id ? { ...u, status: action.status } : u)),
      };
    case "TOGGLE_RULE":
      return {
        ...state,
        rules: state.rules.map((r) =>
          r.id === action.id
            ? { ...r, enabled: !r.enabled, updatedBy: state.currentUser.name, updatedAt: new Date().toISOString() }
            : r,
        ),
      };
    case "UPDATE_RULE_THRESHOLD":
      return {
        ...state,
        rules: state.rules.map((r) =>
          r.id === action.id
            ? { ...r, threshold: action.threshold, updatedBy: state.currentUser.name, updatedAt: new Date().toISOString() }
            : r,
        ),
      };
    case "SET_CURRENT_USER_FIELD":
      return { ...state, currentUser: { ...state.currentUser, ...action.fields } };
    case "PUSH_TOAST":
      return { ...state, toasts: [...state.toasts, action.toast] };
    case "DISMISS_TOAST":
      return { ...state, toasts: state.toasts.filter((tst) => tst.id !== action.id) };
    default:
      return state;
  }
}

const AppContext = createContext(null);

export function AppProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, undefined, createInitialState);
  const toastCounter = useRef(0);

  useEffect(() => {
    const toPersist = {
      language: state.language,
      agentActive: state.agentActive,
      escalations: state.escalations,
      treatmentOverrides: state.treatmentOverrides,
      whitelist: state.whitelist,
      users: state.users,
      rules: state.rules,
    };
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(toPersist));
    } catch {
      // storage unavailable — non-fatal, state stays in memory only
    }
  }, [state.language, state.agentActive, state.escalations, state.treatmentOverrides, state.whitelist, state.users, state.rules]);

  const pushToast = useCallback((message, tone = "info") => {
    toastCounter.current += 1;
    const id = `toast-${Date.now()}-${toastCounter.current}`;
    dispatch({ type: "PUSH_TOAST", toast: { id, message, tone } });
    setTimeout(() => dispatch({ type: "DISMISS_TOAST", id }), 3800);
  }, []);

  const dismissToast = useCallback((id) => dispatch({ type: "DISMISS_TOAST", id }), []);

  const value = useMemo(() => ({ state, dispatch, pushToast, dismissToast }), [state, pushToast, dismissToast]);

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used within AppProvider");
  return ctx;
}

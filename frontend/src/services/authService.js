const viteEnv = import.meta.env || {};
const API_BASE = String(viteEnv.VITE_API_BASE_URL || "").replace(/\/$/, "");
const url = (path) => `${API_BASE}${path}`;

function sessionStore() {
  return typeof sessionStorage === "undefined" ? null : sessionStorage;
}

function persist(token, user) {
  const storage = sessionStore();
  storage?.setItem("token", token);
  storage?.setItem("user", JSON.stringify(user));
}

export function clearSession() {
  const storage = sessionStore();
  storage?.removeItem("token");
  storage?.removeItem("user");
}

export async function initializeAuth() {
  const token = sessionStore()?.getItem("token");
  if (!token) return false;
  try {
    const response = await fetch(url("/api/auth/me"), {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) {
      clearSession();
      return false;
    }
    persist(token, await response.json());
    return true;
  } catch {
    clearSession();
    return false;
  }
}

export async function login(username, password) {
  const response = await fetch(url("/api/auth/login"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(payload?.detail || "Sign in failed");
  persist(payload.access_token, payload.user);
  return payload.user;
}

export function logout() {
  clearSession();
}

export function getAccessToken() {
  return sessionStore()?.getItem("token") ?? null;
}

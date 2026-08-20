import { getAccessToken } from "./authService.js";

const API_BASE = String(import.meta.env?.VITE_API_BASE_URL || "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(message, { status = 0, path = "", payload = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.path = path;
    this.payload = payload;
  }
}

function buildUrl(path) {
  if (/^https?:\/\//i.test(path)) return path;
  return `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

async function parsePayload(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json().catch(() => null);
  }
  const text = await response.text().catch(() => "");
  return text || null;
}

export async function requestJson(path, options = {}) {
  const { signal, headers, body, method = "GET", ...rest } = options;
  const accessToken = await getAccessToken();
  const response = await fetch(buildUrl(path), {
    method,
    signal,
    headers: {
      Accept: "application/json",
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    ...rest,
  });

  const payload = await parsePayload(response);
  if (!response.ok) {
    const detail = payload?.detail || payload?.error || response.statusText || "Request failed";
    throw new ApiError(String(detail), {
      status: response.status,
      path,
      payload,
    });
  }
  return payload;
}

export async function optionalJson(path, options = {}) {
  try {
    return await requestJson(path, options);
  } catch (error) {
    if (error instanceof ApiError && [404, 405, 501, 503].includes(error.status)) {
      return null;
    }
    throw error;
  }
}

export default {
  requestJson,
  optionalJson,
};

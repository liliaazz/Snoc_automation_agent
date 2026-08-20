import backendApi from "./backendApi.js";
import adapters from "./backendAdapters.js";

const cache = new Map();

export function getCachedTrace(publicReference) {
  if (!publicReference) return null;
  return cache.get(publicReference) || null;
}

export async function loadTrace(publicReference, { signal, force = false } = {}) {
  if (!publicReference) return null;
  if (!force && cache.has(publicReference)) return cache.get(publicReference);
  const raw = await backendApi.requestTrace(publicReference, signal);
  if (!raw) return null;
  const normalized = adapters.normalizeRequestTrace(raw);
  cache.set(publicReference, normalized);
  return normalized;
}

export function clearTraceCache() {
  cache.clear();
}

export default {
  getCachedTrace,
  loadTrace,
  clearTraceCache,
};

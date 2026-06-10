const API_BASE = "";
const SENSITIVE_TOKENS = ["password", "secret", "token", "authorization", "cookie", "api_key", "session"];

export class ApiClientError extends Error {
  constructor(message, details = {}) {
    super(message);
    this.name = "ApiClientError";
    this.code = details.code || "API_ERROR";
    this.details = details.details || [];
    this.status = details.status || 0;
    this.meta = details.meta || {};
  }
}

export function buildQuery(params = {}) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      query.set(key, value);
    }
  });
  const text = query.toString();
  return text ? `?${text}` : "";
}

export async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  const contentType = response.headers.get("content-type") || "";
  const raw = contentType.includes("application/json") ? await response.json() : await response.text();
  const envelope = unwrapEnvelope(raw, response.status);
  if (!response.ok && !(raw && raw.ok === false)) {
    const detail = raw && typeof raw === "object" ? raw.detail : raw;
    const message = typeof detail === "string" ? detail : (detail?.message || response.statusText || "API request failed");
    throw new ApiClientError(message, { status: response.status, details: Array.isArray(detail) ? detail : [] });
  }
  return envelope;
}

export function unwrapEnvelope(payload, status = 200) {
  if (payload && typeof payload === "object" && "ok" in payload && "data" in payload && "error" in payload && "meta" in payload) {
    if (payload.ok === false) {
      const error = payload.error || {};
      throw new ApiClientError(error.message || "API request failed", {
        code: error.code,
        details: error.details,
        status,
        meta: payload.meta,
      });
    }
    return { data: payload.data, meta: payload.meta || {} };
  }
  return { data: redactSensitive(payload), meta: {} };
}

export function apiGet(path, params) {
  return apiRequest(`${path}${buildQuery(params)}`);
}

export function apiPost(path, body = undefined) {
  return apiRequest(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export function apiPut(path, body = undefined) {
  return apiRequest(path, {
    method: "PUT",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export function apiDelete(path) {
  return apiRequest(path, { method: "DELETE" });
}

export function redactSensitive(value) {
  if (Array.isArray(value)) {
    return value.map((item) => redactSensitive(item));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => {
      const lowered = String(key).toLowerCase();
      const hidden = SENSITIVE_TOKENS.some((token) => lowered.includes(token));
      return [key, hidden ? redactSensitiveValue(item) : redactSensitive(item)];
    }));
  }
  return value;
}

function redactSensitiveValue(value) {
  if (Array.isArray(value)) {
    return value.map((item) => redactSensitiveValue(item));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, redactSensitiveValue(item)]));
  }
  if (value === null || value === undefined) {
    return value;
  }
  return "***REDACTED***";
}

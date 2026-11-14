// Empty base = same origin; Vite proxies /api to the backend in dev.
// For a deployed backend set VITE_API_BASE, e.g. https://api.pulse.example
const BASE = import.meta.env.VITE_API_BASE ?? "";

// Where the raw server-rendered protected page lives (FastAPI serves /secret directly).
export const SECRET_PAGE_URL =
  import.meta.env.VITE_SECRET_URL ?? `${window.location.protocol}//${window.location.hostname}:8000/secret`;

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include", // send the session cookie
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const detail = Array.isArray(data?.detail) ? data.detail.map((d) => d.msg).join(", ") : data?.detail;
    throw new ApiError(detail || `Request failed (${res.status})`, res.status);
  }
  return data;
}

const post = (path, body) => request(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });

export const api = {
  health: () => request("/api/health"),

  // access control
  session: () => request("/api/session"),
  unlock: (sim_outcome) => post("/api/session/unlock", sim_outcome ? { sim_outcome } : {}),
  claim: (request_id, claim) => post("/api/session/claim", { request_id, claim }),
  cancelUnlock: () => post("/api/session/cancel"),
  lock: () => post("/api/session/lock"),
  vault: () => request("/api/vault"),

  // payments
  listPayments: () => request("/api/payments?limit=20"),
  getPayment: (id) => request(`/api/payments/${id}`),
  createPayment: (body) => post("/api/payments", body),
  cancelPayment: (id) => post(`/api/payments/${id}/cancel`),

  // receipts
  verifyReceipts: () => request("/api/receipts/verify"),

  // simulator
  setSim: (body) => post("/api/sim/device", body),
};

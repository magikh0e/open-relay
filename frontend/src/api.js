// Thin fetch wrapper. Stores tokens in localStorage and transparently
// refreshes the access token on a 401 once before giving up.

import { API_BASE } from "./config.js";

const ACCESS = "chat_access";
const REFRESH = "chat_refresh";

export const tokens = {
  get access() {
    return localStorage.getItem(ACCESS) || "";
  },
  get refresh() {
    return localStorage.getItem(REFRESH) || "";
  },
  set({ access_token, refresh_token }) {
    localStorage.setItem(ACCESS, access_token);
    localStorage.setItem(REFRESH, refresh_token);
  },
  clear() {
    localStorage.removeItem(ACCESS);
    localStorage.removeItem(REFRESH);
  },
};

export async function refreshAccess() {
  const rt = tokens.refresh;
  if (!rt) return false;
  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: rt }),
  });
  if (!res.ok) {
    tokens.clear();
    return false;
  }
  tokens.set(await res.json());
  return true;
}

export async function api(path, { method = "GET", body, auth = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth && tokens.access) headers.Authorization = `Bearer ${tokens.access}`;

  const opts = { method, headers };
  if (body !== undefined) opts.body = JSON.stringify(body);

  let res = await fetch(`${API_BASE}${path}`, opts);

  if (res.status === 401 && auth && (await refreshAccess())) {
    headers.Authorization = `Bearer ${tokens.access}`;
    res = await fetch(`${API_BASE}${path}`, opts);
  }

  if (res.status === 204) return null;

  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.detail || res.statusText;
    throw new Error(typeof detail === "string" ? detail : "Request failed");
  }
  return data;
}

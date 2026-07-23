import { createContext, useContext, useEffect, useState } from "react";
import { api, tokens } from "./api.js";

const AuthContext = createContext(null);

// After an OAuth redirect the backend appends tokens (or an error) to the URL
// fragment. Consume it, store tokens, and scrub the URL.
function consumeOAuthHash() {
  const h = window.location.hash;
  if (!h || (!h.includes("access=") && !h.includes("error="))) return null;
  const params = new URLSearchParams(h.slice(1));
  history.replaceState(null, "", window.location.pathname + window.location.search);
  const access = params.get("access");
  const refresh = params.get("refresh");
  if (access && refresh) {
    tokens.set({ access_token: access, refresh_token: refresh });
    return { ok: true };
  }
  return { ok: false, error: params.get("error") || "sign-in failed" };
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [authError, setAuthError] = useState("");

  useEffect(() => {
    const oauth = consumeOAuthHash();
    if (oauth && !oauth.ok) setAuthError(oauth.error);
    if (!tokens.access) {
      setLoading(false);
      return;
    }
    api("/users/me")
      .then(setUser)
      .catch(() => tokens.clear())
      .finally(() => setLoading(false));
  }, []);

  async function login(username_or_email, password) {
    const pair = await api("/auth/login", {
      method: "POST",
      auth: false,
      body: { username_or_email, password },
    });
    tokens.set(pair);
    setUser(await api("/users/me"));
  }

  async function register(fields) {
    const pair = await api("/auth/register", {
      method: "POST",
      auth: false,
      body: fields,
    });
    tokens.set(pair);
    setUser(await api("/users/me"));
  }

  function logout() {
    tokens.clear();
    setUser(null);
  }

  function updateUser(patch) {
    setUser((u) => (u ? { ...u, ...patch } : u));
  }

  return (
    <AuthContext.Provider
      value={{ user, loading, login, register, logout, updateUser, authError }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);

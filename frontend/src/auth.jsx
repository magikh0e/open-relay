import { createContext, useContext, useEffect, useState } from "react";
import { api, tokens } from "./api.js";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
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
      value={{ user, loading, login, register, logout, updateUser }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);

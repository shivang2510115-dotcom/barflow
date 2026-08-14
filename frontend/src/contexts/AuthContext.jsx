import React, { createContext, useContext, useEffect, useState } from "react";
import { api, TOKEN_KEY, formatApiErrorDetail } from "@/lib/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null); // null=loading, false=guest, obj=user
  const [error, setError] = useState("");

  useEffect(() => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) {
      setUser(false);
      return;
    }
    api
      .get("/auth/me")
      .then((r) => setUser(r.data))
      .catch(() => {
        localStorage.removeItem(TOKEN_KEY);
        setUser(false);
      });
  }, []);

  const login = async (email, password) => {
    setError("");
    try {
      const { data } = await api.post("/auth/login", { email, password });
      localStorage.setItem(TOKEN_KEY, data.token);
      // The login response is a summary of the account; /auth/me is the payload the rest
      // of the app is written against — it carries the screen permissions the navigation
      // filters on. Reading it once here means a session that has just signed in and one
      // that reloaded an hour later are the same object, rather than the sidebar being
      // empty until the first refresh. If it fails, the summary still gets somebody in.
      const me = await api
        .get("/auth/me")
        .then((r) => r.data)
        .catch(() => data.user);
      setUser(me);
      return { ok: true, user: me };
    } catch (e) {
      const msg = formatApiErrorDetail(e.response?.data?.detail) || e.message;
      setError(msg);
      return { ok: false, error: msg };
    }
  };

  const logout = () => {
    localStorage.removeItem(TOKEN_KEY);
    setUser(false);
  };

  return (
    <AuthContext.Provider value={{ user, login, logout, error }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);

import { useEffect, useState } from "react";
import { authFetch, getToken, logout as clearAuth } from "../services/auth";

export function useAuth() {
  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => {
    if (!getToken()) {
      setAuthed(false);
      return;
    }
    authFetch("/api/auth/me").then((res) => setAuthed(res.ok));
  }, []);

  function login() {
    setAuthed(true);
  }

  function logout() {
    clearAuth();
    setAuthed(false);
  }

  return { authed, login, logout };
}

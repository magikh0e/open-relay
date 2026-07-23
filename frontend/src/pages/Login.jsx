import { useState } from "react";
import { useAuth } from "../auth.jsx";

export default function Login() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState({
    username_or_email: "",
    username: "",
    email: "",
    password: "",
    display_name: "",
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (mode === "login") {
        await login(form.username_or_email, form.password);
      } else {
        await register({
          username: form.username,
          email: form.email,
          password: form.password,
          display_name: form.display_name || form.username,
        });
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center">
      <form className="auth-card" onSubmit={submit}>
        <h1>Chat</h1>
        <div className="tabs">
          <button
            type="button"
            className={mode === "login" ? "active" : ""}
            onClick={() => setMode("login")}
          >
            Log in
          </button>
          <button
            type="button"
            className={mode === "register" ? "active" : ""}
            onClick={() => setMode("register")}
          >
            Register
          </button>
        </div>

        {mode === "login" ? (
          <input
            placeholder="Username or email"
            value={form.username_or_email}
            onChange={set("username_or_email")}
            autoFocus
          />
        ) : (
          <>
            <input
              placeholder="Username"
              value={form.username}
              onChange={set("username")}
              autoFocus
            />
            <input
              placeholder="Email"
              type="email"
              value={form.email}
              onChange={set("email")}
            />
            <input
              placeholder="Display name (optional)"
              value={form.display_name}
              onChange={set("display_name")}
            />
          </>
        )}

        <input
          placeholder="Password"
          type="password"
          value={form.password}
          onChange={set("password")}
        />

        {error && <div className="error">{error}</div>}

        <button className="primary" disabled={busy}>
          {busy ? "…" : mode === "login" ? "Log in" : "Create account"}
        </button>
      </form>
    </div>
  );
}

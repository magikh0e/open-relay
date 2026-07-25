import { useEffect, useState } from "react";
import { api } from "../api.js";
import { SERVER, serverLabel } from "../config.js";
import { useAuth } from "../auth.jsx";
import { APP_NAME, APP_VERSION } from "../version.js";
import ServerPicker from "../components/ServerPicker.jsx";

const PROVIDER_LABELS = {
  google: "Continue with Google",
  discord: "Continue with Discord",
};

export default function Login() {
  const { login, register, authError } = useAuth();
  const [mode, setMode] = useState("login");
  const [providers, setProviders] = useState([]);
  const [inviteRequired, setInviteRequired] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  useEffect(() => {
    api("/auth/oauth/providers", { auth: false })
      .then((p) => setProviders(p || []))
      .catch(() => {});
    api("/auth/registration", { auth: false })
      .then((r) => setInviteRequired(!!r?.invite_required))
      .catch(() => {});
  }, []);
  const [form, setForm] = useState({
    username_or_email: "",
    username: "",
    email: "",
    password: "",
    display_name: "",
    invite_code: "",
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
          invite_code: form.invite_code || undefined,
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
        <img
          className="brand-logo"
          src="/openrelay.webp"
          alt={APP_NAME}
          width="1000"
          height="546"
        />
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
            {inviteRequired && (
              <input
                placeholder="Invite code"
                value={form.invite_code}
                onChange={set("invite_code")}
              />
            )}
          </>
        )}

        <input
          placeholder="Password"
          type="password"
          value={form.password}
          onChange={set("password")}
        />

        {(error || authError) && (
          <div className="error">{error || authError}</div>
        )}

        <button className="primary" disabled={busy}>
          {busy ? "…" : mode === "login" ? "Log in" : "Create account"}
        </button>

        {providers.length > 0 && (
          <>
            <div className="oauth-divider">
              <span>or</span>
            </div>
            {providers.map((p) => (
              <button
                key={p}
                type="button"
                className={`oauth-btn oauth-${p}`}
                onClick={() => {
                  // Build against the validated http(s) origin (SERVER is
                  // scheme-checked in config.js) with an encoded provider id, so
                  // neither the server value nor the provider list can inject a
                  // scheme into the navigation.
                  const target = new URL(
                    `/api/auth/oauth/${encodeURIComponent(p)}/start`,
                    SERVER || location.origin
                  );
                  window.location.href = target.href;
                }}
              >
                {PROVIDER_LABELS[p] || `Continue with ${p}`}
              </button>
            ))}
          </>
        )}

        <div className="app-version">
          Server:{" "}
          <button
            type="button"
            className="policy-link"
            onClick={() => setPickerOpen(true)}
            title="Connect to a different Open Relay server"
          >
            {serverLabel()}
          </button>
        </div>
        <div className="app-version">
          {APP_NAME} v{APP_VERSION}
          <span className="sep">·</span>
          by magikh0e
        </div>
        <div className="app-version app-links">
          <a className="policy-link" href="/guide.html" target="_blank" rel="noreferrer">
            Guide
          </a>
          <span className="sep">·</span>
          <a className="policy-link" href="/developers.html" target="_blank" rel="noreferrer">
            Developers
          </a>
          <span className="sep">·</span>
          <a className="policy-link" href="/about.html" target="_blank" rel="noreferrer">
            About
          </a>
          <span className="sep">·</span>
          <a className="policy-link" href="/privacy.html" target="_blank" rel="noreferrer">
            Privacy
          </a>
          <span className="sep">·</span>
          <a className="policy-link" href="/terms.html" target="_blank" rel="noreferrer">
            Terms
          </a>
          <span className="sep">·</span>
          <a
            className="policy-link"
            href="https://github.com/magikh0e/open-relay"
            target="_blank"
            rel="noreferrer"
          >
            GitHub
          </a>
        </div>
      </form>
      {pickerOpen && <ServerPicker onClose={() => setPickerOpen(false)} />}
    </div>
  );
}

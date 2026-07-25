import { useState } from "react";
import { api } from "../api.js";
import { useDialog } from "../useDialog.js";
import {
  cacheUnlockedKey,
  exportPublicKey,
  generateKeyPair,
  unwrapPrivateKey,
  wrapPrivateKey,
} from "../e2ee.js";

// Two-mode dialog for direct-message encryption:
//   "setup"  — first time: pick a passphrase, generate a keypair, publish it
//   "unlock" — later sessions: re-derive the private key from the passphrase
export default function E2EESetup({ mode, onUnlocked, onClose }) {
  const [pass, setPass] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const setup = mode === "setup";
  const dialogRef = useDialog(onClose);

  async function submit(e) {
    e.preventDefault();
    setError("");
    if (setup && pass !== confirm) {
      setError("Those passphrases don't match.");
      return;
    }
    if (setup && pass.length < 8) {
      setError("Use at least 8 characters.");
      return;
    }
    setBusy(true);
    try {
      let privateKey;
      if (setup) {
        const kp = await generateKeyPair();
        const public_key = await exportPublicKey(kp.publicKey);
        const wrapped = await wrapPrivateKey(kp.privateKey, pass);
        await api("/keys/me", {
          method: "PUT",
          body: { public_key, ...wrapped },
        });
        privateKey = kp.privateKey;
      } else {
        const bundle = await api("/keys/me");
        privateKey = await unwrapPrivateKey(bundle, pass);
      }
      await cacheUnlockedKey(privateKey);
      onUnlocked(privateKey);
    } catch (err) {
      setError(err.message || "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form
        className="modal e2ee-modal"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
        onSubmit={submit}
      >
        <h3>{setup ? "Encrypt your direct messages" : "Unlock your messages"}</h3>

        {setup ? (
          <>
            <p className="muted small">
              Your messages get a key that only you hold. Pick a passphrase to
              protect it; you'll enter this on each new device.
            </p>
            <div className="e2ee-warn">
              There is no reset. If you forget this passphrase, your encrypted
              messages are gone for good; not even the server admin can recover
              them.
            </div>
          </>
        ) : (
          <p className="muted small">
            Enter your encryption passphrase to read and send direct messages on
            this device.
          </p>
        )}

        <input
          type="password"
          placeholder="Passphrase"
          value={pass}
          autoFocus
          onChange={(e) => setPass(e.target.value)}
        />
        {setup && (
          <input
            type="password"
            placeholder="Confirm passphrase"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        )}

        {error && <div className="error">{error}</div>}

        <div className="e2ee-actions">
          <button className="primary" disabled={busy || !pass}>
            {busy ? "Working…" : setup ? "Enable encryption" : "Unlock"}
          </button>
          <button type="button" className="mini ghost" onClick={onClose}>
            Not now
          </button>
        </div>
      </form>
    </div>
  );
}

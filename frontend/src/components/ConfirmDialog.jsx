import { useDialog } from "../useDialog.js";

// In-app replacement for window.confirm / window.alert. Native dialogs break
// the app's visual language and can't be styled or made dismissible on Esc.
//
// Used via ChatShell's `ask()` helper, which resolves a promise with the
// user's answer — so call sites read almost the same as `await confirm(...)`.
export default function ConfirmDialog({
  title,
  body,
  confirmLabel = "Confirm",
  danger = false,
  alertOnly = false,
  onResolve,
}) {
  const ref = useDialog(() => onResolve(false));

  return (
    <div className="modal-backdrop" onClick={() => onResolve(false)}>
      <div
        className="modal confirm-modal"
        ref={ref}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id="confirm-title">{title}</h3>
        {body && <p className="confirm-body muted">{body}</p>}
        <div className="confirm-actions">
          {!alertOnly && (
            <button className="mini ghost" onClick={() => onResolve(false)}>
              Cancel
            </button>
          )}
          <button
            className={`primary ${danger ? "danger-btn" : ""}`}
            autoFocus
            onClick={() => onResolve(true)}
          >
            {alertOnly ? "OK" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

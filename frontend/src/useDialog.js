import { useEffect, useRef } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Modal accessibility: Escape closes, Tab is trapped inside, and focus is
 * restored to whatever opened it.
 *
 * Without this a keyboard or screen-reader user tabs straight out of an open
 * dialog into the page behind it, with no way to dismiss it from the keyboard.
 *
 * Returns a ref to spread onto the dialog element.
 */
export function useDialog(onClose) {
  const ref = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const node = ref.current;
    const previouslyFocused = document.activeElement;

    // Focus the first control unless something inside already claimed it
    // (e.g. an autoFocus input).
    if (node && !node.contains(document.activeElement)) {
      const first = node.querySelector(FOCUSABLE);
      (first || node).focus?.();
    }

    function onKeyDown(e) {
      if (e.key === "Escape") {
        e.stopPropagation();
        closeRef.current?.();
        return;
      }
      if (e.key !== "Tab" || !node) return;
      const items = [...node.querySelectorAll(FOCUSABLE)].filter(
        (el) => el.offsetParent !== null
      );
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      // Wrap around rather than escaping to the page behind.
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      previouslyFocused?.focus?.();
    };
  }, []);

  return ref;
}

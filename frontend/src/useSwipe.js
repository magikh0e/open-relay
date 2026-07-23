import { useRef } from "react";

// Detect horizontal swipe gestures on touch devices. Returns handlers to spread
// onto an element. Fires onSwipeLeft/onSwipeRight only when a gesture is clearly
// horizontal (so it doesn't hijack vertical scrolling) and exceeds `threshold`.
export function useSwipe({ onSwipeLeft, onSwipeRight, threshold = 65 } = {}) {
  const start = useRef(null);

  return {
    onTouchStart: (e) => {
      if (e.touches.length !== 1) {
        start.current = null;
        return;
      }
      const t = e.touches[0];
      start.current = { x: t.clientX, y: t.clientY };
    },
    onTouchEnd: (e) => {
      const s = start.current;
      start.current = null;
      if (!s) return;
      const t = e.changedTouches[0];
      const dx = t.clientX - s.x;
      const dy = t.clientY - s.y;
      // Require a mostly-horizontal move past the threshold.
      if (Math.abs(dx) < threshold || Math.abs(dx) < Math.abs(dy) * 1.4) return;
      if (dx > 0) onSwipeRight?.();
      else onSwipeLeft?.();
    },
  };
}

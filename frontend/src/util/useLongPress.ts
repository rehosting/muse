import { useRef } from "react";

// Press-and-hold gesture for touch: fires `onLongPress` after `ms` if the finger
// stays down and roughly still. Skips mouse entirely (desktop uses an explicit
// affordance instead), and swallows the click that a touch would otherwise
// synthesize after the hold — so a long-press doesn't ALSO trigger the element's
// normal tap action (open row / select tab). Spread the returned handlers onto the
// target element.
export function useLongPress(
  onLongPress: () => void,
  { ms = 500, moveTol = 10 }: { ms?: number; moveTol?: number } = {},
) {
  const timer = useRef<number | undefined>(undefined);
  const fired = useRef(false);
  const start = useRef<{ x: number; y: number } | null>(null);

  const clear = () => {
    window.clearTimeout(timer.current);
    timer.current = undefined;
    start.current = null;
  };

  return {
    onPointerDown(e: React.PointerEvent) {
      if (e.pointerType === "mouse") return; // desktop path is the ✎ affordance
      fired.current = false;
      start.current = { x: e.clientX, y: e.clientY };
      timer.current = window.setTimeout(() => {
        fired.current = true;
        navigator.vibrate?.(15);
        onLongPress();
      }, ms);
    },
    onPointerMove(e: React.PointerEvent) {
      if (!start.current) return;
      if (Math.hypot(e.clientX - start.current.x, e.clientY - start.current.y) > moveTol) clear();
    },
    onPointerUp: clear,
    onPointerLeave: clear,
    onClick(e: React.MouseEvent) {
      // The tap that follows a fired long-press must not bubble to open/select.
      if (fired.current) {
        e.preventDefault();
        e.stopPropagation();
        fired.current = false;
      }
    },
  };
}

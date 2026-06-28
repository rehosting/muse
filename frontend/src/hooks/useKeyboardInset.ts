import { useEffect } from "react";

/**
 * Keep bottom-anchored inputs above the on-screen keyboard.
 *
 * `interactive-widget=resizes-content` (viewport meta) already shrinks the layout
 * viewport on Android Chrome, so this is mostly a no-op there. But some browsers
 * (notably iOS Safari, and standalone PWAs) *overlay* the keyboard without
 * resizing — there, the VisualViewport API is the only signal. We compute the
 * overlap and publish it as the `--kb-inset` CSS variable; layouts add it to their
 * bottom padding so the composer rides up with the keyboard.
 *
 * Call once near the app root. Safe when VisualViewport is unsupported (inset 0).
 */
export function useKeyboardInset(): void {
  useEffect(() => {
    const vv = window.visualViewport;
    const root = document.documentElement;
    if (!vv) {
      root.style.setProperty("--kb-inset", "0px");
      return;
    }
    let raf = 0;
    const apply = () => {
      raf = 0;
      // How much of the layout viewport is hidden below the visual viewport —
      // i.e. the keyboard's height when it overlays the content.
      const overlap = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
      root.style.setProperty("--kb-inset", `${Math.round(overlap)}px`);
    };
    // Coalesce the burst of resize/scroll events the keyboard animation fires into
    // one write per frame, so the layout tracks the slide fluidly without thrash.
    const update = () => {
      if (!raf) raf = requestAnimationFrame(apply);
    };
    apply();
    vv.addEventListener("resize", update);
    vv.addEventListener("scroll", update);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      vv.removeEventListener("resize", update);
      vv.removeEventListener("scroll", update);
      root.style.setProperty("--kb-inset", "0px");
    };
  }, []);
}

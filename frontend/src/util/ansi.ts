import type { CSSProperties } from "react";

/**
 * Minimal ANSI SGR parser → styled text segments, for rendering `tmux capture-pane
 * -e` output with real terminal colors. Handles the common cases: 16 colors
 * (standard + bright), 256-color and truecolor (38/48;5/2;…), bold, dim, italic,
 * underline, inverse, and reset. Non-SGR escape sequences are stripped.
 */

export interface AnsiSegment {
  text: string;
  style: CSSProperties;
}

// Standard 16-color xterm palette (tuned slightly for a dark background).
const PALETTE_16 = [
  "#3b3f46", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#abb2bf",
  "#5c6370", "#ef596f", "#89ca78", "#e5c07b", "#61afef", "#d55fde", "#56b6c2", "#ffffff",
];

function color256(n: number): string {
  if (n < 16) return PALETTE_16[n];
  if (n >= 232) {
    const v = 8 + (n - 232) * 10; // grayscale ramp
    return `rgb(${v},${v},${v})`;
  }
  const i = n - 16;
  const r = Math.floor(i / 36);
  const g = Math.floor((i % 36) / 6);
  const b = i % 6;
  const c = (x: number) => (x === 0 ? 0 : x * 40 + 55);
  return `rgb(${c(r)},${c(g)},${c(b)})`;
}

interface State {
  fg?: string;
  bg?: string;
  bold?: boolean;
  dim?: boolean;
  italic?: boolean;
  underline?: boolean;
  inverse?: boolean;
}

function applyCodes(s: State, params: number[]): void {
  for (let i = 0; i < params.length; i++) {
    const c = params[i];
    if (c === 0) { Object.keys(s).forEach((k) => delete (s as Record<string, unknown>)[k]); }
    else if (c === 1) s.bold = true;
    else if (c === 2) s.dim = true;
    else if (c === 3) s.italic = true;
    else if (c === 4) s.underline = true;
    else if (c === 7) s.inverse = true;
    else if (c === 22) { s.bold = false; s.dim = false; }
    else if (c === 23) s.italic = false;
    else if (c === 24) s.underline = false;
    else if (c === 27) s.inverse = false;
    else if (c >= 30 && c <= 37) s.fg = PALETTE_16[c - 30];
    else if (c >= 90 && c <= 97) s.fg = PALETTE_16[c - 90 + 8];
    else if (c >= 40 && c <= 47) s.bg = PALETTE_16[c - 40];
    else if (c >= 100 && c <= 107) s.bg = PALETTE_16[c - 100 + 8];
    else if (c === 39) delete s.fg;
    else if (c === 49) delete s.bg;
    else if (c === 38 || c === 48) {
      const target = c === 38 ? "fg" : "bg";
      const mode = params[i + 1];
      if (mode === 5) { s[target] = color256(params[i + 2]); i += 2; }
      else if (mode === 2) { s[target] = `rgb(${params[i + 2]},${params[i + 3]},${params[i + 4]})`; i += 4; }
    }
  }
}

function toCss(s: State): CSSProperties {
  const css: CSSProperties = {};
  const fg = s.inverse ? s.bg : s.fg;
  const bg = s.inverse ? s.fg : s.bg;
  if (fg) css.color = fg;
  if (bg) css.backgroundColor = bg;
  if (s.bold) css.fontWeight = 700;
  if (s.italic) css.fontStyle = "italic";
  if (s.underline) css.textDecoration = "underline";
  if (s.dim) css.opacity = 0.6;
  return css;
}

// SGR sequences (…m) we interpret; any OTHER escape sequence is discarded.
const SGR = /\x1b\[([0-9;]*)m/g;
// eslint-disable-next-line no-control-regex
const OTHER_ESC = /\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]|\x1b\[[0-9;?]*[A-Za-ln-z]/g;

export function parseAnsi(input: string): AnsiSegment[] {
  const clean = input.replace(OTHER_ESC, "");
  const segs: AnsiSegment[] = [];
  const state: State = {};
  let last = 0;
  let m: RegExpExecArray | null;
  SGR.lastIndex = 0;
  while ((m = SGR.exec(clean)) !== null) {
    if (m.index > last) segs.push({ text: clean.slice(last, m.index), style: toCss(state) });
    const params = m[1] === "" ? [0] : m[1].split(";").map((p) => parseInt(p, 10) || 0);
    applyCodes(state, params);
    last = SGR.lastIndex;
  }
  if (last < clean.length) segs.push({ text: clean.slice(last), style: toCss(state) });
  return segs;
}

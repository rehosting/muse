/** Best-effort redaction for shareable exports. Applied to RAW strings before
 * HTML assembly (text-level, not DOM-level). Pattern matching can both over-
 * and under-match — exports carry a footer saying so, and the caller surfaces
 * the replacement count so the user notices. */

export interface RedactOptions {
  paths: boolean; // /home/<user>, /Users/<user> → ~
  emails: boolean;
  secrets: boolean; // API keys / tokens / private key blocks
}

export interface Redactor {
  redact: (s: string) => string;
  count: () => number;
}

const SECRET_PATTERNS: RegExp[] = [
  /AKIA[0-9A-Z]{16}/g, // AWS access key id
  /ghp_[A-Za-z0-9]{36}/g, // GitHub PAT
  /github_pat_[A-Za-z0-9_]{22,}/g,
  /sk-ant-[A-Za-z0-9_-]{10,}/g, // Anthropic
  /sk-[A-Za-z0-9]{32,}/g, // OpenAI-style
  /xox[bpars]-[A-Za-z0-9-]{10,}/g, // Slack
  /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g,
  /\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*/g,
  /\b(api[_-]?key|token|secret|password)\s*[:=]\s*["']?[A-Za-z0-9._~+/-]{8,}["']?/gi,
];

const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
const HOME_RE = /(\/home\/|\/Users\/)[A-Za-z0-9._-]+/g;

export function makeRedactor(opts: RedactOptions): Redactor {
  let n = 0;
  const sub = (s: string, re: RegExp, repl: string) =>
    s.replace(re, () => {
      n += 1;
      return repl;
    });
  return {
    redact(s: string): string {
      let out = s;
      if (opts.secrets) {
        for (const re of SECRET_PATTERNS) out = sub(out, re, "[REDACTED]");
      }
      if (opts.emails) out = sub(out, EMAIL_RE, "[email]");
      if (opts.paths) out = sub(out, HOME_RE, "~");
      return out;
    },
    count: () => n,
  };
}

/** A redactor that changes nothing (keeps the export code path uniform). */
export const NOOP_REDACTOR: Redactor = { redact: (s) => s, count: () => 0 };

import { abbrevHome, contextLabel, modelDisplay } from "../util/format";

const PROVIDER_NAME: Record<string, string> = {
  claude: "Claude Code",
  codex: "Codex",
  gemini: "Gemini",
  opencode: "opencode",
};

/** Recreates the CLI startup banner: provider name + version/model/cwd
 * (the crab logo is shown only for Claude Code). */
export default function WelcomeBanner({
  cwd,
  model,
  version,
  contextWindow,
  provider = "claude",
}: {
  cwd: string | null;
  model: string | null;
  version?: string | null;
  contextWindow?: number;
  provider?: string;
}) {
  const modelLine = [
    modelDisplay(model),
    contextWindow ? `(${contextLabel(contextWindow)})` : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="cc-banner">
      <span className="cc-banner-star">✻</span>
      <div className="cc-banner-lines">
        <div className="cc-banner-l1">
          Welcome to {PROVIDER_NAME[provider] ?? provider}
          {version ? ` v${version}` : ""}!
        </div>
        {modelLine && <div className="cc-banner-l2">{modelLine}</div>}
        {cwd && <div className="cc-banner-l3">cwd: {abbrevHome(cwd)}</div>}
      </div>
    </div>
  );
}

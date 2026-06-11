import { useState } from "react";
import { copyToClipboard, resumeCommand } from "../util/shell";

/**
 * Click to copy the shell command that resumes this session:
 *   cd <cwd> && claude --resume <sessionId>
 */
export default function ResumeButton({
  cwd,
  sessionId,
  className = "",
}: {
  cwd: string | null;
  sessionId: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  const cmd = resumeCommand(cwd, sessionId);

  const onClick = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const ok = await copyToClipboard(cmd);
    setCopied(ok);
    setTimeout(() => setCopied(false), 1600);
  };

  return (
    <button
      className={`resume-btn ${className}`.trim()}
      title={`Copy resume command:\n${cmd}`}
      onClick={onClick}
    >
      {copied ? "✓ copied" : "⧉ resume"}
    </button>
  );
}

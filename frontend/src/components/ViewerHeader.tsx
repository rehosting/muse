import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { SessionLineage, Thread } from "../api/types";
import { formatTokens, shortModel } from "../util/format";
import { sessionStats } from "../util/stats";
import { api } from "../api/client";
import AiActionButton from "./AiActionButton";
import ExportMenu from "./ExportMdButton";
import Breadcrumb, { type Crumb } from "./Breadcrumb";
import ContextMeter from "./ContextMeter";
import ResumeButton from "./ResumeButton";
import SubagentTree, { type SubNode } from "./SubagentTree";

export type LayoutMode = 1 | 2 | 3;

interface Props {
  current: Thread;
  crumbs: Crumb[];
  onNavigate: (index: number) => void;
  layout: LayoutMode;
  onLayoutChange: (mode: LayoutMode) => void;
  live: boolean;
  subagents: SubNode[];
  subagentCount: number;
  activePath: string[];
  onOpenSubagentPath: (path: string[]) => void;
  onRename: (title: string) => void;
  lineage?: SessionLineage | null;
  onJumpToCompaction?: (uuid: string) => void;
}

function fmtTokens(n: number): string {
  return n >= 1000 ? `${Math.round(n / 1000)}k` : String(n);
}

const LAYOUT_TITLES: Record<LayoutMode, string> = {
  1: "Conversation only",
  2: "Conversation + stacked tool log / detail",
  3: "Conversation + tool log + detail",
};

export default function ViewerHeader({
  current,
  crumbs,
  onNavigate,
  layout,
  onLayoutChange,
  live,
  subagents,
  subagentCount,
  activePath,
  onOpenSubagentPath,
  onRename,
  lineage,
  onJumpToCompaction,
}: Props) {
  const stats = useMemo(() => sessionStats(current), [current]);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [treeOpen, setTreeOpen] = useState(false);
  const inSubagent = activePath.length > 0;
  return (
    <header className="viewer-header">
      <div className="viewer-header-top">
        <div className="viewer-title-block">
          <div className="viewer-titlebar">
            {editing ? (
              <input
                className="rename-input"
                autoFocus
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onBlur={() => {
                  onRename(draft);
                  setEditing(false);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    onRename(draft);
                    setEditing(false);
                  }
                  if (e.key === "Escape") setEditing(false);
                }}
              />
            ) : (
              <Breadcrumb
                crumbs={crumbs}
                onNavigate={onNavigate}
                live={live}
                onTitleClick={() => {
                  setDraft(current.title);
                  setEditing(true);
                }}
              />
            )}
          </div>
          <div className="viewer-meta">
            {current.project_cwd && <span className="chip">{current.project_cwd}</span>}
            {current.agent_type && (
              <span className="subagent-pill">{current.agent_type}</span>
            )}
            <span>{current.items.length} items</span>

            {stats.model && (
              <span className="stat model-chip" title="Model">
                {shortModel(stats.model)}
              </span>
            )}
            <span
              className="stat"
              title={`Tokens (priced separately)\ninput ${stats.inputTokens.toLocaleString()}\noutput ${stats.outputTokens.toLocaleString()}\ncache ${stats.cacheTokens.toLocaleString()}`}
            >
              <span className="stat-val accent-in">↑{formatTokens(stats.inputTokens)}</span> in
              {" · "}
              <span className="stat-val accent-out">↓{formatTokens(stats.outputTokens)}</span> out
              {" · "}
              <span className="stat-val">{formatTokens(stats.cacheTokens)}</span> cache
            </span>
            <ContextMeter
              used={stats.contextUsed}
              window={stats.contextWindow}
              pct={stats.contextPct}
            />

            {!inSubagent && lineage && lineage.boundaries.length > 0 && (
              <button
                className="stat compaction-chip"
                title={
                  `Context compacted ${lineage.boundaries.length}× ` +
                  `(${fmtTokens(lineage.total_pre_tokens)} tokens reclaimed)\n` +
                  lineage.boundaries
                    .map(
                      (b, i) =>
                        `${i + 1}. ${b.trigger ?? "?"} · ${
                          b.pre_tokens ? fmtTokens(b.pre_tokens) + " tokens" : ""
                        }${b.timestamp ? " · " + new Date(b.timestamp).toLocaleString() : ""}`,
                    )
                    .join("\n") +
                  "\n(click to jump to the first boundary)"
                }
                onClick={() => {
                  const u = lineage.boundaries[0]?.uuid;
                  if (u) onJumpToCompaction?.(u);
                }}
              >
                ⊟ {lineage.boundaries.length} compaction
                {lineage.boundaries.length > 1 ? "s" : ""}
              </button>
            )}

            <Link to="/" className="all-sessions-link">
              ← all sessions
            </Link>
          </div>
        </div>

        <div className="viewer-actions">
          {inSubagent && (
            <button
              className="action-btn back-btn"
              title="Back to the main session"
              onClick={() => onNavigate(0)}
            >
              ↑ Main session
            </button>
          )}
          {subagentCount > 0 && (
            <div className="subagent-menu">
              <button
                className={`action-btn${treeOpen ? " active" : ""}`}
                title="Subagents in this session"
                onClick={() => setTreeOpen((o) => !o)}
              >
                🌳 Subagents ({subagentCount})
              </button>
              {treeOpen && (
                <>
                  <div className="menu-overlay" onClick={() => setTreeOpen(false)} />
                  <div className="subagent-pop">
                    <SubagentTree
                      nodes={subagents}
                      activePath={activePath}
                      onOpen={(path) => {
                        onOpenSubagentPath(path);
                        setTreeOpen(false);
                      }}
                    />
                  </div>
                </>
              )}
            </div>
          )}
          {current.provider !== "codex" && current.provider !== "opencode" && (
            <ResumeButton cwd={current.project_cwd} sessionId={current.session_id} />
          )}
          {current.provider === "claude" && !inSubagent && (
            <AiActionButton
              label="✦ AI summary"
              title="Generate a re-entry summary of this session (headless claude; lands as a note)"
              enqueue={() => api.summarizeSession(current.session_id)}
              onDone={() => window.dispatchEvent(new Event("muse:notes-refresh"))}
            />
          )}
          {current.provider === "claude" && (
            <button
              className="action-btn"
              title="Start a fresh session seeded with this one's context (brief / notes / files)"
              onClick={() =>
                window.dispatchEvent(
                  new CustomEvent("muse:launch", {
                    detail: {
                      sourceSessionId: current.session_id,
                      cwd: current.project_cwd ?? undefined,
                    },
                  }),
                )
              }
            >
              ✻ Continue in new session
            </button>
          )}
          <ExportMenu
            sessionId={current.session_id}
            title={current.title}
            label={subagentCount > 0 ? `↓ Export (+${subagentCount} subagents)` : "↓ Export"}
          />
        </div>

        <div className="layout-switch" role="group" aria-label="Layout mode">
          {([1, 2, 3] as LayoutMode[]).map((m) => (
            <button
              key={m}
              className={`layout-btn${layout === m ? " active" : ""}`}
              title={LAYOUT_TITLES[m]}
              onClick={() => onLayoutChange(m)}
            >
              {m}
            </button>
          ))}
        </div>
      </div>
    </header>
  );
}

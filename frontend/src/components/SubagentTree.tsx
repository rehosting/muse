export interface SubNode {
  agentId: string;
  agentType: string;
  description: string;
  path: string[];
  children: SubNode[];
}

/** Recursive subagent tree. Clicking a node navigates to that subagent. */
export default function SubagentTree({
  nodes,
  activePath,
  onOpen,
}: {
  nodes: SubNode[];
  activePath: string[];
  onOpen: (path: string[]) => void;
}) {
  if (nodes.length === 0) return <div className="subtree-empty">No subagents</div>;
  return (
    <ul className="subtree">
      {nodes.map((n) => {
        const active = activePath.join(",") === n.path.join(",");
        return (
          <li key={n.path.join("/")}>
            <button
              className={`subtree-node${active ? " active" : ""}`}
              onClick={() => onOpen(n.path)}
              title={n.description}
            >
              <span className="subagent-pill">{n.agentType}</span>
              <span className="subtree-desc">{n.description || n.agentId}</span>
            </button>
            {n.children.length > 0 && (
              <SubagentTree nodes={n.children} activePath={activePath} onOpen={onOpen} />
            )}
          </li>
        );
      })}
    </ul>
  );
}

import LiveBadge from "./LiveBadge";

export interface Crumb {
  label: string;
  sub?: string;
}

export default function Breadcrumb({
  crumbs,
  onNavigate,
  live,
  onTitleClick,
}: {
  crumbs: Crumb[];
  onNavigate: (index: number) => void;
  live?: boolean;
  /** When at the root level, clicking the session title triggers this (rename). */
  onTitleClick?: () => void;
}) {
  return (
    <div className="breadcrumb">
      {crumbs.map((c, i) => {
        const isLast = i === crumbs.length - 1;
        // At the root (single crumb), the title itself is the rename trigger.
        if (isLast && onTitleClick && crumbs.length === 1) {
          return (
            <button
              key={i}
              className="viewer-title-btn"
              title="Click to rename"
              onClick={onTitleClick}
            >
              {c.label}
            </button>
          );
        }
        return (
          <span key={i}>
            {i > 0 && <span className="sep"> / </span>}
            {isLast ? (
              <strong>
                {c.label}
                {c.sub && <span style={{ color: "var(--text-dim)" }}> · {c.sub}</span>}
              </strong>
            ) : (
              <a
                href="#"
                onClick={(e) => {
                  e.preventDefault();
                  onNavigate(i);
                }}
              >
                {c.label}
              </a>
            )}
          </span>
        );
      })}
      {live && <LiveBadge />}
    </div>
  );
}

/** Badges for live failure patterns ("stuck in retry loop (Bash ×4)" …). */
export default function HealthBadges({
  health,
  flags,
}: {
  health: "ok" | "warn" | "bad" | null;
  flags: string[];
}) {
  if (!health || health === "ok" || flags.length === 0) return null;
  return (
    <span className="health-badges">
      {flags.map((f) => (
        <span key={f} className={`health-badge health-${health}`} title={f}>
          {health === "bad" ? "⛔" : "⚠"} {f}
        </span>
      ))}
    </span>
  );
}

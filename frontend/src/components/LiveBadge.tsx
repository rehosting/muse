/** Red, blinking LIVE indicator — used on the session list and in the viewer. */
export default function LiveBadge({ className = "" }: { className?: string }) {
  return <span className={`live-badge ${className}`.trim()}>● LIVE</span>;
}

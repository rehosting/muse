import { openFile } from "../util/openFile";

interface Props {
  path?: string;
  /** Text to show; defaults to the full path. */
  label?: string;
  className?: string;
}

/** A file path rendered as a click target that opens it in the global FileViewer.
 * Falls back to plain text when there's no real path (e.g. an unknown file_path). */
export default function FilePathLink({ path, label, className }: Props) {
  const text = label ?? path ?? "(unknown)";
  if (!path || path === "(unknown)") {
    return <pre className={`code nowrap ${className ?? ""}`}>{text}</pre>;
  }
  return (
    <button
      type="button"
      className={`file-link ${className ?? ""}`}
      title={`View ${path}`}
      onClick={() => openFile(path)}
    >
      {text}
    </button>
  );
}

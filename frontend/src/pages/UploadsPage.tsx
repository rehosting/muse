import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { UploadList, UploadedFile } from "../api/types";
import { copyToClipboard } from "../util/shell";
import { openFile } from "../util/openFile";
import { relativeTime } from "../util/format";

const fmtSize = (n: number) => {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)} MB`;
  if (n >= 1e3) return `${Math.round(n / 1e3)} KB`;
  return `${n} B`;
};

/**
 * Drop files from any device on the tailnet onto this machine's /tmp.
 *
 * The deliverable is the PATH, not the file: you upload a screenshot from the phone
 * and paste /tmp/muse-uploads-<uid>/shot.png into a session. So the path row is the
 * primary affordance on every entry — one tap copies it — and the bytes are never
 * served back out over HTTP (the file viewer reads them locally, through the same
 * guarded /api/file every other path in muse goes through).
 *
 * Uploads are temporary by construction: /tmp clears on reboot and anything past the
 * TTL is swept on the next listing. Both numbers are shown rather than discovered
 * from a rejection.
 */
export default function UploadsPage() {
  const [data, setData] = useState<UploadList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.listUploads());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not list uploads");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const send = useCallback(
    async (files: File[]) => {
      if (!files.length) return;
      setBusy(true);
      setError(null);
      try {
        const res = await api.uploadFiles(files);
        // Show the whole dir, not just this batch, so the page is the same view
        // whether you just uploaded or arrived fresh.
        await load();
        if (res.errors.length) setError(res.errors.join(" · "));
        const first = res.files[0];
        if (first) {
          const ok = await copyToClipboard(first.path);
          if (ok) setCopied(first.path);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "upload failed");
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  // Desktop convenience: a pasted screenshot is the common case this page exists for.
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const files = [...(e.clipboardData?.files ?? [])];
      if (files.length) {
        e.preventDefault();
        send(files);
      }
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [send]);

  const copy = async (path: string) => {
    const ok = await copyToClipboard(path);
    setCopied(ok ? path : null);
    if (!ok) setError("could not reach the clipboard — long-press the path to select it");
  };

  const remove = async (f: UploadedFile) => {
    try {
      await api.deleteUpload(f.name);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not delete");
    }
  };

  const files = data?.files ?? [];

  return (
    <div className="list-wrap uploads-page">
      <div className="stats-head">
        <h2 className="list-heading">Upload</h2>
        {data && (
          <span className="uploads-limits">
            up to {data.max_mb} MB each · swept after {data.ttl_hours}h
          </span>
        )}
      </div>

      <div
        className={`upload-drop${dragging ? " dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          send([...e.dataTransfer.files]);
        }}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          className="upload-input"
          aria-label="Choose files to upload"
          onChange={(e) => {
            send([...(e.target.files ?? [])]);
            e.target.value = ""; // so re-picking the same file fires again
          }}
        />
        <button
          className="upload-pick"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
        >
          {busy ? "Uploading…" : "Choose files"}
        </button>
        <p className="upload-hint">
          …or drop files here, or paste a screenshot. They land on this machine and you
          get the path.
        </p>
      </div>

      {error && <div className="upload-error">{error}</div>}

      {data && (
        <p className="uploads-root">
          <code>{data.root}</code>
        </p>
      )}

      {files.length === 0 ? (
        <p className="empty-note">Nothing uploaded yet.</p>
      ) : (
        <ul className="upload-list">
          {files.map((f) => (
            <li key={f.path} className="upload-row">
              <div className="upload-meta">
                <span className="upload-name">{f.name}</span>
                <span className="upload-sub">
                  {fmtSize(f.size)} · {relativeTime(f.mtime)}
                </span>
              </div>
              <button
                className="upload-path"
                title="Copy this path"
                onClick={() => copy(f.path)}
              >
                <code>{f.path}</code>
                <span className="upload-copy">{copied === f.path ? "copied" : "copy"}</span>
              </button>
              <div className="upload-actions">
                <button className="fc-view" title={`View ${f.name}`} onClick={() => openFile(f.path)}>
                  view
                </button>
                <button className="upload-del" title={`Delete ${f.name}`} onClick={() => remove(f)}>
                  ✕
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Global "open the file viewer" signal. Any call site (tool renderers, FileChanges,
// FilesPage, panes/drive) calls openFile(path); the single <FileViewer> mounted in
// App.tsx listens for the event and opens the modal. Mirrors the "muse:search" pattern.
export const VIEW_FILE_EVENT = "muse:view-file";

export function openFile(path: string): void {
  window.dispatchEvent(new CustomEvent(VIEW_FILE_EVENT, { detail: { path } }));
}

"""FUTURE: job queue + tmux injection layer.

This package is an intentional stub. The architecture is already in place for it:

- ``services/events.py`` (EventBroker) is the pub/sub bus. A worker here will
  publish job lifecycle events (``queued``/``running``/``done``) onto a
  ``job:{id}`` topic; the existing SSE machinery streams them with no changes.
- ``services/session_service.py`` is the seam routers call. Job methods
  (``enqueue_job``, ``list_jobs``) will live there, keeping routers thin.
- ``main.py``'s lifespan owns long-lived state, so a background worker task and
  a ``tmux_adapter`` (libtmux: ``send-keys`` into a session's pane) get started
  and stopped alongside the broker and tailer registry.

Planned modules:
    worker.py        — async job queue + dispatcher
    tmux_adapter.py  — libtmux wrapper to inject prompts into a Claude Code pane
    models.py        — Job / JobStatus pydantic models
"""

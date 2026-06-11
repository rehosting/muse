"""Tests for session lineage (compaction boundaries) + compaction events."""

import json
from pathlib import Path

from muse import eventlog, lineage
from muse.paths import SessionPaths


def _write(tmp_path: Path, lines: list[dict]) -> Path:
    f = tmp_path / "s.jsonl"
    f.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    return f


def _boundary(uuid, pre, trigger="manual", ms=113668):
    return {
        "type": "system",
        "subtype": "compact_boundary",
        "uuid": uuid,
        "timestamp": "2026-06-07T18:15:04Z",
        "content": "Conversation compacted",
        "compactMetadata": {"trigger": trigger, "preTokens": pre, "durationMs": ms},
    }


def test_lineage_segments_by_compaction(tmp_path):
    lines = [
        {"type": "user", "uuid": "u1", "message": {"content": "hi"}},
        _boundary("b1", 278717, "manual"),
        {"type": "user", "uuid": "u2", "message": {"content": "more"}},
        _boundary("b2", 190000, "auto"),
    ]
    lin = lineage.build_lineage(_write(tmp_path, lines), "s")
    assert lin.segment_count == 3  # two boundaries -> three segments
    assert lin.total_pre_tokens == 278717 + 190000
    assert [b.trigger for b in lin.boundaries] == ["manual", "auto"]
    assert lin.boundaries[0].pre_tokens == 278717
    assert lin.boundaries[0].duration_ms == 113668


def test_no_compaction_is_single_segment(tmp_path):
    lin = lineage.build_lineage(_write(tmp_path, [{"type": "user", "uuid": "u1"}]), "s")
    assert lin.segment_count == 1 and lin.boundaries == []


def test_compaction_event_flagged_in_timeline(tmp_path):
    f = _write(tmp_path, [_boundary("b1", 278717, "manual")])
    paths = SessionPaths(project_dir="-tmp", session_id="s")
    events = eventlog.build_events(f, paths)
    comp = next(e for e in events if e.is_compaction)
    assert comp.type == "compact_boundary"
    assert "compacted" in comp.label.lower() and "279k" in comp.label
    assert comp.duration_ms == 113668

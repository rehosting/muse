"""The tail-cap that protects parse paths from pathological multi-GB transcripts.

A handful of gemini "merged" dumps reached 1-3.5GB; fully parsing one blocked the
request thread for ~34s and bloated RSS. Oversized files must be read from the
TAIL only, while normal files are read in full and offsets still advance for the
incremental (append-only) scanners.
"""

import orjson

from muse import incremental, transcript


def _write_jsonl(path, n):
    with path.open("wb") as fh:
        for i in range(n):
            fh.write(orjson.dumps({"i": i, "pad": "x" * 200}) + b"\n")
    return path


def test_iter_json_lines_full_read_under_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(transcript, "MAX_PARSE_BYTES", 10 * 1024 * 1024)
    p = _write_jsonl(tmp_path / "small.jsonl", 100)
    objs = list(transcript.iter_json_lines(p))
    assert [o["i"] for o in objs] == list(range(100))  # nothing dropped


def test_iter_json_lines_tails_oversized(tmp_path, monkeypatch):
    p = _write_jsonl(tmp_path / "big.jsonl", 5000)
    size = p.stat().st_size
    monkeypatch.setattr(transcript, "MAX_PARSE_BYTES", size // 4)
    objs = list(transcript.iter_json_lines(p))
    # Only the tail is read, and every returned object is intact (the partial
    # first line we land inside is dropped, never half-parsed).
    assert 0 < len(objs) < 5000
    assert objs[-1]["i"] == 4999  # tail includes the most recent activity
    assert all("pad" in o for o in objs)


def test_iter_json_lines_disabled_cap_reads_all(tmp_path, monkeypatch):
    monkeypatch.setattr(transcript, "MAX_PARSE_BYTES", 0)
    p = _write_jsonl(tmp_path / "big.jsonl", 2000)
    assert len(list(transcript.iter_json_lines(p))) == 2000


def test_new_objects_caps_cold_read_and_advances_to_eof(tmp_path):
    p = _write_jsonl(tmp_path / "big.jsonl", 5000)
    size = p.stat().st_size
    objs, new_offset = incremental.new_objects(p, 0, max_bytes=size // 4)
    assert 0 < len(objs) < 5000
    assert objs[-1]["i"] == 4999
    # Offset advances to EOF so the next append-only call resumes correctly.
    assert new_offset == size


def test_new_objects_uncapped_when_under_limit(tmp_path):
    p = _write_jsonl(tmp_path / "small.jsonl", 50)
    objs, new_offset = incremental.new_objects(p, 0, max_bytes=10 * 1024 * 1024)
    assert [o["i"] for o in objs] == list(range(50))
    assert new_offset == p.stat().st_size

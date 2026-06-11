"""Session artifacts: bounded, read-only collection of working-dir notes + memory."""

from muse import artifacts


def test_collect_notes_top_level_and_nested(tmp_path):
    (tmp_path / "NOTES.md").write_text("# notes\nthe latest understanding")
    (tmp_path / "readme.txt").write_text("hi")
    sub = tmp_path / "proj"
    sub.mkdir()
    (sub / "NOTES.md").write_text("nested notes")
    (tmp_path / "ignore.py").write_text("print(1)")  # not markdown/text

    entries = artifacts.collect_notes(str(tmp_path))
    names = {e["name"] for e in entries}
    assert "NOTES.md" in names and "readme.txt" in names
    assert "ignore.py" not in names
    assert any(e["path"].endswith("proj/NOTES.md") for e in entries)  # depth-1 NOTES
    assert all("preview" in e and "mtime" in e and "size" in e for e in entries)


def test_collect_notes_missing(tmp_path):
    assert artifacts.collect_notes(None) == []
    assert artifacts.collect_notes(str(tmp_path / "does-not-exist")) == []


def test_collect_memory(tmp_path):
    md = tmp_path / "memory"
    md.mkdir()
    (md / "a.md").write_text("memory a")
    (md / "b.md").write_text("memory b")
    names = {e["name"] for e in artifacts.collect_memory(md)}
    assert names == {"a.md", "b.md"}
    assert artifacts.collect_memory(None) == []
    assert artifacts.collect_memory(tmp_path / "nope") == []


def test_preview_is_bounded(tmp_path):
    (tmp_path / "big.md").write_text("x" * 5000)
    e = artifacts.collect_notes(str(tmp_path))[0]
    assert len(e["preview"]) == artifacts._PREVIEW_CHARS and e["preview_truncated"] is True


def test_collect_results_latest_run_manifest(tmp_path):
    # rehostings/target/results/{0,1,...,12}/  — latest run picked numerically.
    rd = tmp_path / "rehostings" / "t" / "results"
    for n in (0, 1, 2, 10, 12):
        run = rd / str(n)
        run.mkdir(parents=True)
        (run / "console.log").write_text(f"run {n} log " * 100)
        (run / "health_final.yaml").write_text("ok: true")
    res = artifacts.collect_results(str(tmp_path))
    assert len(res) == 1
    r = res[0]
    assert r["run_count"] == 5
    assert r["latest_run"].endswith("/12")  # 12 > 10 numerically (not lexically)
    names = {f["name"] for f in r["files"]}
    assert names == {"console.log", "health_final.yaml"}
    assert all("size" in f and "path" in f and "preview" not in f for f in r["files"])  # manifest only


def test_read_artifact_paginates(tmp_path):
    f = tmp_path / "results" / "5" / "console.log"
    f.parent.mkdir(parents=True)
    f.write_text("A" * 100)
    r1 = artifacts.read_artifact([tmp_path], str(f), 0, 40)
    assert r1["content"] == "A" * 40 and r1["size"] == 100 and r1["next_offset"] == 40
    r2 = artifacts.read_artifact([tmp_path], str(f), 40, 1000)
    assert len(r2["content"]) == 60 and r2["next_offset"] is None


def test_read_artifact_rejects_escape(tmp_path):
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("top secret")
    root = tmp_path / "proj"
    root.mkdir()
    # Absolute path outside the root, and a ../ traversal, both rejected.
    assert "error" in artifacts.read_artifact([root], str(secret), 0, 100)
    assert "error" in artifacts.read_artifact([root], "../secret.txt", 0, 100)

"""GET /api/file: live on-disk file viewer, guarded to the indexed project dirs +
~/.claude via artifacts.read_artifact. The router maps read_artifact's {error} to
403 (outside the allowlist) or 404 (missing / not a file); a clean read returns the
{path,size,content,next_offset} page. The sandbox itself is exercised here too — a
../ escape and an absolute path outside the roots must be refused."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from muse import artifacts
from muse.routers import sessions as sessions_router


@pytest.fixture
def client(tmp_path):
    # Two allowed roots: a project cwd and a fake ~/.claude (holding a plan).
    cwd = tmp_path / "proj"
    claude = tmp_path / "claude"
    (cwd / "src").mkdir(parents=True)
    (claude / "plans").mkdir(parents=True)
    (cwd / "src" / "main.py").write_text("print('hello')\n")
    (claude / "plans" / "plan.md").write_text("# Plan\n\n- step one\n- step two\n")
    # A secret OUTSIDE both roots — must never be readable.
    (tmp_path / "secret.txt").write_text("top secret\n")

    # A third root: the agent scratchpad base (/tmp/claude-<uid>/…) where sessions
    # Write HTML/notes; files there are referenced but live outside cwd and ~/.claude.
    scratch = tmp_path / "scratch"
    (scratch / "sess" / "scratchpad").mkdir(parents=True)
    (scratch / "sess" / "scratchpad" / "walkthrough.html").write_text("<h1>hi</h1>\n")

    class FakeService:
        def read_file(self, path, offset=0, limit=40000):
            return artifacts.read_artifact([cwd, claude, scratch], path, offset, limit)

    app = FastAPI()
    app.include_router(sessions_router.router)
    app.state.service = FakeService()
    c = TestClient(app)
    c.roots = {"cwd": cwd, "claude": claude, "scratch": scratch, "tmp": tmp_path}
    return c


def test_reads_a_plan_markdown(client):
    plan = client.roots["claude"] / "plans" / "plan.md"
    r = client.get("/api/file", params={"path": str(plan)})
    assert r.status_code == 200
    body = r.json()
    assert body["content"].startswith("# Plan")
    assert body["size"] == plan.stat().st_size
    assert body["next_offset"] is None


def test_reads_a_file_under_project_cwd(client):
    f = client.roots["cwd"] / "src" / "main.py"
    r = client.get("/api/file", params={"path": str(f)})
    assert r.status_code == 200 and "hello" in r.json()["content"]


def test_reads_a_scratchpad_file(client):
    f = client.roots["scratch"] / "sess" / "scratchpad" / "walkthrough.html"
    r = client.get("/api/file", params={"path": str(f)})
    assert r.status_code == 200 and "<h1>hi</h1>" in r.json()["content"]


def test_rejects_parent_traversal(client):
    # ../secret.txt from inside a root resolves outside it → 403.
    escape = client.roots["claude"] / "plans" / ".." / ".." / ".." / "secret.txt"
    r = client.get("/api/file", params={"path": str(escape)})
    assert r.status_code == 403
    assert "outside" in r.json()["detail"]


def test_rejects_absolute_path_outside_roots(client):
    r = client.get("/api/file", params={"path": str(client.roots["tmp"] / "secret.txt")})
    assert r.status_code == 403


def test_missing_file_is_404(client):
    r = client.get("/api/file", params={"path": str(client.roots["cwd"] / "nope.py")})
    assert r.status_code == 404


def test_directory_is_404(client):
    r = client.get("/api/file", params={"path": str(client.roots["cwd"] / "src")})
    assert r.status_code == 404


def test_pagination_next_offset(client):
    f = client.roots["cwd"] / "src" / "main.py"
    size = f.stat().st_size
    r = client.get("/api/file", params={"path": str(f), "offset": 0, "limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert len(body["content"]) == 5 and body["next_offset"] == 5
    # Second page finishes the file.
    r2 = client.get("/api/file", params={"path": str(f), "offset": 5, "limit": 40000})
    assert r2.json()["next_offset"] is None
    assert r2.json()["offset"] + len(r2.json()["content"]) == size

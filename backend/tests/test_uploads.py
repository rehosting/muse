"""Temporary upload drop: the storage rules (name sanitising, size cap, TTL sweep,
containment) and the endpoints over them.

This is the only place muse writes client-supplied bytes, so most of these tests are
about what must NOT happen: a name escaping the drop dir, an oversized stream filling
the disk, a planted symlink redirecting a delete, a collision silently overwriting a
file the user already pasted a path to.
"""

import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from muse import uploads
from muse.config import get_settings
from muse.routers import uploads as uploads_router


@pytest.fixture
def drop(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSE_UPLOAD_DIR", str(tmp_path / "drop"))
    monkeypatch.setenv("MUSE_UPLOAD_MAX_MB", "1")
    monkeypatch.setenv("MUSE_UPLOAD_TTL_HOURS", "48")
    get_settings.cache_clear()
    yield tmp_path / "drop"
    get_settings.cache_clear()


@pytest.fixture
def client(drop):
    app = FastAPI()
    app.include_router(uploads_router.router)
    return TestClient(app)


def put(name: str, data: bytes = b"hi"):
    import io

    return uploads.save(name, io.BytesIO(data))


# --- names ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("../../etc/passwd", "passwd"),       # traversal reduced to a basename
        ("/etc/shadow", "shadow"),
        ("C:\\Users\\me\\notes.txt", "notes.txt"),
        (".bashrc", "bashrc"),                # never lands as a dotfile
        ("..", "upload"),
        ("", "upload"),
        ("My Photo (1).JPG", "My_Photo_1_.JPG"),
    ],
)
def test_safe_name(raw, expected):
    assert uploads.safe_name(raw) == expected


def test_long_name_keeps_its_extension():
    out = uploads.safe_name("x" * 300 + ".png")
    assert len(out) <= uploads.MAX_NAME and out.endswith(".png")


def test_traversal_lands_inside_the_drop_dir(drop):
    e = put("../../../etc/passwd", b"not really passwd")
    assert os.path.dirname(e["path"]) == str(drop.resolve())
    assert (drop / "passwd").read_bytes() == b"not really passwd"


# --- writing ----------------------------------------------------------------------


def test_save_returns_the_absolute_path(drop):
    e = put("notes.txt", b"hello")
    assert e["path"] == str(drop / "notes.txt")
    assert e["size"] == 5
    assert (drop / "notes.txt").read_bytes() == b"hello"


def test_collision_suffixes_instead_of_overwriting(drop):
    first = put("IMG_0001.jpg", b"one")
    second = put("IMG_0001.jpg", b"two")
    assert first["name"] == "IMG_0001.jpg" and second["name"] == "IMG_0001-2.jpg"
    # the first file — whose path may already be pasted somewhere — is untouched
    assert (drop / "IMG_0001.jpg").read_bytes() == b"one"


def test_oversize_is_refused_and_leaves_no_partial_file(drop):
    import io

    with pytest.raises(uploads.UploadTooLarge):
        uploads.save("big.bin", io.BytesIO(b"x" * 4096), limit=1024)
    assert list(drop.iterdir()) == []


def test_cap_comes_from_settings_not_the_client(drop):
    import io

    # MUSE_UPLOAD_MAX_MB=1 in the fixture
    assert uploads.max_bytes() == 1024 * 1024
    with pytest.raises(uploads.UploadTooLarge):
        uploads.save("big.bin", io.BytesIO(b"x" * (2 * 1024 * 1024)))


def test_uploads_are_private_to_the_user(drop):
    e = put("secret.txt", b"shh")
    assert oct(os.stat(e["path"]).st_mode)[-3:] == "600"
    assert oct(drop.stat().st_mode)[-3:] == "700"


# --- listing & sweeping -----------------------------------------------------------


def test_list_is_newest_first(drop):
    put("a.txt")
    time.sleep(0.01)
    put("b.txt")
    assert [e["name"] for e in uploads.list_files()] == ["b.txt", "a.txt"]


def test_sweep_drops_only_what_is_past_the_ttl(drop):
    old = put("old.txt")["path"]
    put("new.txt")
    stale = time.time() - 49 * 3600
    os.utime(old, (stale, stale))
    assert uploads.sweep() == 1
    assert [e["name"] for e in uploads.list_files()] == ["new.txt"]


def test_ttl_zero_disables_the_sweep(drop, monkeypatch):
    old = put("old.txt")["path"]
    stale = time.time() - 1000 * 3600
    os.utime(old, (stale, stale))
    monkeypatch.setenv("MUSE_UPLOAD_TTL_HOURS", "0")
    get_settings.cache_clear()
    assert uploads.sweep() == 0
    assert len(uploads.list_files()) == 1


def test_listing_ignores_a_planted_symlink(drop, tmp_path):
    put("real.txt")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (drop / "link.txt").symlink_to(outside)
    assert [e["name"] for e in uploads.list_files()] == ["real.txt"]


# --- deleting ---------------------------------------------------------------------


def test_delete_removes_the_file(drop):
    put("gone.txt")
    assert uploads.delete("gone.txt") is True
    assert uploads.delete("gone.txt") is False


def test_delete_cannot_escape_the_drop_dir(drop, tmp_path):
    uploads.root()
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    assert uploads.delete("../victim.txt") is False
    assert victim.exists()


def test_delete_cannot_follow_a_symlink_out(drop, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    (uploads.root() / "link.txt").symlink_to(victim)
    assert uploads.delete("link.txt") is False
    assert victim.exists()


# --- endpoints --------------------------------------------------------------------


def test_post_returns_the_path_and_the_limits(client, drop):
    r = client.post("/api/uploads", files={"files": ("shot.png", b"\x89PNG", "image/png")})
    assert r.status_code == 200
    body = r.json()
    assert body["files"][0]["path"] == str(drop / "shot.png")
    assert body["root"] == str(drop) and body["max_mb"] == 1 and body["ttl_hours"] == 48
    assert body["errors"] == []


def test_post_accepts_a_multi_select(client):
    r = client.post(
        "/api/uploads",
        files=[("files", ("a.txt", b"a", "text/plain")), ("files", ("b.txt", b"b", "text/plain"))],
    )
    assert [f["name"] for f in r.json()["files"]] == ["a.txt", "b.txt"]


def test_partial_batch_keeps_what_landed(client):
    """One oversized file must not throw away the paths that already saved."""
    r = client.post(
        "/api/uploads",
        files=[
            ("files", ("ok.txt", b"small", "text/plain")),
            ("files", ("huge.bin", b"x" * (2 * 1024 * 1024), "application/octet-stream")),
        ],
    )
    assert r.status_code == 200
    body = r.json()
    assert [f["name"] for f in body["files"]] == ["ok.txt"]
    assert "huge.bin" in body["errors"][0]


def test_entirely_oversized_batch_is_413(client):
    r = client.post(
        "/api/uploads",
        files={"files": ("huge.bin", b"x" * (2 * 1024 * 1024), "application/octet-stream")},
    )
    assert r.status_code == 413


def test_get_lists_the_drop_dir(client):
    client.post("/api/uploads", files={"files": ("one.txt", b"1", "text/plain")})
    body = client.get("/api/uploads").json()
    assert [f["name"] for f in body["files"]] == ["one.txt"]


def test_delete_endpoint(client):
    client.post("/api/uploads", files={"files": ("bye.txt", b"1", "text/plain")})
    assert client.delete("/api/uploads/bye.txt").status_code == 200
    assert client.delete("/api/uploads/bye.txt").status_code == 404


def test_delete_endpoint_refuses_an_unknown_name(client):
    assert client.delete("/api/uploads/nope.txt").status_code == 404


# --- the viewer fence -------------------------------------------------------------


def test_uploaded_files_are_viewable(drop, tmp_path, monkeypatch):
    """A path the user just pasted into an agent should also open in muse's own file
    viewer — so the drop dir has to be one of the click-to-view roots."""
    monkeypatch.setenv("MUSE_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("MUSE_CLAUDE_DIR", str(tmp_path / "claude"))
    get_settings.cache_clear()
    e = put("note.md", b"# hi\n")

    from muse.services.events import EventBroker
    from muse.services.session_service import SessionService

    svc = SessionService(EventBroker())
    try:
        assert svc.read_file(e["path"])["content"] == "# hi\n"
        # …and the fence still holds for everything else
        outside = tmp_path / "secret.txt"
        outside.write_text("nope")
        assert "outside" in svc.read_file(str(outside)).get("error", "")
    finally:
        for store in (svc.store, svc.search_index, svc.notify_store, svc.investigations):
            store.close()

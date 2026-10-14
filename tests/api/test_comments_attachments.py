"""Comments + attachments."""
from __future__ import annotations

from tests.conftest import _register_and_login


def _proj_bug(client, key="CMT"):
    r = client.post("/api/v1/projects", json={"project_key": key, "name": key, "description": "d"})
    assert r.status_code == 201
    proj = r.json()
    rb = client.post(f"/api/v1/projects/{proj['id']}/bugs", json={"title": "Comment bug title!", "description": "d"})
    assert rb.status_code == 201
    return proj, rb.json()


def test_comments_crud_and_soft_delete(client):
    _register_and_login(client)
    proj, bug = _proj_bug(client, key="CMT")
    r = client.post(f"/api/v1/bugs/{bug['id']}/comments", json={"body": "First comment"})
    assert r.status_code == 201
    cid = r.json()["id"]
    # edit own
    r2 = client.patch(f"/api/v1/comments/{cid}", json={"body": "Edited"})
    assert r2.status_code == 200
    # delete (soft)
    assert client.delete(f"/api/v1/comments/{cid}").status_code == 200
    # list should not include deleted
    lst = client.get(f"/api/v1/bugs/{bug['id']}/comments").json()
    assert all(c["id"] != cid for c in lst)
    # history records deletion
    h = client.get(f"/api/v1/bugs/{bug['id']}/history").json()
    assert any(x["event_type"] == "comment_deleted" for x in h)


def test_comment_renders_as_text_xss(client):
    _register_and_login(client)
    proj, bug = _proj_bug(client, key="XSS")
    payload = "<script>alert(1)</script><img src=x onerror=alert(2)>"
    r = client.post(f"/api/v1/bugs/{bug['id']}/comments", json={"body": payload})
    assert r.status_code == 201
    # API returns raw text (frontend must use textContent); ensure no HTML execution server-side and body preserved as text
    got = client.get(f"/api/v1/bugs/{bug['id']}/comments").json()
    assert any(payload in c["body"] for c in got)


def _png_bytes():
    # minimal 1x1 PNG
    return bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c626001000000ffff0300000600050b0a2d0000000049454e44ae426082")


def test_upload_download_and_access(client):
    _register_and_login(client)
    proj, bug = _proj_bug(client, key="UPL")
    files = {"file": ("shot.png", _png_bytes(), "image/png")}
    # need raw httpx without json header; TestClient post with files
    # temporarily remove content-type json (TestClient handles)
    r = client.post(f"/api/v1/bugs/{bug['id']}/attachments", files=files)
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    # download
    d = client.get(f"/api/v1/attachments/{aid}/download")
    assert d.status_code == 200
    assert d.headers["X-Content-Type-Options"] == "nosniff"
    assert "attachment" in d.headers["content-disposition"]
    # list
    lst = client.get(f"/api/v1/bugs/{bug['id']}/attachments").json()
    assert len(lst) == 1


def test_malicious_upload_rejected(client):
    _register_and_login(client)
    proj, bug = _proj_bug(client, key="MAL")
    # exe with png name but MZ header
    r = client.post(f"/api/v1/bugs/{bug['id']}/attachments", files={"file": ("evil.png", b"MZ" + b"\x00" * 100, "image/png")})
    assert r.status_code == 400
    # path traversal filename is sanitized but extension check still applies; try .exe
    r2 = client.post(f"/api/v1/bugs/{bug['id']}/attachments", files={"file": ("../../etc/passwd", b"hello", "text/plain")})
    # no extension -> rejected
    assert r2.status_code == 400
    # oversized: craft setting? max is 10MB; we test 413 by sending > limit? avoid huge alloc; check limit logic via small max? skip heavy; test disallowed type
    r3 = client.post(f"/api/v1/bugs/{bug['id']}/attachments", files={"file": ("run.exe", b"MZtest", "application/x-msdownload")})
    assert r3.status_code == 400

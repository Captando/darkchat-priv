import importlib
import os
import tempfile
from io import BytesIO
from pathlib import Path

import sys

import pytest
from fastapi.testclient import TestClient


def make_client():
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    tmp = tempfile.TemporaryDirectory()
    os.environ["DATA_DIR"] = tmp.name
    os.environ["DB_PATH"] = os.path.join(tmp.name, "app.db")
    os.environ["UPLOAD_DIR"] = os.path.join(tmp.name, "uploads")
    import main

    importlib.reload(main)
    client = TestClient(main.app)
    client._tmp = tmp
    return client


@pytest.fixture()
def client():
    return make_client()


def register_and_login(client):
    client.get("/")
    captcha = client.cookies.get("captcha_answer", "")
    r = client.post("/auth/register", data={"username": "u1", "password": "p1", "captcha": captcha})
    assert r.status_code in (200, 303)
    client.get("/")
    captcha = client.cookies.get("captcha_answer", "")
    r = client.post("/auth/login", data={"username": "u1", "password": "p1", "captcha": captcha})
    assert r.status_code in (200, 303)
    return r.cookies


def test_auth_and_app_access(client):
    cookies = register_and_login(client)
    r = client.get("/app", cookies=cookies)
    assert r.status_code == 200


def test_create_room_and_destroy(client):
    cookies = register_and_login(client)
    r = client.get("/new", cookies=cookies, allow_redirects=False)
    assert r.status_code in (302, 307)
    room_url = r.headers["location"]
    r = client.get(room_url, cookies=cookies)
    assert r.status_code == 200
    room_id = room_url.split("/room/")[-1]
    r = client.post(f"/destroy/{room_id}", cookies=cookies)
    assert r.status_code == 200
    r = client.get(room_url, cookies=cookies, allow_redirects=False)
    assert r.status_code in (302, 307)


def test_album_share_flow(client):
    cookies = register_and_login(client)
    files = [
        ("files", ("a.png", BytesIO(b"fake"), "image/png")),
    ]
    r = client.post("/albums", cookies=cookies, data={"caption": "teste"}, files=files)
    assert r.status_code in (200, 303)
    r = client.get("/albums/me", cookies=cookies)
    data = r.json()
    assert data["albums"]
    album_id = data["albums"][0]["id"]
    r = client.post(f"/album/{album_id}/share?ttl_hours=1", cookies=cookies)
    assert r.status_code == 200
    token = r.json()["token"]
    r = client.get(f"/album/shared/{token}")
    assert r.status_code == 200
    r = client.post(f"/album/share/revoke/{token}", cookies=cookies)
    assert r.status_code == 200

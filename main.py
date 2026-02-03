from __future__ import annotations

import json
import math
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
DB_PATH = os.environ.get("DB_PATH", os.path.join(DATA_DIR, "app.db"))
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(DATA_DIR, "uploads"))

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# Volatile, in-memory storage only for chat messages
rooms_messages: Dict[str, List[dict]] = {}
rooms_connections: Dict[str, Set[WebSocket]] = {}
rooms_owner: Dict[str, str] = {}
rooms_banned: Dict[str, Set[str]] = {}
rooms_user_sockets: Dict[str, Dict[str, Set[WebSocket]]] = {}
rooms_user_meta: Dict[str, Dict[str, dict]] = {}
rooms_deleted: Set[str] = set()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            bio TEXT DEFAULT '',
            banner_path TEXT DEFAULT '',
            avatar_path TEXT DEFAULT '',
            btc_address TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        """
    )
    try:
        cur.execute("ALTER TABLE users ADD COLUMN avatar_path TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        cur.execute("ALTER TABLE users ADD COLUMN btc_address TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS locations (
            user_id TEXT PRIMARY KEY,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS blocks (
            blocker_id TEXT NOT NULL,
            blocked_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (blocker_id, blocked_id)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS albums (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            caption TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS media (
            id TEXT PRIMARY KEY,
            album_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS album_shares (
            token TEXT PRIMARY KEY,
            album_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        """
    )
    try:
        cur.execute("ALTER TABLE album_shares ADD COLUMN expires_at TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_photos (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            path TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


@app.on_event("startup")
async def on_startup() -> None:
    init_db()


def now_iso() -> str:
    return datetime.utcnow().isoformat()


def now_dt() -> datetime:
    return datetime.utcnow()


def parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def ext_from_content_type(content_type: str) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/heic": ".heic",
        "image/heif": ".heif",
        "image/avif": ".avif",
    }
    return mapping.get(content_type.lower(), "")


def ensure_room(room_id: str) -> None:
    rooms_messages.setdefault(room_id, [])
    rooms_connections.setdefault(room_id, set())
    rooms_banned.setdefault(room_id, set())
    rooms_user_sockets.setdefault(room_id, {})
    rooms_user_meta.setdefault(room_id, {})


async def broadcast(room_id: str, msg: dict) -> None:
    dead = []
    for conn in rooms_connections.get(room_id, set()):
        try:
            await conn.send_json(msg)
        except Exception:
            dead.append(conn)
    for conn in dead:
        rooms_connections[room_id].discard(conn)


def get_session_user(request: Request) -> Optional[sqlite3.Row]:
    session_id = request.cookies.get("session")
    if not session_id:
        return None
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT users.* FROM sessions
        JOIN users ON users.id = sessions.user_id
        WHERE sessions.id = ?
        """,
        (session_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def require_user(request: Request) -> sqlite3.Row:
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


@app.get("/")
async def home(request: Request):
    user = get_session_user(request)
    if user:
        return RedirectResponse(url="/app")
    # lightweight captcha (simple math)
    a = uuid.uuid4().int % 9 + 1
    b = (uuid.uuid4().int // 10) % 9 + 1
    answer = str(a + b)
    resp = templates.TemplateResponse(
        "index.html",
        {"request": request, "mode": "auth", "user": None, "captcha_q": f"{a} + {b}"},
    )
    resp.set_cookie("captcha_answer", answer, max_age=600, httponly=True)
    return resp


@app.get("/app")
async def app_home(request: Request):
    user = get_session_user(request)
    if not user:
        return RedirectResponse(url="/")
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "mode": "app",
            "user": dict(user),
            "open_profile": request.query_params.get("profile") == "1",
        },
    )


@app.post("/auth/register")
async def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    captcha: str = Form(...),
):
    if captcha.strip() != (request.cookies.get("captcha_answer") or ""):
        raise HTTPException(status_code=400, detail="Invalid captcha")
    conn = get_db()
    cur = conn.cursor()
    user_id = str(uuid.uuid4())
    try:
        cur.execute(
            """
            INSERT INTO users (id, username, password_hash, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, username, pwd_context.hash(password), now_iso()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Username already exists")
    conn.close()
    return RedirectResponse(url="/", status_code=303)


@app.post("/auth/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    captcha: str = Form(...),
):
    if captcha.strip() != (request.cookies.get("captcha_answer") or ""):
        raise HTTPException(status_code=400, detail="Invalid captcha")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ?", (username,))
    user = cur.fetchone()
    if not user or not pwd_context.verify(password, user["password_hash"]):
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid credentials")

    session_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO sessions (id, user_id, created_at) VALUES (?, ?, ?)",
        (session_id, user["id"], now_iso()),
    )
    conn.commit()
    conn.close()

    resp = RedirectResponse(url="/app", status_code=303)
    resp.set_cookie("session", session_id, httponly=True)
    return resp


@app.post("/auth/logout")
async def logout(request: Request):
    session_id = request.cookies.get("session")
    if session_id:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        conn.close()
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie("session")
    return resp


@app.post("/me/profile")
async def update_profile(
    request: Request,
    username: str = Form(""),
    bio: str = Form(""),
    btc_address: str = Form(""),
    banner: UploadFile = File(None),
    avatar: UploadFile = File(None),
):
    user = require_user(request)
    banner_path = user["banner_path"]
    avatar_path = user["avatar_path"]
    btc = btc_address.strip()

    if banner is not None:
        ext = os.path.splitext(banner.filename or "")[1].lower()
        if not ext:
            ext = ext_from_content_type(banner.content_type or "")
        filename = f"banner-{user['id']}-{uuid.uuid4().hex}{ext}"
        dest = os.path.join(UPLOAD_DIR, filename)
        with open(dest, "wb") as f:
            f.write(await banner.read())
        banner_path = f"/uploads/{filename}"
    if avatar is not None:
        ext = os.path.splitext(avatar.filename or "")[1].lower()
        if not ext:
            ext = ext_from_content_type(avatar.content_type or "")
        filename = f"avatar-{user['id']}-{uuid.uuid4().hex}{ext}"
        dest = os.path.join(UPLOAD_DIR, filename)
        with open(dest, "wb") as f:
            f.write(await avatar.read())
        avatar_path = f"/uploads/{filename}"

    conn = get_db()
    cur = conn.cursor()
    if username and username != user["username"]:
        cur.execute("SELECT 1 FROM users WHERE username = ? AND id != ?", (username, user["id"]))
        if cur.fetchone():
            conn.close()
            raise HTTPException(status_code=400, detail="Username already exists")
    cur.execute(
        "UPDATE users SET username = ?, bio = ?, banner_path = ?, avatar_path = ?, btc_address = ? WHERE id = ?",
        (username or user["username"], bio, banner_path, avatar_path, btc, user["id"]),
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url="/app?profile=1", status_code=303)


@app.get("/me/photos")
async def list_profile_photos(request: Request):
    user = require_user(request)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, path FROM profile_photos WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],),
    )
    photos = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"photos": photos}


@app.post("/me/photos")
async def upload_profile_photos(request: Request, files: List[UploadFile] = File(...)):
    user = require_user(request)
    conn = get_db()
    cur = conn.cursor()
    for f in files:
        if not (f.content_type or "").startswith("image/"):
            continue
        ext = os.path.splitext(f.filename or "")[1].lower()
        photo_id = str(uuid.uuid4())
        filename = f"profile-{user['id']}-{photo_id}{ext}"
        dest = os.path.join(UPLOAD_DIR, filename)
        with open(dest, "wb") as out:
            out.write(await f.read())
        cur.execute(
            "INSERT INTO profile_photos (id, user_id, path, created_at) VALUES (?, ?, ?, ?)",
            (photo_id, user["id"], f"/uploads/{filename}", now_iso()),
        )
    conn.commit()
    conn.close()
    return RedirectResponse(url="/app?profile=1", status_code=303)


@app.post("/me/photos/delete/{photo_id}")
async def delete_profile_photo(request: Request, photo_id: str):
    user = require_user(request)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT path FROM profile_photos WHERE id = ? AND user_id = ?",
        (photo_id, user["id"]),
    )
    row = cur.fetchone()
    if row:
        path = row["path"]
        cur.execute(
            "DELETE FROM profile_photos WHERE id = ? AND user_id = ?", (photo_id, user["id"])
        )
        try:
            if path.startswith("/uploads/"):
                os.remove(os.path.join(UPLOAD_DIR, path.replace("/uploads/", "")))
        except Exception:
            pass
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/me/location")
async def update_location(
    request: Request,
    lat: float = Form(...),
    lon: float = Form(...),
):
    user = require_user(request)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO locations (user_id, lat, lon, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            lat = excluded.lat,
            lon = excluded.lon,
            updated_at = excluded.updated_at
        """,
        (user["id"], lat, lon, now_iso()),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/users/nearby")
async def nearby_users(request: Request, radius_km: float = 5.0):
    user = require_user(request)
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT lat, lon FROM locations WHERE user_id = ?", (user["id"],))
    me_loc = cur.fetchone()
    if not me_loc:
        conn.close()
        return {"users": []}

    cur.execute(
        """
        SELECT users.id, users.username, users.bio, users.banner_path, users.avatar_path, locations.lat, locations.lon
        FROM locations
        JOIN users ON users.id = locations.user_id
        WHERE users.id != ?
        """,
        (user["id"],),
    )
    rows = cur.fetchall()

    cur.execute(
        "SELECT blocked_id FROM blocks WHERE blocker_id = ?",
        (user["id"],),
    )
    blocked_out = {r["blocked_id"] for r in cur.fetchall()}
    cur.execute(
        "SELECT blocker_id FROM blocks WHERE blocked_id = ?",
        (user["id"],),
    )
    blocked_in = {r["blocker_id"] for r in cur.fetchall()}

    results = []
    for r in rows:
        if r["id"] in blocked_out or r["id"] in blocked_in:
            continue
        dist = haversine_km(me_loc["lat"], me_loc["lon"], r["lat"], r["lon"])
        if dist <= radius_km:
            results.append(
                {
                    "id": r["id"],
                    "username": r["username"],
                    "bio": r["bio"],
                    "banner": r["banner_path"],
                    "avatar": r["avatar_path"],
                    "distance": round(dist, 2),
                }
            )

    conn.close()
    results.sort(key=lambda x: x["distance"])
    return {"users": results}


@app.post("/block/{user_id}")
async def block_user(request: Request, user_id: str):
    user = require_user(request)
    if user_id == user["id"]:
        raise HTTPException(status_code=400, detail="Cannot block self")
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO blocks (blocker_id, blocked_id, created_at) VALUES (?, ?, ?)",
        (user["id"], user_id, now_iso()),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/albums")
async def create_album(
    request: Request,
    caption: str = Form(""),
    files: List[UploadFile] = File(...),
):
    user = require_user(request)
    album_id = str(uuid.uuid4())
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO albums (id, user_id, caption, created_at) VALUES (?, ?, ?, ?)",
        (album_id, user["id"], caption, now_iso()),
    )

    for f in files:
        content_type = f.content_type or "application/octet-stream"
        ext = os.path.splitext(f.filename or "")[1].lower()
        media_id = str(uuid.uuid4())
        filename = f"{media_id}{ext}"
        dest = os.path.join(UPLOAD_DIR, filename)
        with open(dest, "wb") as out:
            out.write(await f.read())
        cur.execute(
            "INSERT INTO media (id, album_id, kind, path, created_at) VALUES (?, ?, ?, ?, ?)",
            (media_id, album_id, content_type, f"/uploads/{filename}", now_iso()),
        )

    conn.commit()
    conn.close()
    return RedirectResponse(url="/app", status_code=303)


@app.get("/albums/me")
async def my_albums(request: Request):
    user = require_user(request)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM albums WHERE user_id = ? ORDER BY created_at DESC",
        (user["id"],),
    )
    albums = []
    for a in cur.fetchall():
        cur.execute(
            "SELECT * FROM media WHERE album_id = ? ORDER BY created_at ASC",
            (a["id"],),
        )
        albums.append(
            {
                "id": a["id"],
                "caption": a["caption"],
                "media": [dict(m) for m in cur.fetchall()],
            }
        )
    conn.close()
    return {"albums": albums}


@app.get("/album/{album_id}")
async def view_album(request: Request, album_id: str):
    user = require_user(request)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM albums WHERE id = ? AND user_id = ?", (album_id, user["id"]))
    album = cur.fetchone()
    if not album:
        conn.close()
        return RedirectResponse(url="/app")
    cur.execute("SELECT * FROM media WHERE album_id = ? ORDER BY created_at ASC", (album_id,))
    media = [dict(m) for m in cur.fetchall()]
    conn.close()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "mode": "album",
            "user": dict(user),
            "album": {**dict(album), "media": media},
            "shared": False,
        },
    )


@app.post("/album/{album_id}/share")
async def share_album(request: Request, album_id: str, ttl_hours: int = 24):
    user = require_user(request)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM albums WHERE id = ? AND user_id = ?", (album_id, user["id"]))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Album not found")
    if ttl_hours not in {1, 24, 168}:
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid ttl")
    token = str(uuid.uuid4())
    expires_at = (now_dt() + timedelta(hours=ttl_hours)).isoformat()
    cur.execute(
        "INSERT INTO album_shares (token, album_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, album_id, now_iso(), expires_at),
    )
    conn.commit()
    conn.close()
    return {"token": token, "expires_at": expires_at}


@app.get("/album/{album_id}/shares")
async def list_album_shares(request: Request, album_id: str):
    user = require_user(request)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM albums WHERE id = ? AND user_id = ?", (album_id, user["id"]))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Album not found")
    cur.execute(
        "SELECT token, created_at, expires_at FROM album_shares WHERE album_id = ? ORDER BY created_at DESC",
        (album_id,),
    )
    shares = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {"shares": shares}


@app.post("/album/share/revoke/{token}")
async def revoke_share_token(request: Request, token: str):
    user = require_user(request)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT albums.id AS album_id FROM album_shares
        JOIN albums ON albums.id = album_shares.album_id
        WHERE album_shares.token = ? AND albums.user_id = ?
        """,
        (token, user["id"]),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Token not found")
    cur.execute("DELETE FROM album_shares WHERE token = ?", (token,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/album/{album_id}/revoke")
async def revoke_album_shares(request: Request, album_id: str):
    user = require_user(request)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM albums WHERE id = ? AND user_id = ?", (album_id, user["id"]))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Album not found")
    cur.execute("DELETE FROM album_shares WHERE album_id = ?", (album_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/album/shared/{token}")
async def view_shared_album(request: Request, token: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT albums.*, album_shares.expires_at FROM album_shares JOIN albums ON albums.id = album_shares.album_id WHERE album_shares.token = ?",
        (token,),
    )
    album = cur.fetchone()
    if not album:
        conn.close()
        return RedirectResponse(url="/")
    if album["expires_at"]:
        exp = parse_iso(album["expires_at"])
        if exp and exp < now_dt():
            conn.close()
            return RedirectResponse(url="/")
    cur.execute("SELECT * FROM media WHERE album_id = ? ORDER BY created_at ASC", (album["id"],))
    media = [dict(m) for m in cur.fetchall()]
    conn.close()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "mode": "album",
            "user": None,
            "album": {**dict(album), "media": media},
            "shared": True,
        },
    )


@app.get("/new")
async def new_room(request: Request):
    user = require_user(request)
    room_id = str(uuid.uuid4())
    rooms_deleted.discard(room_id)
    rooms_owner[room_id] = user["id"]
    return RedirectResponse(url=f"/room/{room_id}")


@app.get("/room/{room_id}")
async def chat_room(request: Request, room_id: str):
    require_user(request)
    try:
        uuid.UUID(room_id)
    except ValueError:
        return RedirectResponse(url="/app")

    if room_id in rooms_deleted:
        return RedirectResponse(url="/app")

    ensure_room(room_id)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "mode": "chat",
            "room_id": room_id,
            "room_owner_id": rooms_owner.get(room_id, ""),
        },
    )


@app.post("/destroy/{room_id}")
async def destroy_room(request: Request, room_id: str):
    user = require_user(request)
    owner_id = rooms_owner.get(room_id)
    if owner_id and owner_id != user["id"]:
        raise HTTPException(status_code=403, detail="Only owner can destroy this room")
    conns = rooms_connections.get(room_id, set())
    for ws in list(conns):
        try:
            await ws.close(code=1000)
        except Exception:
            pass
    rooms_connections.pop(room_id, None)
    rooms_messages.pop(room_id, None)
    rooms_owner.pop(room_id, None)
    rooms_banned.pop(room_id, None)
    rooms_user_sockets.pop(room_id, None)
    rooms_user_meta.pop(room_id, None)
    rooms_deleted.add(room_id)
    return {"ok": True}


@app.post("/room/{room_id}/kick/{user_id}")
async def kick_user(request: Request, room_id: str, user_id: str):
    user = require_user(request)
    owner_id = rooms_owner.get(room_id)
    if owner_id and owner_id != user["id"]:
        raise HTTPException(status_code=403, detail="Only owner can kick/ban")
    ensure_room(room_id)
    conns = rooms_user_sockets.get(room_id, {}).get(user_id, set())
    for ws in list(conns):
        try:
            await ws.close(code=4000)
        except Exception:
            pass
    return {"ok": True}


@app.post("/room/{room_id}/ban/{user_id}")
async def ban_user(request: Request, room_id: str, user_id: str):
    user = require_user(request)
    owner_id = rooms_owner.get(room_id)
    if owner_id and owner_id != user["id"]:
        raise HTTPException(status_code=403, detail="Only owner can kick/ban")
    ensure_room(room_id)
    rooms_banned[room_id].add(user_id)
    conns = rooms_user_sockets.get(room_id, {}).get(user_id, set())
    for ws in list(conns):
        try:
            await ws.close(code=4003)
        except Exception:
            pass
    return {"ok": True}


@app.post("/room/{room_id}/media")
async def upload_room_media(
    request: Request,
    room_id: str,
    files: List[UploadFile] = File(...),
):
    user = require_user(request)
    try:
        uuid.UUID(room_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid room")

    uploaded = []
    for f in files:
        content_type = f.content_type or "application/octet-stream"
        ext = os.path.splitext(f.filename or "")[1].lower()
        media_id = str(uuid.uuid4())
        filename = f"room-{room_id}-{media_id}{ext}"
        dest = os.path.join(UPLOAD_DIR, filename)
        with open(dest, "wb") as out:
            out.write(await f.read())
        uploaded.append(
            {
                "type": "media",
                "url": f"/uploads/{filename}",
                "kind": content_type,
                "user_id": user["id"],
                "username": user["username"],
                "avatar": user["avatar_path"],
                "ts": now_iso(),
            }
        )
    return {"items": uploaded}


@app.get("/room/{room_id}/online")
async def room_online(request: Request, room_id: str):
    require_user(request)
    owner = rooms_owner.get(room_id)
    if not owner:
        raise HTTPException(status_code=404, detail="Room not found")
    users = []
    meta = rooms_user_meta.get(room_id, {})
    for uid, sockets in rooms_user_sockets.get(room_id, {}).items():
        if sockets:
            info = meta.get(uid, {"username": uid, "avatar": ""})
            users.append(
                {"id": uid, "username": info.get("username", uid), "avatar": info.get("avatar", "")}
            )
    return {"users": users}


@app.websocket("/ws/{room_id}")
async def websocket_endpoint(ws: WebSocket, room_id: str):
    await ws.accept()

    # Require authenticated user via session cookie
    session_id = ws.cookies.get("session")
    if not session_id:
        await ws.close(code=1008)
        return
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT users.id, users.username, users.avatar_path FROM sessions
        JOIN users ON users.id = sessions.user_id
        WHERE sessions.id = ?
        """,
        (session_id,),
    )
    user = cur.fetchone()
    conn.close()
    if not user:
        await ws.close(code=1008)
        return

    try:
        uuid.UUID(room_id)
    except ValueError:
        await ws.close(code=1008)
        return

    ensure_room(room_id)
    if room_id in rooms_deleted:
        await ws.close(code=4100)
        return
    if user["id"] in rooms_banned.get(room_id, set()):
        await ws.close(code=4003)
        return
    rooms_connections[room_id].add(ws)
    rooms_user_sockets.setdefault(room_id, {}).setdefault(user["id"], set()).add(ws)
    rooms_user_meta.setdefault(room_id, {})[user["id"]] = {
        "username": user["username"],
        "avatar": user["avatar_path"],
    }

    await broadcast(
        room_id,
        {
            "type": "system",
            "text": f"{user['username']} entrou na sala",
            "ts": now_iso(),
        },
    )

    for msg in rooms_messages.get(room_id, []):
        await ws.send_json(msg)

    try:
        while True:
            data = await ws.receive_text()
            msg = None
            try:
                payload = json.loads(data)
                if isinstance(payload, dict) and payload.get("type") == "message":
                    msg = {
                        "type": "message",
                        "text": payload.get("text", ""),
                        "enc": payload.get("enc", False),
                        "ct": payload.get("ct", ""),
                        "iv": payload.get("iv", ""),
                        "salt": payload.get("salt", ""),
                        "msg_id": payload.get("msg_id", ""),
                        "user_id": user["id"],
                        "username": user["username"],
                        "avatar": user["avatar_path"],
                        "ts": now_iso(),
                    }
                if isinstance(payload, dict) and payload.get("type") == "media":
                    msg = {
                        "type": "media",
                        "url": payload.get("url", ""),
                        "kind": payload.get("kind", "application/octet-stream"),
                        "enc": payload.get("enc", False),
                        "iv": payload.get("iv", ""),
                        "salt": payload.get("salt", ""),
                        "orig_kind": payload.get("orig_kind", ""),
                        "msg_id": payload.get("msg_id", ""),
                        "user_id": user["id"],
                        "username": user["username"],
                        "avatar": user["avatar_path"],
                        "ts": now_iso(),
                    }
                if isinstance(payload, dict) and payload.get("type") == "album":
                    msg = {
                        "type": "album",
                        "url": payload.get("url", ""),
                        "title": payload.get("title", ""),
                        "enc": payload.get("enc", False),
                        "ct": payload.get("ct", ""),
                        "iv": payload.get("iv", ""),
                        "salt": payload.get("salt", ""),
                        "msg_id": payload.get("msg_id", ""),
                        "user_id": user["id"],
                        "username": user["username"],
                        "avatar": user["avatar_path"],
                        "ts": now_iso(),
                    }
                if isinstance(payload, dict) and payload.get("type") == "location":
                    if payload.get("enc"):
                        msg = {
                            "type": "location",
                            "enc": True,
                            "ct": payload.get("ct", ""),
                            "iv": payload.get("iv", ""),
                            "salt": payload.get("salt", ""),
                            "msg_id": payload.get("msg_id", ""),
                            "user_id": user["id"],
                            "username": user["username"],
                            "avatar": user["avatar_path"],
                            "ts": now_iso(),
                        }
                    else:
                        msg = {
                            "type": "location",
                            "lat": float(payload.get("lat", 0)),
                            "lon": float(payload.get("lon", 0)),
                            "msg_id": payload.get("msg_id", ""),
                            "user_id": user["id"],
                            "username": user["username"],
                            "avatar": user["avatar_path"],
                            "ts": now_iso(),
                        }
                if isinstance(payload, dict) and payload.get("type") == "typing":
                    msg = {
                        "type": "typing",
                        "state": bool(payload.get("state", False)),
                        "user_id": user["id"],
                        "username": user["username"],
                        "ts": now_iso(),
                    }
                if isinstance(payload, dict) and payload.get("type") == "read":
                    msg = {
                        "type": "read",
                        "msg_id": payload.get("msg_id", ""),
                        "user_id": user["id"],
                        "username": user["username"],
                        "ts": now_iso(),
                    }
            except Exception:
                msg = None

            if msg is None:
                msg = {
                    "type": "message",
                    "text": data,
                    "enc": False,
                    "user_id": user["id"],
                    "username": user["username"],
                    "avatar": user["avatar_path"],
                    "ts": now_iso(),
                }
            rooms_messages[room_id].append(msg)

            dead = []
            for conn in rooms_connections.get(room_id, set()):
                try:
                    await conn.send_json(msg)
                except Exception:
                    dead.append(conn)
            for conn in dead:
                rooms_connections[room_id].discard(conn)
    except WebSocketDisconnect:
        rooms_connections[room_id].discard(ws)
        rooms_user_sockets.get(room_id, {}).get(user["id"], set()).discard(ws)
        if not rooms_user_sockets.get(room_id, {}).get(user["id"]):
            rooms_user_meta.get(room_id, {}).pop(user["id"], None)
            await broadcast(
                room_id,
                {
                    "type": "system",
                    "text": f"{user['username']} saiu da sala",
                    "ts": now_iso(),
                },
            )
    except Exception:
        rooms_connections[room_id].discard(ws)
        rooms_user_sockets.get(room_id, {}).get(user["id"], set()).discard(ws)
        if not rooms_user_sockets.get(room_id, {}).get(user["id"]):
            rooms_user_meta.get(room_id, {}).pop(user["id"], None)
            await broadcast(
                room_id,
                {
                    "type": "system",
                    "text": f"{user['username']} saiu da sala",
                    "ts": now_iso(),
                },
            )
        try:
            await ws.close(code=1011)
        except Exception:
            pass

"""Admin credentials and persistent, revocable browser sessions."""
from __future__ import annotations

import configparser
import hashlib
import logging
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request
from locales.localizer import Localizer

ADMIN_CFG = "configs/admin.cfg"
CSRF_TOKEN_FILE = "storage/cache/csrf_token.txt"
SESSION_DB = Path("storage/cache/admin_sessions.sqlite3")
SESSION_COOKIE = "fpc_admin_session"
LOGIN_CSRF_COOKIE = "fpc_login_csrf"
SESSION_SECONDS = 8 * 60 * 60
logger = logging.getLogger("FPC.admin.auth")


def translate(key: str) -> str:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read("configs/_main.cfg", encoding="utf-8")
    return Localizer().translate(key, language=cfg.get("Other", "language", fallback="en"))


def init_admin_cfg() -> None:
    if not os.path.exists(ADMIN_CFG):
        config = configparser.ConfigParser(interpolation=None)
        config["admin"] = {"username": "admin", "password": "changeme"}
        Path(ADMIN_CFG).parent.mkdir(parents=True, exist_ok=True)
        with open(ADMIN_CFG, "w", encoding="utf-8") as f:
            config.write(f)


def credentials() -> tuple[str, str]:
    init_admin_cfg()
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(ADMIN_CFG, encoding="utf-8")
    return cfg["admin"]["username"], cfg["admin"]["password"]


def credential_digest(username: str, password: str) -> str:
    return hashlib.sha256((username + "\0" + password).encode()).hexdigest()


def check_credentials(username: str, password: str) -> bool:
    expected_user, expected_password = credentials()
    user_ok = secrets.compare_digest(username.encode(), expected_user.encode())
    password_ok = secrets.compare_digest(password.encode(), expected_password.encode())
    return user_ok and password_ok


@contextmanager
def session_database() -> Iterator[sqlite3.Connection]:
    SESSION_DB.parent.mkdir(parents=True, exist_ok=True)
    # Store only token hashes; sessions survive worker restarts and work across workers.
    fd = os.open(SESSION_DB, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    db = sqlite3.connect(SESSION_DB, timeout=10)
    try:
        with db:
            db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, username TEXT NOT NULL,
                credentials TEXT NOT NULL, expires REAL NOT NULL)""")
            yield db
    finally:
        db.close()


def create_session(username: str, password: str) -> str:
    current_user, current_password = credentials()
    if not (secrets.compare_digest(username.encode(), current_user.encode()) and
            secrets.compare_digest(password.encode(), current_password.encode())):
        raise ValueError("Credentials changed during sign-in")
    token = secrets.token_urlsafe(32)
    with session_database() as db:
        db.execute("DELETE FROM sessions WHERE expires <= ?", (time.time(),))
        db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                   (hashlib.sha256(token.encode()).hexdigest(), username,
                    credential_digest(current_user, password), time.time() + SESSION_SECONDS))
    return token


def session_user(token: str | None) -> str | None:
    if not token or len(token) > 128:
        return None
    with session_database() as db:
        row = db.execute("SELECT username, credentials, expires FROM sessions WHERE token_hash = ?",
                         (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if row is None or row[2] <= time.time():
        return None
    username, password = credentials()
    if row[0] != username or not secrets.compare_digest(row[1], credential_digest(username, password)):
        return None
    return username


def delete_session(token: str | None) -> None:
    if token:
        with session_database() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode()).hexdigest(),))


def get_current_user(request: Request) -> str:
    user = session_user(request.cookies.get(SESSION_COOKIE))
    if user:
        return user
    destination = request.url.path if request.method in ("GET", "HEAD") else "/"
    raise HTTPException(status_code=303, headers={"Location": "/login?next=" + quote(destination, safe="")})


def get_csrf_token() -> str:
    Path(CSRF_TOKEN_FILE).parent.mkdir(parents=True, exist_ok=True)
    if os.path.exists(CSRF_TOKEN_FILE):
        with open(CSRF_TOKEN_FILE, "r", encoding="utf-8") as f:
            token = f.read().strip()
            if token:
                return token
    token = secrets.token_urlsafe(32)
    with open(CSRF_TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    return token


def validate_csrf_token(token: str) -> bool:
    return secrets.compare_digest(get_csrf_token().encode(), token.encode())

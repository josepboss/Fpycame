import configparser
import os
import secrets
import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()
ADMIN_CFG = "configs/admin.cfg"
CSRF_TOKEN_FILE = "storage/cache/csrf_token.txt"

logger = logging.getLogger("admin.auth")


def init_admin_cfg():
    if not os.path.exists(ADMIN_CFG):
        config = configparser.ConfigParser()
        config["admin"] = {"username": "admin", "password": "changeme"}
        with open(ADMIN_CFG, "w", encoding="utf-8") as f:
            config.write(f)


def get_current_user(credentials: HTTPBasicCredentials = Depends(security)):
    init_admin_cfg()
    config = configparser.ConfigParser()
    config.read(ADMIN_CFG, encoding="utf-8")

    correct_username = config["admin"]["username"]
    correct_password = config["admin"]["password"]

    if credentials.username != correct_username or credentials.password != correct_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def get_csrf_token() -> str:
    """
    Return the current CSRF token, generating a new one if it doesn't exist.
    """
    os.makedirs("storage/cache", exist_ok=True)
    if os.path.exists(CSRF_TOKEN_FILE):
        with open(CSRF_TOKEN_FILE, "r", encoding="utf-8") as f:
            token = f.read().strip()
            if token:
                return token
    # Generate a new token
    token = secrets.token_urlsafe(32)
    with open(CSRF_TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    return token


def validate_csrf_token(token: str) -> bool:
    """Compare provided token against stored token."""
    stored = get_csrf_token()
    return secrets.compare_digest(stored, token)
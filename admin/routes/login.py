"""HTML login/logout without browser HTTP Basic authentication challenges."""
from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from admin import auth

router = APIRouter()
templates = Jinja2Templates(directory="admin/templates")


def destination(value: str) -> str:
    # Never redirect credentials or a logged-in user to an external URL.
    if (not value.startswith("/") or value.startswith("//") or
            "\\" in value or any(ord(c) < 32 for c in value)):
        return "/"
    parsed = urlsplit(value)
    return value if not parsed.scheme and not parsed.netloc else "/"


def login_form(request: Request, next_url: str, error: bool = False) -> Response:
    token = secrets.token_urlsafe(32)
    response = templates.TemplateResponse(request=request, name="login.html", context={
        "request": request, "_": auth.translate, "csrf_token": token,
        "next_url": destination(next_url), "error": error,
    }, status_code=401 if error else 200)
    response.set_cookie(auth.LOGIN_CSRF_COOKIE, token, max_age=600, httponly=True,
                        secure=request.url.scheme == "https", samesite="strict", path="/login")
    return response


@router.get("/login")
def login_page(request: Request, next: str = "/") -> Response:
    if auth.session_user(request.cookies.get(auth.SESSION_COOKIE)):
        return RedirectResponse(destination(next), status_code=303)
    return login_form(request, next)


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...),
          csrf_token: str = Form(""), next: str = Form("/")) -> Response:
    expected = request.cookies.get(auth.LOGIN_CSRF_COOKIE, "")
    if not expected or not secrets.compare_digest(expected.encode(), csrf_token.encode()):
        raise HTTPException(status_code=403, detail=auth.translate("auth_csrf"))
    if not auth.check_credentials(username, password):
        return login_form(request, next, error=True)
    # Replace any existing session instead of accepting a client-selected session ID.
    auth.delete_session(request.cookies.get(auth.SESSION_COOKIE))
    try:
        token = auth.create_session(username, password)
    except ValueError:
        return login_form(request, next, error=True)
    response = RedirectResponse(destination(next), status_code=303)
    response.set_cookie(auth.SESSION_COOKIE, token, max_age=auth.SESSION_SECONDS,
                        httponly=True, secure=request.url.scheme == "https",
                        samesite="strict", path="/")
    response.delete_cookie(auth.LOGIN_CSRF_COOKIE, path="/login")
    return response


@router.get("/logout")
def logout_page(request: Request, user: str = Depends(auth.get_current_user)) -> Response:
    return templates.TemplateResponse(request=request, name="login.html", context={
        "request": request, "_": auth.translate, "logout": True,
        "csrf_token": auth.get_csrf_token(),
    })


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(""),
           user: str = Depends(auth.get_current_user)) -> Response:
    if not auth.validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403, detail=auth.translate("auth_csrf"))
    auth.delete_session(request.cookies.get(auth.SESSION_COOKIE))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response

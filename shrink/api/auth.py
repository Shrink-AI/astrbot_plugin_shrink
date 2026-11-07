import asyncio
import hashlib
import hmac
import os
import secrets
import time
from collections import deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from ..services.memes import MAX_IMAGE_BYTES
from .schemas import Login, PasswordChange


def install_auth(app, state, sessions, allowed_hosts):
    ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1)
    if not state.get("admin_hash"):
        password = secrets.token_urlsafe(24)
        state.put("admin_hash", ph.hash(password))
        bootstrap = state.root / "initial-password.txt"
        fd = os.open(bootstrap, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(password + "\n")
    attempts = deque()
    auth_lock = asyncio.Lock()
    allowed_origins = {"http://" + host for host in allowed_hosts}
    cookie_name = "shrink_session"

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host") not in allowed_hosts:
            return JSONResponse({"detail": "不允许的访问地址"}, status_code=400)
        mutation = request.method not in ("GET", "HEAD", "OPTIONS")
        try:
            content_length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail": "无效的请求长度"}, status_code=400)
        if content_length > MAX_IMAGE_BYTES + 65536:
            return JSONResponse({"detail": "上传文件过大"}, status_code=413)
        if mutation and request.headers.get("origin") not in allowed_origins:
            return JSONResponse({"detail": "请求来源无效"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "不允许跨站请求"}, status_code=403)
        path = request.url.path
        if path.startswith("/api/v1/") and path not in ("/api/v1/login", "/api/v1/health"):
            token_hash = hashlib.sha256(request.cookies.get(cookie_name, "").encode()).hexdigest()
            session = sessions.active(token_hash, time.time())
            if not session:
                return JSONResponse({"detail": "请先登录"}, status_code=401)
            request.state.session = session
            if mutation and not hmac.compare_digest(
                request.headers.get("x-csrf-token", ""), session["csrf"]
            ):
                return JSONResponse({"detail": "操作凭证无效，请刷新页面"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'"
        )
        return response

    @app.post("/api/v1/login")
    async def login(body: Login, request: Request):
        async with auth_lock:
            now = time.time()
            while attempts and attempts[0] < now - 300:
                attempts.popleft()
            if len(attempts) >= 5:
                raise HTTPException(429, "尝试过于频繁，请五分钟后再试")
            attempts.append(now)
            try:
                await asyncio.to_thread(ph.verify, state.get("admin_hash"), body.password)
            except (VerificationError, InvalidHashError):
                raise HTTPException(401, "密码不正确") from None
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            old = hashlib.sha256(request.cookies.get(cookie_name, "").encode()).hexdigest()
            sessions.rotate(old, hashlib.sha256(token.encode()).hexdigest(), csrf, now)
            response = JSONResponse({"csrf": csrf})
            response.set_cookie(
                cookie_name, token, httponly=True, samesite="strict", max_age=28800, path="/"
            )
            return response

    @app.get("/api/v1/session")
    async def session(request: Request):
        return {"csrf": request.state.session["csrf"]}

    @app.post("/api/v1/logout")
    async def logout(request: Request):
        sessions.revoke(request.state.session["token_hash"])
        response = JSONResponse({"ok": True})
        response.delete_cookie(cookie_name)
        return response

    @app.post("/api/v1/password")
    async def change_password(body: PasswordChange):
        async with auth_lock:
            try:
                await asyncio.to_thread(ph.verify, state.get("admin_hash"), body.current_password)
            except VerificationError:
                raise HTTPException(401, "当前密码不正确") from None
            state.put("admin_hash", await asyncio.to_thread(ph.hash, body.new_password))
            sessions.revoke_all()
            (state.root / "initial-password.txt").unlink(missing_ok=True)
            return {"ok": True}

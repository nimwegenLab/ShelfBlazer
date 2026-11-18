"""Request-level plumbing shared by every page: the request body limit, the security headers (plus the reload of commits made
outside this process) and the error pages."""
import re
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from shelfblazer.store import Conflict, Forbidden, NotFound
from .files import MAX_REQUEST_BYTES

# Request bodies: forms that may carry files (only after sign-in) get the upload cap, everything else a small one.
MAX_FORM_BYTES = 4 * 1024 * 1024
UPLOAD_PATHS = re.compile(r"^/(t/[^/]+/new|r/[^/]+/edit|r/[^/]+/files)$")


class NeedLogin(Exception):
    pass


class BodyLimit:
    """The one place that limits request bodies, before any route reads them: over the limit, whether the size is declared
    (Content-Length) or not (chunked: counted while the body arrives); an upload must also state its size."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        upload = bool(UPLOAD_PATHS.match(scope["path"]))
        limit = MAX_REQUEST_BYTES if upload else MAX_FORM_BYTES
        headers = dict(scope["headers"])
        length = headers.get(b"content-length")
        if length is None and upload and headers.get(b"content-type", b"").startswith(b"multipart/"):
            return await self._refuse(send, limit, 411, b"The upload must state its size.")
        if length is not None and (not length.isdigit() or int(length) > limit):
            return await self._refuse(send, limit)
        seen = 0

        async def counted():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:
                    raise HTTPException(413, self._message(limit))
            return msg
        await self.app(scope, counted, send)

    @staticmethod
    def _message(limit):
        return f"Too much data at once (at most {limit // (1024 * 1024)} MB)."

    async def _refuse(self, send, limit, status=413, body=None):
        body = body or self._message(limit).encode()
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode()),
            (b"x-content-type-options", b"nosniff"), (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})


def add_security_headers(app, store, https_only):
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if not request.url.path.startswith("/static/"):
            store.refresh()                                  # a commit made outside this process (maintenance command, restore): reload first
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self' 'wasm-unsafe-eval'; frame-ancestors 'none'; form-action 'self'"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Cache-Control"] = "no-store"
        if https_only:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        return resp


def add_error_pages(app, render):
    @app.exception_handler(NeedLogin)
    async def _need_login(request, exc):
        nxt = request.url.path + (("?" + request.url.query) if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(nxt)}", status_code=303)

    @app.exception_handler(HTTPException)
    async def _http_exc(request, exc):
        return render(request, "error.html", status=exc.status_code, code=exc.status_code, message=str(exc.detail))

    @app.exception_handler(Forbidden)
    async def _forbidden(request, exc):
        return render(request, "error.html", status=403, code=403, message=str(exc))

    @app.exception_handler(NotFound)
    async def _notfound(request, exc):
        return render(request, "error.html", status=404, code=404, message=f"{exc} not found")

    @app.exception_handler(Conflict)
    async def _conflict(request, exc):
        return render(request, "error.html", status=409, code=409, message=str(exc) + ". Reload the record and redo your change.")

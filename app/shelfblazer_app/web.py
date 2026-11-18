"""FastAPI application: builds the app (sessions, templates, the shared helpers every page uses) and registers the route modules:
lists (home, search, lists), records, accounts, users_admin, bulk, export, records_import, files, types_admin."""
import json, os, re, secrets
from types import SimpleNamespace
from urllib.parse import urlparse

from anyio import CapacityLimiter, to_thread
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from starlette.middleware.sessions import SessionMiddleware

from shelfblazer import markdown as md, perms
from shelfblazer.auth import Users
from shelfblazer.search import Index
from shelfblazer.store import TYPE_COLORS, Store
from . import __version__, scan as scanning
from .accounts import APPEARANCES, LOOKS
from .cells import make as make_cells
from .middleware import BodyLimit, NeedLogin, add_error_pages, add_security_headers
from .views import hue_of, initials, svg_icon, time_ago  # svg_icon is also imported from here by the tests

HERE = os.path.dirname(os.path.abspath(__file__))
HASHING_THREADS = 4          # scrypt uses 32 MB per call: at most this many at once, off the event loop


def create_app(data_dir, instance_dir, https_only=False):
    os.makedirs(instance_dir, exist_ok=True)
    users = Users(os.path.join(instance_dir, "users.json"))
    store = Store(data_dir, users_lookup=users.resolve)
    store.recover()
    index = Index(store, users.get)
    key_path = os.path.join(instance_dir, "secret.key")
    if not os.path.exists(key_path):
        with open(key_path, "w") as fh:
            fh.write(secrets.token_hex(32))
        os.chmod(key_path, 0o600)
    with open(key_path) as fh:
        secret = fh.read().strip()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SessionMiddleware, secret_key=secret, session_cookie="shelfblazer", same_site="lax",
                       https_only=https_only, max_age=14 * 24 * 3600)
    app.add_middleware(BodyLimit)                          # outermost: runs before anything reads the body
    app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
    templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))
    app.state.store, app.state.users, app.state.index = store, users, index
    hashing = {}

    async def off_loop(fn, *args):
        """Run password hashing in a worker thread (a few at a time), so one login never stalls every other request."""
        if "limiter" not in hashing:
            hashing["limiter"] = CapacityLimiter(HASHING_THREADS)
        return await to_thread.run_sync(fn, *args, limiter=hashing["limiter"])

    def mono_ids(text):
        """Escape a message and show every record ID in it as a code snippet."""
        pattern = re.compile(r"\b([A-Z]{2})-(\d+)\b")
        out = escape(str(text))
        return Markup(pattern.sub(lambda m: f'<code class="rid">{m.group(0)}</code>' if m.group(1) in store.prefix_type else m.group(0), out))
    templates.env.filters["mono_ids"] = mono_ids
    templates.env.globals["app_version"] = __version__
    add_security_headers(app, store, https_only)

    # ------------------------------------------------------------------ helpers
    def user_of(request):
        name = request.session.get("u")
        if not name:
            return None
        u = users.get(name)
        if (not u or not u["active"] or request.session.get("pwv") != users.password_version(u)
                or not users.session_valid(u, request.session.get("sid"))):
            request.session.clear()
            return None
        return u

    def need_user(request):
        u = user_of(request)
        if not u:
            raise NeedLogin()
        return u

    def need_admin(request):
        u = need_user(request)
        if not perms.can_manage_users(u):
            raise HTTPException(403, "administrators only")
        return u

    def csrf_token(request):
        if "csrf" not in request.session:
            request.session["csrf"] = secrets.token_urlsafe(24)
        return request.session["csrf"]

    def check_csrf(request, form):
        if not secrets.compare_digest(str(form.get("csrf_token", "")), request.session.get("csrf", "\0")):
            raise HTTPException(403, "invalid or missing form token; reload the page and retry")

    def flash(request, msg, kind="ok"):
        request.session.setdefault("flash", []).append([kind, msg])

    def display(uid):
        """A person's display name from the user ID stored in records and commits."""
        return store.person_name(uid)

    def render(request, template, status=200, **ctx):
        u = user_of(request)
        flashes = request.session.pop("flash", []) if "flash" in request.session else []
        ok = store.readable_types(u)
        base = dict(user=u, is_admin=perms.is_admin(u), csrf=csrf_token(request), flashes=flashes, schemas=store.schemas,
                    nav=[(s, store.schemas[s]["name"]) for s in ordered_types() if s in ok], display=display,
                    id_hue=lambda rid: type_hue(store.prefix_type.get(str(rid).split("-")[0].upper())) if store.prefix_type.get(str(rid).split("-")[0].upper()) else "",
                    can_edit=lambda r: store.can_edit(u, r), can_create=lambda slug: store.can_create(u, slug),
                    md=lambda text: md.render(text, store.visible_records(u)),
                    appearance=appearance_of(request), look=look_of(request), hue=hue_of, type_hue=type_hue, icon=icon_of, svg=svg_icon, initials=initials, active=active_nav(request),
                    here=request.url.path + (("?" + request.url.query) if request.url.query else ""),
                    can_scan=bool(u) and bool(scanning.types(store, u)))
        return templates.TemplateResponse(request, template, {**base, **ctx}, status_code=status)

    def type_hue(slug):
        """Palette slot 1..8 for a record type: its optional "color" in the type definition, else a stable default."""
        color = store.type_color(slug)
        return TYPE_COLORS.index(color) + 1 if color else hue_of(slug)

    def icon_of(slug, cls=""):
        """The icon of a record type (its "icon" setting; see Store.type_icon)."""
        return svg_icon(store.type_icon(slug), cls)

    def active_nav(request):
        """Which menu entry is the current page: a type slug, 'home', 'users' or 'me'."""
        path = request.url.path
        m = re.match(r"^/t/([^/]+)", path)
        if m:
            return m.group(1)
        m = re.match(r"^/r/([A-Za-z]{2})-\d+", path)
        if m:
            return store.prefix_type.get(m.group(1).upper())
        return ("admin" if path.startswith("/admin") else "me" if path.startswith("/me")
                else "home" if path == "/" else None)

    def ordered_types():
        """The order chosen on the Types admin page; types not placed yet follow (built-in sequence, then alphabetical)."""
        return store.ordered_slugs()

    def look_of(request):
        a = request.cookies.get("look")
        return a if a in LOOKS else "spectrum"

    def appearance_of(request):
        a = request.cookies.get("appearance")
        return a if a in APPEARANCES else "auto"

    def safe_next(url):
        p = urlparse(url or "")
        return url if url and url.startswith("/") and not url.startswith("//") and not p.netloc else "/"

    def edit_refusal(rec):
        mode = perms.setting(store.schemas[rec["type"]], "edit")
        if mode == "admin":
            return f"Only administrators can edit {rec['id']}."
        return f"Only {display(rec['created_by'])} or an administrator can edit {rec['id']}."

    cells = make_cells(store, users, display)
    label_of, computed_cell, present, cell, form_descriptors = cells.label_of, cells.computed_cell, cells.present, cells.cell, cells.form_descriptors
    picture = cells.picture

    add_error_pages(app, render)

    app.state.time_ago = time_ago

    from . import accounts, bulk, export, files as files_routes, lists, records, records_import, scan, types_admin, users_admin
    ctx = SimpleNamespace(store=store, users=users, index=index, render=render, need_user=need_user, need_admin=need_admin, user_of=user_of,
                          check_csrf=check_csrf, flash=flash, display=display, off_loop=off_loop,
                          form_descriptors=form_descriptors, type_hue=type_hue, computed_cell=computed_cell, present=present, cell=cell, label_of=label_of, picture=picture, ordered_types=ordered_types,
                          edit_refusal=edit_refusal, safe_next=safe_next, https_only=https_only)
    lists.register(app, ctx)
    records.register(app, ctx)
    accounts.register(app, ctx)
    users_admin.register(app, ctx)
    bulk.register(app, ctx)
    export.register(app, ctx)
    records_import.register(app, ctx)
    files_routes.register(app, ctx)
    types_admin.register(app, ctx)
    scan.register(app, ctx)
    return app

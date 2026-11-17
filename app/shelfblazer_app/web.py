"""FastAPI application: pages for search, lists, records, forms, accounts and user admin."""
import datetime, json, os, re, secrets, zlib
from types import SimpleNamespace
from urllib.parse import quote, unquote, urlparse

from anyio import CapacityLimiter, to_thread
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from starlette.middleware.sessions import SessionMiddleware

from shelfblazer import markdown as md
from shelfblazer import fields as fieldrules, formula, seqsearch, uploads
from . import __version__
from .files import FILES_PER_REQUEST, MAX_REQUEST_BYTES
from .accounts import APPEARANCES, LOOKS
from .format import short_storage
from shelfblazer import perms
from starlette.datastructures import UploadFile
from shelfblazer.auth import UID_RE, Users
from shelfblazer.search import Index, norm
from shelfblazer.store import TYPE_COLORS, Conflict, Forbidden, NotFound, Store, ValidationError

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE_SIZE = 100
SHOW_ALL_MAX = 5000   # "show all" on a list (the header checkbox then selects every row; bulk edit takes at most this many)
PREFS_COOKIE = "listprefs"   # remembered sort, direction and view per record type (per browser)
# Request bodies: forms that may carry files (only after sign-in) get the upload cap, everything else a small one.
MAX_FORM_BYTES = 4 * 1024 * 1024
UPLOAD_PATHS = re.compile(r"^/(t/[^/]+/new|r/[^/]+/edit|r/[^/]+/files)$")
UID_IN_TEXT = re.compile(r"\b" + UID_RE.pattern.strip("^$") + r"\b")   # user IDs in commit messages ("ST-12: update by U7F3K2QA"), shown as names
HASHING_THREADS = 4          # scrypt uses 32 MB per call: at most this many at once, off the event loop


def hue_of(key):
    """Stable colour slot 1..8 for an option value, a person or a type without a colour setting."""
    return 1 + zlib.crc32(str(key).encode("utf-8")) % 8


def svg_icon(name, cls=""):
    """Flat SVG icon: a Font Awesome Free glyph from the local sprite (static/icons.svg)."""
    return Markup(f'<svg class="ico {escape(cls)}" aria-hidden="true" focusable="false"><use href="/static/icons.svg#{escape(name)}"></use></svg>')


def initials(name):
    words = re.findall(r"\w+", name or "", re.UNICODE)
    if not words:
        return "?"
    return (words[0][0] + words[-1][0]).upper() if len(words) > 1 else words[0][:2].upper()


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

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if not request.url.path.startswith("/static/"):
            store.refresh()                                  # a commit made outside this process (maintenance command, restore): reload first
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; form-action 'self'"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Cache-Control"] = "no-store"
        if https_only:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        return resp

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
                    here=request.url.path + (("?" + request.url.query) if request.url.query else ""))
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

    def read_prefs(request):
        """Remembered list preferences, validated against the current type definitions (a bad or stale cookie is ignored)."""
        try:
            raw = json.loads(unquote(request.cookies.get(PREFS_COOKIE, "")) or "{}")
        except ValueError:
            return {}
        out = {}
        for slug, v in (raw.items() if isinstance(raw, dict) else []):
            if slug in store.schemas and isinstance(v, dict):
                cols = store.schemas[slug]["list_columns"]
                clean = {}
                if v.get("sort") in cols: clean["sort"] = v["sort"]
                if v.get("dir") in ("asc", "desc"): clean["dir"] = v["dir"]
                if v.get("view") in ("table", "cards"): clean["view"] = v["view"]
                out[slug] = clean
        return out

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

    def label_of(slug, key):
        for f in store.schemas[slug]["fields"]:
            if f["key"] == key:
                return f["label"]
        return {"id": "ID", "name": "Name", "created_by": "Owner", "created_at": "Created"}.get(key, key.replace("_", " ").title())

    def computed_cell(rec, fdef, viewer):
        """A computed field for a list or record: dict(text | links, sort, error)."""
        value, err = store.compute(rec, fdef, viewer)
        if isinstance(value, list) and value and all(isinstance(v, str) and v in store.records for v in value):
            return dict(text="", links=[(v, v) for v in value], sort=formula.sort_key(len(value)), error=err)
        return dict(text=formula.format_value(value), sort=formula.sort_key(value), error=err)

    def present(f, v):
        """A stored value of a field (not empty, not a link or calculated field) for a page: dict(text, and chips | url | dois |
        full + outdated). Lists, record pages and old versions all show values through this one function."""
        if f["type"] == "user":
            return dict(text=display(v))
        if fieldrules.is_place(f):
            return dict(text=fieldrules.place_name(store.places, v))
        if fieldrules.is_doi(f) and isinstance(v, str):
            return dict(text=v, dois=[d for d in re.split(r"[\s,]+", v) if d])
        if f["type"] == "url":
            return dict(text=v, url=v)
        mode = fieldrules.display_mode(f)
        if mode and isinstance(v, str):
            shown = short_storage(v) if mode == "short" else v
            return dict(text=shown, full=v if mode == "short" else None, outdated=(mode == "outdated"))
        if f["type"] in ("select", "multiselect"):
            vals = v if isinstance(v, list) else [v]
            return dict(text=", ".join(map(str, vals)), chips=[(x, hue_of(x)) for x in vals])
        return dict(text=fieldrules.as_text(store, f, v))

    def cell(rec, key, viewer=None):
        """One list cell: dict(text, links=[(id, label)], chips, href, avatar, sort, ...)."""
        fdef = next((f for f in store.schemas[rec["type"]]["fields"] if f["key"] == key), None)
        if fdef and fdef["type"] == "computed":
            return computed_cell(rec, fdef, viewer)
        if key == "id":
            return dict(text=rec["id"], href=f"/r/{rec['id']}", sort=rec["legacy_id"])
        if key == "name":
            return dict(text=rec["name"], sort=norm(rec["name"]))
        if key == "created_by":
            n = display(rec["created_by"]); return dict(text=n, sort=norm(n), avatar=initials(n), ahue=hue_of(rec["created_by"]))
        if key == "created_at":
            return dict(text=(rec["created_at"] or "")[:10], sort=rec["created_at"] or "")
        v = rec["fields"].get(key)
        if v in (None, "", []) or fdef is None:
            return dict(text="" if v in (None, "", []) else str(v), sort="")
        if fdef["type"] == "ref":
            return dict(text="", links=[(i, i) for i in v], sort=",".join(v))
        c = present(fdef, v)
        c["sort"] = re.sub(r"\d+", lambda m: m.group().zfill(6), norm(c["text"])) if fieldrules.display_mode(fdef) else norm(c["text"])  # "Box 2" before "Box 10"
        return c

    def form_descriptors(slug, values=None, errors=None, creating=False, place_new=False, keep_legacy=(), typed=None):
        """Fields of the form. A field marked "legacy": true (the free-text `storage`, a label from the old database) is only offered where the record already has a value (keep_legacy: those keys, or True for all, as in bulk edit); chemicals use Storage place."""
        values, errors, out = values or {}, errors or {}, []
        for f in store.schemas[slug]["fields"]:
            k = f["key"]
            if not (fieldrules.is_value(f) or fieldrules.is_file_input(f)) or (f.get("legacy") and keep_legacy is not True and k not in keep_legacy):
                continue
            opts = []
            if f["type"] in ("select", "multiselect"):
                if fieldrules.is_place(f):
                    opts = [(p["id"], p["name"]) for p in store.places_in_use()]
                    if values.get(k) and values[k] not in dict(opts):                               # just created, not yet used by a saved record
                        opts += [(p["id"], p["name"]) for p in store.places if p["id"] == values[k]]
                    if place_new:
                        opts.append(("__new__", "New place…"))
                else:
                    opts = [(o, o) for o in f.get("options", [])]
                    if place_new and f.get("addable") and f["type"] == "select":
                        opts.append(("__new__", "New option…"))
            v = values.get(k, "")
            if f["type"] == "ref" and isinstance(v, list):
                v = ", ".join(v)
            if f["type"] == "user" and v:
                u = users.resolve(v)
                v = u["username"] if u else ""                                     # the form edits usernames; records hold user IDs
            if f["type"] == "file":
                v = [os.path.basename(x) for x in (v or [])]                                 # the files already attached (shown by name)
            out.append(dict(key=k, label=f["label"], type=f["type"], value=v, options=opts, error=errors.get(k),
                            required=f.get("required"), help=f.get("help"), to=f.get("to"), multiple=f.get("multiple", True), fold=bool(f.get("fold")),
                            new_place=bool(place_new and fieldrules.is_place(f)),
                            new_option=bool(place_new and f.get("addable") and not f.get("source")),
                            new_value=(typed or {}).get(k, "")))                                     # a new option or place typed before an error
        return out

    async def read_form(request, slug):
        """The record form: (form, raw field values, files chosen in File fields, new options/places typed: key -> name)."""
        form = await request.form()                                               # size already checked by BodyLimit
        check_csrf(request, form)
        raw, files, additions = {}, {}, {}
        try:
            for f in store.schemas[slug]["fields"]:                                       # files chosen in File fields: read now, attached after the record is saved
                if fieldrules.is_file_input(f):
                    for part in form.getlist(f"f_{f['key']}"):
                        if isinstance(part, UploadFile) and part.filename and sum(map(len, files.values())) < FILES_PER_REQUEST:
                            files.setdefault(f["key"], []).append((part.filename, await part.read(uploads.MAX_UPLOAD_BYTES + 1)))
        finally:
            await form.close()
        for f in store.schemas[slug]["fields"]:
            k, ty = f["key"], f["type"]
            if not fieldrules.is_value(f):
                continue
            fk = f"f_{k}"
            if ty == "bool":
                raw[k] = form.get(fk) == "on"
            elif ty == "multiselect":
                raw[k] = form.getlist(fk)
            else:
                if f.get("legacy") and fk not in form:
                    continue                                                           # not offered on this form: keep the stored value
                raw[k] = form.get(fk, "")
            if str(form.get(fk + "_new", "")).strip():
                additions[k] = str(form.get(fk + "_new"))                             # a new option or place: saved with the record, if it is valid
        return form, raw, files, additions

    def attach_files(request, rid, user, files):
        """Attach the files chosen in the form to the saved record (one commit each); problems are reported, the record stays saved."""
        for key, items in files.items():
            for filename, content in items:
                try:
                    store.add_attachment(rid, user, filename, content, field=key)
                    flash(request, f"Added {uploads.safe_name(filename)} to {rid}.")
                except (ValidationError, Forbidden) as e:
                    flash(request, f"Not added: {e.errors.get('file', str(e)) if isinstance(e, ValidationError) else 'you may not change this record'}", "err")

    # ------------------------------------------------------------------ error handling
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

    # ------------------------------------------------------------------ search and lists
    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request, q: str = "", archived: str = "", fuzzy: str = ""):
        u = need_user(request)
        q = q.strip()
        if q:
            exact = store.readable(u, store.canonical_id(q.upper().replace(" ", "")))
            if exact and not request.headers.get("x-live"):          # Enter jumps to the record; the live search shows it as the first result instead
                return RedirectResponse(f"/r/{exact['id']}", 303)
            results = index.search(q, include_archived=bool(archived), types=store.readable_types(u), fuzzy=fuzzy != "0", archived_owner=owner_scope(u))
            groups = {}
            for d in results:
                groups.setdefault(d["type"], []).append(store.records[d["id"]])
            return render(request, "home.html", q=q, archived=bool(archived), fuzzy=fuzzy != "0", total=len(results),
                          groups=[(s, store.schemas[s]["name"], recs) for s, recs in ((s, groups[s]) for s in store.schemas if s in groups)])
        counts = []
        readable = store.readable_types(u)
        types = [t for t in ordered_types() if t in readable]
        mine = store.visible_records(u).values()                                     # one pass for everything below
        by_type = {}
        for r in mine:
            by_type.setdefault(r["type"], []).append(r)
        for s in types:
            recs = by_type.get(s, [])
            counts.append((s, store.schemas[s]["name"], sum(not r["archived"] for r in recs), sum(r["archived"] for r in recs)))
        example = "ST-1"
        if types:
            nums = [r["legacy_id"] for r in by_type.get(types[0], [])]
            example = f"{store.schemas[types[0]]['prefix']}-{max(nums) if nums else 1}"
        activity = [dict(a, ago=time_ago(a["when"]), when_text=a["when"][:16].replace("T", " "), hue=type_hue(a["type"]), who_name=display(a["who"]) if a["who"] else "")
                    for a in store.recent_activity(u, 8)]
        return render(request, "home.html", q="", fuzzy=fuzzy != "0", counts=counts, n_types=len(types), n_records=sum(c[2] for c in counts), example=example, activity=activity)

    def time_ago(iso):
        """'just now', '12 min ago', '1 h ago', 'yesterday', '3 days ago', then the date."""
        try:
            then = datetime.datetime.fromisoformat(iso)
        except ValueError:
            return iso[:10]
        secs = (datetime.datetime.now(then.tzinfo) - then).total_seconds()
        if secs < 60: return "just now"
        if secs < 3600: return f"{int(secs // 60)} min ago"
        if secs < 86400: return f"{int(secs // 3600)} h ago"
        days = (datetime.datetime.now(then.tzinfo).date() - then.date()).days
        return "yesterday" if days == 1 else f"{days} days ago" if days < 7 else then.strftime("%d %b %Y")
    app.state.time_ago = time_ago

    def owner_scope(u):
        """What to pass to the search index as ``archived_owner``: administrators see every archived record, everybody else their own."""
        return None if perms.is_admin(u) else u["uid"]

    def seq_field(slug):
        """The field the type's sequence search looks in (the "sequence_search" setting of the type), or None."""
        key = store.schemas[slug].get("sequence_search")
        return key if any(f["key"] == key for f in store.schemas[slug]["fields"]) else None

    def list_records(slug, q="", owner="", archived=False, fuzzy=True, viewer=None, seq=False, mm=0):
        """The records a list page shows for these filters (before sorting and paging), and the owners among them.
        Archived records are hidden unless asked for, and then only the viewer's own (all of them for administrators)."""
        recs = [r for r in store.records.values() if r["type"] == slug]
        if q.strip() and seq and seq_field(slug):                        # sequence search: the query is a sequence, not words
            recs = [r for r, _ in seqsearch.find(recs, seq_field(slug), q, mm)]
        elif q.strip():
            ids = [d["id"] for d in index.search(q, include_archived=bool(archived), type_slug=slug, fuzzy=fuzzy, archived_owner=owner_scope(viewer))]
            recs = [store.records[i] for i in ids]
        if not archived:
            recs = [r for r in recs if not r["archived"]]
        else:
            recs = [r for r in recs if not r["archived"] or perms.can_see_archived(viewer, r)]
        owners = sorted({r["created_by"] for r in recs}, key=display)
        if owner:
            recs = [r for r in recs if r["created_by"] == owner]
        return recs, owners

    @app.get("/t/{slug}", response_class=HTMLResponse)
    async def list_type(request: Request, slug: str, q: str = "", sort: str = "", dir: str = "", owner: str = "",
                        archived: str = "", page: int = 1, view: str = "", fuzzy: str = "", seq: str = "", mm: int = 0, show: str = ""):
        u = need_user(request)
        if slug not in store.schemas or slug not in store.readable_types(u):
            raise HTTPException(404, "unknown type")
        schema = store.schemas[slug]
        hidden = {f["key"] for f in schema["fields"] if f.get("hidden")}
        cols = [c for c in schema["list_columns"] if c not in hidden]
        # sort, direction and view: what the link asks for, else what this browser used last time, else the defaults
        prefs = read_prefs(request)
        mine = prefs.get(slug, {})
        asked = {k: v for k, v in (("sort", sort), ("dir", dir), ("view", view))
                 if v and v in {"sort": cols, "dir": ("asc", "desc"), "view": ("table", "cards")}[k]}
        eff = {"sort": "id", "dir": "asc", "view": "table", **mine, **asked}
        sort, dir, view = eff["sort"], eff["dir"], eff["view"]
        remember = bool(asked) and eff != {**{"sort": "id", "dir": "asc", "view": "table"}, **mine}
        seq_on = bool(seq) and bool(seq_field(slug))
        mm = max(0, min(mm, seqsearch.MAX_MISMATCHES))
        recs, owners = list_records(slug, q, owner, archived, fuzzy != "0", u, seq_on, mm)
        keyed = [(cell(r, sort, u)["sort"], r) for r in recs]
        if not q.strip():
            keyed.sort(key=lambda x: (x[0] in ("", None), x[0]), reverse=False)
            if dir == "desc":
                keyed.reverse()
        total = len(keyed)
        show_all = show == "all" and total <= SHOW_ALL_MAX
        page = 1 if show_all else max(1, page)
        shown = keyed if show_all else keyed[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
        rows = [[cell(r, c, u) for c in cols] for _, r in shown]
        ids = [r["id"] for _, r in shown]
        resp = render(request, "list.html", slug=slug, schema=schema, cols=[(c, label_of(slug, c)) for c in cols], rows=list(zip(ids, rows)), gone={i for i in ids if store.records[i]["archived"]},
                      q=q, fuzzy=fuzzy != "0", sort=sort, dir=dir, owner=owner, owners=owners, archived=bool(archived), page=page, view=view,
                      seq_ok=bool(seq_field(slug)), seq=seq_on, mm=mm, seq_short=seq_on and bool(q.strip()) and not seqsearch.usable(q),
                      pages=(total + PAGE_SIZE - 1) // PAGE_SIZE, total=total, show_all=show_all, can_show_all=total <= SHOW_ALL_MAX)
        if remember:
            prefs[slug] = eff
            resp.set_cookie(PREFS_COOKIE, quote(json.dumps(prefs, separators=(",", ":")), safe=""), max_age=365 * 24 * 3600,
                            samesite="lax", httponly=True, secure=https_only)
        return resp

    def attachment_info(rec, key):
        """[(path under data/files, file name, human size or None when the file is missing)] for the record page."""
        out = []
        for rel in rec["fields"].get(key, []):
            full = os.path.join(store.repo, "data", "files", rel)
            out.append((rel, os.path.basename(rel), uploads.human_size(os.path.getsize(full)) if os.path.isfile(full) else None))
        return out

    # ------------------------------------------------------------------ records
    def record_rows(rec, schema, u, seen, calculated=True):
        """The label/value rows of a record page, read with the given type definition (an old version of a record uses the one from
        its commit). Sequences are not rows: see record_blocks."""
        rows = []
        readable_now = store.readable_types(u)
        for f in schema["fields"]:
            k = f["key"]
            if not fieldrules.is_shown(f) or f["type"] == "file" or fieldrules.is_sequence(f):
                continue
            if f["type"] == "computed":
                if not calculated:
                    continue                                    # depends on today's date and on other records: not part of an old version
                c = computed_cell(rec, f, u)
                if c.get("links"):
                    rows.append((f["label"], dict(links=[(i, seen[i]["name"]) for i, _ in c["links"] if i in seen], computed=True)))
                else:
                    rows.append((f["label"], dict(text=c["text"], computed=True, error=c["error"])))
                continue
            v = rec["fields"].get(k)
            if v in (None, "", []) and not (f["type"] == "bool" and k in rec["fields"]):
                continue
            if f["type"] == "ref":
                gone = {i for i in v if i not in seen and i in store.records and store.records[i]["type"] in readable_now}      # archived by someone else: the ID stays, the record does not open
                rows.append((f["label"], dict(links=[(i, seen[i]["name"] if i in seen else "") for i in v if i in seen or i not in store.records or i in gone], gone=gone)))
            else:
                rows.append((f["label"], present(f, v)))
        return rows

    def record_blocks(rec, schema):
        """Sequences, shown under the table in their own folded block: [(label, text)]."""
        return [(f["label"], rec["fields"][f["key"]]) for f in schema["fields"]
                if fieldrules.is_shown(f) and fieldrules.is_sequence(f) and rec["fields"].get(f["key"])]

    @app.get("/r/{rid}", response_class=HTMLResponse)
    async def record(request: Request, rid: str):
        u = need_user(request)
        if store.canonical_id(rid) != rid:                           # an ID with a former prefix of its type (ST-12 after ST became BS)
            return RedirectResponse(f"/r/{store.canonical_id(rid)}", 302)
        rec = store.readable(u, rid)
        if not rec:
            raise NotFound(rid)
        slug = rec["type"]
        seen = store.visible_records(u)
        rows = record_rows(rec, store.schemas[slug], u, seen)
        refs = {}
        for label, r in store.referenced_by(rid):
            if r["id"] not in seen:
                continue
            refs.setdefault((r["type"], label), []).append(r)
        return render(request, "record.html", rec=rec, schema=store.schemas[slug], rows=rows, blocks_=record_blocks(rec, store.schemas[slug]), refs=refs,
                      edit_mode=perms.setting(store.schemas[slug], "edit"),
                      history=[dict(h, author=display(h["author"]), subject=UID_IN_TEXT.sub(lambda m: display(m.group(0)), h["subject"])) for h in store.history(rid)],
                      file_sections=[(f["key"], f["label"], attachment_info(rec, f["key"]), "files-" + f["key"])
                                     for f in store.schemas[slug]["fields"] if fieldrules.is_file_input(f)])

    @app.get("/r/{rid}/at/{sha}", response_class=HTMLResponse)
    async def record_version(request: Request, rid: str, sha: str):
        """A record as one of its commits saved it: read-only, with the type definition from that commit, for people who may read the record today."""
        u = need_user(request)
        cur = store.readable(u, rid)
        if not cur:
            raise NotFound(rid)
        v = store.record_at(rid, sha)
        if v["current"]:                                                      # the last change of this record is the record as it is now
            return RedirectResponse(f"/r/{rid}", 303)
        rec, schema = v["rec"], v["schema"]
        rows = record_rows(rec, schema, u, store.visible_records(u), calculated=False)
        files = [(f["label"], [os.path.basename(x) for x in rec["fields"].get(f["key"], [])]) for f in schema["fields"]
                 if fieldrules.is_file_input(f) and rec["fields"].get(f["key"])]
        return render(request, "version.html", rec=rec, cur=cur, schema=schema, rows=rows, blocks_=record_blocks(rec, schema), files=files,
                      sha=v["sha"], when=v["when"][:16].replace("T", " "),
                      author=display(v["author"]), subject=UID_IN_TEXT.sub(lambda m: display(m.group(0)), v["subject"]))

    @app.get("/t/{slug}/new", response_class=HTMLResponse)
    async def new_form(request: Request, slug: str):
        u = need_user(request)
        if slug not in store.schemas or slug not in store.readable_types(u):
            raise HTTPException(404, "unknown type")
        if not store.can_create(u, slug):
            raise Forbidden(f"Only administrators can add {store.schemas[slug]['name'].lower()} records.")
        return render(request, "form.html", slug=slug, schema=store.schemas[slug], mode="new", name="", notes="",
                      fields=form_descriptors(slug, creating=True, place_new=True), version="")

    @app.post("/t/{slug}/new")
    async def new_submit(request: Request, slug: str):
        u = need_user(request)
        if slug not in store.schemas or slug not in store.readable_types(u):
            raise HTTPException(404, "unknown type")
        if not store.can_create(u, slug):                                    # before reading the form: it may add options and places
            raise Forbidden(f"Only administrators can add {store.schemas[slug]['name'].lower()} records.")
        form, raw, files, additions = await read_form(request, slug)
        try:
            rid = store.create(slug, u, form.get("name", ""), raw, form.get("notes", ""), additions=additions)
        except ValidationError as e:
            return render(request, "form.html", status=422, slug=slug, schema=store.schemas[slug], mode="new", name=form.get("name", ""),
                          notes=form.get("notes", ""), fields=form_descriptors(slug, raw, e.errors, creating=True, place_new=True, typed=additions),
                          version="", errors=e.errors)
        flash(request, f"Created {rid}. Write this ID on the label.")
        attach_files(request, rid, u, files)
        return RedirectResponse(f"/t/{slug}/new" if form.get("action") == "another" else f"/r/{rid}", 303)

    def owner_choices(u):
        """(user ID, name, username) of everyone an administrator may hand a record to; empty for other people (the form shows no owner box)."""
        return [(p["uid"], p["name"], p["username"]) for p in users.all()] if perms.is_admin(u) else []

    @app.get("/r/{rid}/edit", response_class=HTMLResponse)
    async def edit_form(request: Request, rid: str):
        u = need_user(request)
        rec = store.readable(u, rid)
        if not rec:
            raise NotFound(rid)
        if not store.can_edit(u, rec):
            raise Forbidden(edit_refusal(rec))
        return render(request, "form.html", slug=rec["type"], schema=store.schemas[rec["type"]], mode="edit", rec=rec, name=rec["name"],
                      notes=rec["notes"], fields=form_descriptors(rec["type"], rec["fields"], place_new=True, keep_legacy={k for k, v in rec["fields"].items() if v}), version=rec["_version"],
                      people=owner_choices(u), owner=rec["created_by"])

    @app.post("/r/{rid}/edit")
    async def edit_submit(request: Request, rid: str):
        u = need_user(request)
        rec = store.readable(u, rid)
        if not rec:
            raise NotFound(rid)
        if not store.can_edit(u, rec):                                       # before reading the form: it may add options and places
            raise Forbidden(edit_refusal(rec))
        form, raw, files, additions = await read_form(request, rec["type"])
        try:
            store.update(rid, u, form.get("name", ""), raw, form.get("notes", ""), form.get("version", ""),
                         owner=form.get("owner", "") if perms.is_admin(u) else None, additions=additions)
        except ValidationError as e:
            return render(request, "form.html", status=422, slug=rec["type"], schema=store.schemas[rec["type"]], mode="edit", rec=rec,
                          name=form.get("name", ""), notes=form.get("notes", ""), fields=form_descriptors(rec["type"], {**rec["fields"], **raw}, e.errors, place_new=True, keep_legacy={k for k, v in rec["fields"].items() if v}, typed=additions),
                          version=form.get("version", ""), errors=e.errors, people=owner_choices(u), owner=form.get("owner", rec["created_by"]))
        flash(request, f"Saved {rid}.")
        attach_files(request, rid, u, files)
        return RedirectResponse(f"/r/{rid}", 303)

    @app.post("/r/{rid}/archive")
    async def archive(request: Request, rid: str):
        u = need_user(request)
        form = await request.form()
        check_csrf(request, form)
        rec = store.readable(u, rid)
        if not rec:
            raise NotFound(rid)
        store.set_archived(rid, u, not rec["archived"])
        flash(request, f"{rid} {'restored' if rec['archived'] else 'archived'}.")
        now = store.readable(u, rid)                                          # archiving someone else's record (open types): it is gone for you
        return RedirectResponse(f"/r/{rid}" if now else f"/t/{rec['type']}", 303)

    @app.get("/files/{path:path}")
    async def files(request: Request, path: str):
        u = need_user(request)
        base = os.path.realpath(os.path.join(store.repo, "data", "files"))
        full = os.path.realpath(os.path.join(base, path))
        if not full.startswith(base + os.sep) or not os.path.isfile(full):
            raise HTTPException(404, "file not found")
        owner_dir = os.path.relpath(full, base).split(os.sep)[0]                 # files live in data/files/<record ID>/
        rec = store.records.get(owner_dir)
        if rec is not None:
            if not store.readable(u, owner_dir):                                  # hidden type, or archived by someone else
                raise HTTPException(404, "file not found")
        else:                                                                     # a purged record's folder still has a type by its prefix
            kind = store.prefix_type.get(owner_dir.split("-")[0].upper())
            if not perms.is_admin(u) and (kind is None or kind not in store.readable_types(u)):
                raise HTTPException(404, "file not found")
        return FileResponse(full, filename=os.path.basename(full))

    # ------------------------------------------------------------------ picker support
    @app.get("/api/records")
    async def api_records(request: Request, q: str = "", type: str = "", ids: str = "", limit: int = 8, archived: str = ""):
        """Small JSON lookup for the reference picker: by text (fuzzy), by IDs, or the newest records of a type."""
        if not user_of(request):
            return JSONResponse({"error": "login required"}, status_code=401)
        limit = max(1, min(limit, 20))
        viewer = user_of(request)
        ok = store.readable_types(viewer)
        types = None
        if type:
            types = {t for t in type.split(",") if t in ok}
            if not types:
                return {"results": []}
        elif ok != set(store.schemas):
            types = ok

        def item(r):
            return {"id": r["id"], "name": r["name"], "type": r["type"], "type_name": store.schemas[r["type"]]["name"],
                    "hue": type_hue(r["type"]), "archived": r["archived"]}
        if ids:
            found = [store.readable(viewer, i.strip()) for i in ids.split(",")[:50]]
            out = [item(r) for r in found if r and (types is None or r["type"] in types)]
        elif q.strip():
            out = [item(store.records[d["id"]]) for d in index.search(q, include_archived=bool(archived), types=types, archived_owner=owner_scope(viewer))[:limit]]
        else:   # nothing typed yet: the newest records, so the list is useful on focus
            recs = [r for r in store.records.values() if (types is None or r["type"] in types) and (not r["archived"] or (archived and perms.can_see_archived(viewer, r)))]
            recs.sort(key=lambda r: (r["created_at"] or "", r["legacy_id"]), reverse=True)
            out = [item(r) for r in recs[:limit]]
        return {"results": out}

    from . import accounts, bulk, export, files as files_routes, types_admin, users_admin
    ctx = SimpleNamespace(store=store, users=users, index=index, render=render, need_user=need_user, need_admin=need_admin, user_of=user_of,
                          check_csrf=check_csrf, flash=flash, display=display, list_records=list_records, off_loop=off_loop,
                          form_descriptors=form_descriptors, type_hue=type_hue, safe_next=safe_next, https_only=https_only)
    accounts.register(app, ctx)
    users_admin.register(app, ctx)
    bulk.register(app, ctx)
    export.register(app, ctx)
    files_routes.register(app, ctx)
    types_admin.register(app, ctx)
    return app

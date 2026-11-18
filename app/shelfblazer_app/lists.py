"""The home page (counts, search, recent activity), the list pages, and the JSON lookup behind the reference picker."""
import json
from urllib.parse import quote, unquote

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from shelfblazer import perms, seqsearch
from . import scan
from .views import day_label, time_ago

ACTIVITY_EVENTS = 7   # events in the Home timeline (a count, not a period)
VERBS = {"create": "created", "update": "edited", "archive": "archived", "restore": "restored", "delete": "deleted", "bulk": "bulk edit"}
PAGE_SIZE = 100
SHOW_ALL_MAX = 5000   # "show all" on a list (the header checkbox then selects every row; bulk edit takes at most this many)
PREFS_COOKIE = "listprefs"   # remembered sort, direction and view per record type (per browser)


def register(app, c):
    store, index, https_only = c.store, c.index, c.https_only
    need_user, user_of, render, display, type_hue = c.need_user, c.user_of, c.render, c.display, c.type_hue
    cell, label_of, ordered_types, picture = c.cell, c.label_of, c.ordered_types, c.picture

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

    # ------------------------------------------------------------------ search and lists
    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request, q: str = "", archived: str = "", fuzzy: str = ""):
        u = need_user(request)
        q = q.strip()
        if q:
            exact = store.readable(u, store.canonical_id(q.upper().replace(" ", "")))
            if exact and not request.headers.get("x-live"):          # Enter jumps to the record; the live search shows it as the first result instead
                return RedirectResponse(f"/r/{exact['id']}", 303)
            if not request.headers.get("x-live"):                    # a barcode reader types the code and presses Enter: one record carrying it opens
                carriers = scan.active(store, u, [q])
                if len(carriers) == 1:
                    return RedirectResponse(f"/r/{carriers[0]['id']}", 303)
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
        activity = [dict(a, ago=time_ago(a["when"]), when_text=a["when"][:16].replace("T", " "), hue=type_hue(a["type"]), who_name=display(a["who"]) if a["who"] else "",
                         verb=VERBS.get(a["action"], a["action"]))
                    for a in store.recent_activity(u, ACTIVITY_EVENTS)]
        days = []                                                                     # the timeline: events grouped by day, newest first
        for a in activity:
            key, label = day_label(a["when"])
            if not days or days[-1]["key"] != key:
                days.append(dict(key=key, label=label, today=label == "Today", events=[]))
            days[-1]["events"].append(a)
        return render(request, "home.html", q="", fuzzy=fuzzy != "0", counts=counts, n_types=len(types), n_records=sum(c[2] for c in counts), example=example,
                      activity=activity, days=days, n_events=len(activity))

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
        resp = render(request, "list.html", slug=slug, schema=schema, cols=[(c, label_of(slug, c)) for c in cols], rows=list(zip(ids, rows)), pics={i: picture(store.records[i]) for i in ids} if view == "cards" else {}, gone={i for i in ids if store.records[i]["archived"]},
                      q=q, fuzzy=fuzzy != "0", sort=sort, dir=dir, owner=owner, owners=owners, archived=bool(archived), page=page, view=view,
                      seq_ok=bool(seq_field(slug)), seq=seq_on, mm=mm, seq_short=seq_on and bool(q.strip()) and not seqsearch.usable(q),
                      pages=(total + PAGE_SIZE - 1) // PAGE_SIZE, total=total, show_all=show_all, can_show_all=total <= SHOW_ALL_MAX)
        if remember:
            prefs[slug] = eff
            resp.set_cookie(PREFS_COOKIE, quote(json.dumps(prefs, separators=(",", ":")), safe=""), max_age=365 * 24 * 3600,
                            samesite="lax", httponly=True, secure=https_only)
        return resp

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

    c.list_records = list_records                       # bulk edit selects "all matching" with the same filters

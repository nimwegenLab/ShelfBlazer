"""The home page (counts, search, recent activity), the list pages, and the JSON lookup behind the reference picker."""
import json
from urllib.parse import quote, unquote

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from shelfblazer import ids as record_ids, perms, seqsearch
from shelfblazer.auth import Users
from shelfblazer.query import Query, in_collection, select, sequence_field
from . import scan
from .views import day_label, time_ago

ACTIVITY_EVENTS = 7   # events in the Home timeline (a count, not a period)
VERBS = {"create": "created", "update": "edited", "archive": "archived", "restore": "restored", "delete": "deleted", "bulk": "bulk edit"}
PAGE_SIZE = 100
SHOW_ALL_MAX = 5000   # "show all" on a list (the header checkbox then selects every row; bulk edit takes at most this many)
PREFS_COOKIE = "listprefs"   # remembered sort, direction and view per record type (per browser)


def register(app, c):
    store, index, users, https_only = c.store, c.index, c.users, c.https_only
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
            exact = store.readable(u, store.resolve(q.replace(" ", ""), u["uid"]))
            if exact and not request.headers.get("x-live"):          # Enter jumps to the record; the live search shows it as the first result instead
                return RedirectResponse(f"/r/{exact['id']}", 303)
            if not request.headers.get("x-live"):                    # a barcode reader types the code and presses Enter: one record carrying it opens
                carriers = scan.active(store, u, [q])
                if len(carriers) == 1:
                    return RedirectResponse(f"/r/{carriers[0]['id']}", 303)
            results, _ = select(store, index, u, Query(text=q, archived=bool(archived), fuzzy=fuzzy != "0", collection="any"))
            groups = {}                                                                      # one table per type and collection
            for r in results:
                key = (r["type"], "lab" if not record_ids.is_personal(r["id"]) else "mine" if r["created_by"] == u["uid"] else r["created_by"])
                groups.setdefault(key, []).append(r)
            order = sorted(groups, key=lambda k: (list(store.schemas).index(k[0]), k[1] != "lab", k[1] != "mine", display(k[1]).casefold() if k[1] not in ("lab", "mine") else ""))
            return render(request, "home.html", q=q, archived=bool(archived), fuzzy=fuzzy != "0", total=len(results),
                          groups=[(s, store.schemas[s]["name"] + "s" if coll == "lab" else collection_title(store.schemas[s], u["uid"] if coll == "mine" else coll, coll == "mine"),
                                   groups[(s, coll)], "" if coll == "lab" else coll) for s, coll in order])
        counts = []
        readable = store.readable_types(u)
        types = [t for t in ordered_types() if t in readable]
        mine = store.visible_records(u).values()                                     # one pass for everything below
        by_type = {}
        own = {}
        for r in mine:
            if record_ids.is_personal(r["id"]):
                if r["created_by"] == u["uid"]:
                    own.setdefault(r["type"], []).append(r)
                continue
            by_type.setdefault(r["type"], []).append(r)
        for s in types:
            recs = by_type.get(s, [])
            counts.append((s, store.schemas[s]["name"], sum(not r["archived"] for r in recs), sum(r["archived"] for r in recs)))
        mine_counts = [(s, n, sum(not r["archived"] for r in own.get(s, [])), sum(r["archived"] for r in own.get(s, [])))
                       for s, n in c.my_types(u)]
        example = "ST-1"
        if types:
            first = [r for r in by_type.get(types[0], []) if not record_ids.is_personal(r["id"])]
            example = max(first, key=record_ids.sort_key)["id"] if first else record_ids.make(store.schemas[types[0]]["prefix"], 1)
        activity = [dict(a, ago=time_ago(a["when"]), when_text=a["when"][:16].replace("T", " "), hue=type_hue(a["type"]), who_name=display(a["who"]) if a["who"] else "",
                         verb=VERBS.get(a["action"], a["action"]))
                    for a in store.recent_activity(u, ACTIVITY_EVENTS)]
        days = []                                                                     # the timeline: events grouped by day, newest first
        for a in activity:
            key, label = day_label(a["when"])
            if not days or days[-1]["key"] != key:
                days.append(dict(key=key, label=label, today=label == "Today", events=[]))
            days[-1]["events"].append(a)
        return render(request, "home.html", q="", fuzzy=fuzzy != "0", counts=counts, mine_counts=mine_counts, n_types=len(types), n_records=sum(c[2] for c in counts), example=example,
                      activity=activity, days=days, n_events=len(activity))

    def seq_field(slug):
        return sequence_field(store, slug)

    def list_records(slug, q="", owner="", archived=False, fuzzy=True, viewer=None, seq=False, mm=0, collection="lab"):
        """The records a list page shows for these filters (before sorting and paging), and the owners among them (by name)."""
        recs, owners = select(store, index, viewer, Query(type=slug, text=q, fuzzy=fuzzy, sequence=seq, mismatches=mm, owner=owner,
                                                          archived=bool(archived), collection=collection))
        return recs, sorted(owners, key=display)

    def first_name(uid):
        return (display(uid).split() or ["?"])[0]

    def collection_title(schema, who, mine):
        """"Strains" for the lab collection, "My strains", "<Name>'s strains" for a person's."""
        plural = f"{schema['name'].lower()}s"
        return f"My {plural}" if mine else f"{first_name(who)}'s {plural}"

    def others_collections(slug, viewer):
        """[(user ID, number of active records)] for the other people's personal collections of this type that the viewer can see."""
        recs, _ = select(store, index, viewer, Query(type=slug, collection="personal"))
        counts = {}
        for r in recs:
            if r["created_by"] != viewer["uid"]:
                counts[r["created_by"]] = counts.get(r["created_by"], 0) + 1
        return sorted(counts.items(), key=lambda kv: display(kv[0]).casefold())

    def personal_hits(slug, q, viewer):
        """[(user ID or "mine", title, count)]: where else this list's search finds something, in personal collections."""
        recs, _ = select(store, index, viewer, Query(type=slug, text=q, collection="personal"))
        counts = {}
        for r in recs:
            counts[r["created_by"]] = counts.get(r["created_by"], 0) + 1
        mine = counts.pop(viewer["uid"], 0)
        out = [("mine", "yours", mine)] if mine else []
        return out + [(uid, f"{first_name(uid)}'s", n) for uid, n in sorted(counts.items(), key=lambda kv: display(kv[0]).casefold())]

    @app.get("/t/{slug}", response_class=HTMLResponse)
    async def list_type(request: Request, slug: str, q: str = "", sort: str = "", dir: str = "", owner: str = "",
                        archived: str = "", page: int = 1, view: str = "", fuzzy: str = "", seq: str = "", mm: int = 0, show: str = "",
                        c: str = ""):
        u = need_user(request)
        if slug not in store.schemas or slug not in store.readable_types(u):
            raise HTTPException(404, "unknown type")
        schema = store.schemas[slug]
        coll = "lab" if c in ("", "lab") else "mine" if c == "mine" else c                     # "lab", "mine" or the user ID of someone else
        if coll not in ("lab", "mine") and not users.by_uid(coll):
            raise HTTPException(404, "unknown collection")
        if coll != "lab":
            owner = u["uid"] if coll == "mine" else coll                                       # a personal list is one person's
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
        recs, owners = list_records(slug, q, owner, archived, fuzzy != "0", u, seq_on, mm, "lab" if coll == "lab" else "personal")
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
                      pages=(total + PAGE_SIZE - 1) // PAGE_SIZE, total=total, show_all=show_all, can_show_all=total <= SHOW_ALL_MAX,
                      coll=coll, title=schema["name"] + "s" if coll == "lab" else collection_title(schema, owner, coll == "mine"),
                      can_add=perms.can_create_personal(u, schema) if coll == "mine" else store.can_create(u, slug) if coll == "lab" else False,
                      shared=Users.collection_visible(u, slug), others=others_collections(slug, u) if coll == "mine" else [],
                      elsewhere=personal_hits(slug, q, u) if coll == "lab" and q.strip() and not seq_on else [])
        if remember:
            prefs[slug] = eff
            resp.set_cookie(PREFS_COOKIE, quote(json.dumps(prefs, separators=(",", ":")), safe=""), max_age=365 * 24 * 3600,
                            samesite="lax", httponly=True, secure=https_only)
        return resp

    # ------------------------------------------------------------------ picker support
    @app.get("/api/records")
    async def api_records(request: Request, q: str = "", type: str = "", ids: str = "", limit: int = 8, archived: str = "", collection: str = "lab", owner: str = ""):
        """Small JSON lookup for the reference picker: by text (fuzzy), by IDs, or the newest records of a type."""
        if not user_of(request):
            return JSONResponse({"error": "login required"}, status_code=401)
        limit = max(1, min(limit, 20))
        viewer = user_of(request)
        ok = store.readable_types(viewer)
        types = None
        if type:
            types = frozenset(t for t in type.split(",") if t in ok)
            if not types:
                return {"results": []}
        elif ok != set(store.schemas):
            types = frozenset(ok)

        def item(r):
            return {"id": r["id"], "label": record_ids.label(r["id"], viewer["uid"], display), "name": r["name"], "type": r["type"],
                    "type_name": store.schemas[r["type"]]["name"], "hue": type_hue(r["type"]), "archived": r["archived"]}
        if ids:
            found = [store.readable(viewer, store.resolve(i.strip(), owner or viewer["uid"])) for i in ids.split(",")[:50]]
            out = [item(r) for r in found if r and (types is None or r["type"] in types) and in_collection(viewer, r, collection)]
        elif q.strip():
            out = [item(r) for r in select(store, index, viewer, Query(types=types, text=q, archived=bool(archived), collection=collection))[0][:limit]]
        else:   # nothing typed yet: the newest records, so the list is useful on focus
            recs, _ = select(store, index, viewer, Query(types=types, archived=bool(archived), collection=collection))
            recs.sort(key=lambda r: (r["created_at"] or "", record_ids.sort_key(r)), reverse=True)
            out = [item(r) for r in recs[:limit]]
        return {"results": out}


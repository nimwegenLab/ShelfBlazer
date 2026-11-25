"""Moving personal records to the lab collection (design section 17.5).

``/move?ids=…`` shows what moves (the new lab IDs, the linked personal records that move along, what stops the move) and what moving
means, with the button that does it: one page, one click. Only when a linked personal record has a lab record that looks the same does
a first page ask what to do about it (move it too, or link to the lab record instead); ``/move/confirm`` is the page after that.
``POST /move`` does the move in one commit (shelfblazer/personal.py). One record and a bulk selection use the same pages.
"""
from urllib.parse import urlencode

from fastapi import HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.requests import Request

from shelfblazer import personal
from shelfblazer.errors import Conflict, NotFound, ValidationError

MAX_IDS = 200


def register(app, c):
    store = c.store

    def parse_relink(values, user):
        """"PL-P3:PL-142" pairs from the form: the linked personal record, and the lab record to link to instead."""
        out = {}
        for v in values[:MAX_IDS]:
            old, _, new = str(v).partition(":")
            if old.strip() and new.strip():
                out[store.resolve(old.strip(), user["uid"])] = store.resolve(new.strip(), user["uid"])
        return out

    def clean_ids(values, user):
        """The IDs to move; more than MAX_IDS stops at once rather than moving only some (the IDs travel in the address of the next page)."""
        out = list(dict.fromkeys(store.resolve(str(v).strip(), user["uid"]) for v in values if str(v).strip()))
        if len(out) > MAX_IDS:
            raise HTTPException(400, f"{len(out)} records are selected; at most {MAX_IDS} can be moved at a time. Select fewer and try again.")
        return out

    def what(plan):
        """ "1 strain and 2 plasmids" """
        parts = [f"{n} {name.lower()}{'' if n == 1 else 's'}" for name, n in plan["counts"]]
        return " and ".join([", ".join(parts[:-1]), parts[-1]] if len(parts) > 1 else parts)

    def page(request, ids, relink, decided):
        """The move page for these IDs: the question about linked records first when there is one (and ``decided`` is false), else the page with the button."""
        user = c.need_user(request)
        if not ids:
            raise HTTPException(404, "nothing to move")
        plan = personal.plan_move(store, user, ids, relink)
        relink = {i["rec"]["id"]: i["relink"] for i in plan["items"] if i["relink"]}          # only the choices that were offered
        asks = any(i["lookalikes"] and not i["asked"] for i in plan["items"])                  # a linked record that has a look-alike in the lab
        query = urlencode([("ids", i) for i in ids] + [("relink", f"{o}:{n}") for o, n in relink.items()])
        return c.render(request, "move.html", choose=asks and not decided and not plan["problems"], asks=asks, plan=plan, ids=ids, relink=relink,
                        query=query, what=what(plan), total=len(plan["moving"]),
                        back=f"/r/{ids[0]}" if len(ids) == 1 and store.readable(user, ids[0]) else "/")

    def choices(request, relink, user):
        """The radio buttons of the first page ("r_PL-P3" = the lab record to link to, empty = move it too) and the hidden "relink" pairs after it."""
        out = parse_relink(relink, user)
        out.update({store.resolve(k[2:], user["uid"]): store.resolve(v, user["uid"]) for k, v in request.query_params.items() if k.startswith("r_") and v})
        return out

    @app.get("/move", response_class=HTMLResponse)
    async def move_preview(request: Request, ids: list[str] = Query(default=[]), relink: list[str] = Query(default=[])):
        user = c.need_user(request)
        return page(request, clean_ids(ids, user), choices(request, relink, user), decided=False)

    @app.get("/move/confirm", response_class=HTMLResponse)
    async def move_confirm(request: Request, ids: list[str] = Query(default=[]), relink: list[str] = Query(default=[])):
        user = c.need_user(request)
        return page(request, clean_ids(ids, user), choices(request, relink, user), decided=True)

    @app.post("/move/preview", response_class=HTMLResponse)
    async def move_from_selection(request: Request):
        """The move page for a selection ticked in a list (a POST, so that the form token stays out of the address)."""
        user = c.need_user(request)
        form = await request.form()
        c.check_csrf(request, form)
        return page(request, clean_ids(form.getlist("ids"), user), {}, decided=False)

    @app.post("/move")
    async def move_apply(request: Request):
        user = c.need_user(request)
        form = await request.form()
        c.check_csrf(request, form)
        ids, relink = clean_ids(form.getlist("ids"), user), parse_relink(form.getlist("relink"), user)
        if not ids or any(store.readable(user, i) is None for i in ids):
            raise HTTPException(404, "record not found")                  # a record this person cannot see looks missing
        try:
            mapping = personal.apply_move(store, user, ids, relink)
        except ValidationError as e:
            raise HTTPException(409, "; ".join(f"{k}: {v}" for k, v in e.errors.items()))
        except NotFound:
            raise HTTPException(404, "nothing to move")
        except Conflict as e:
            raise HTTPException(409, str(e))
        c.flash(request, "Moved to the lab collection: " + ", ".join(f"{o} is now {n}" for o, n in mapping.items()) + ".")
        return RedirectResponse(f"/r/{mapping.get(ids[0], next(iter(mapping.values())))}", 303)

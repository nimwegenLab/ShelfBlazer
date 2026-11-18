"""Bulk edit pages: choose what to do for the selected records, apply it (one git commit), undo it."""
import re

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.requests import Request

from shelfblazer.store import BULK_ADMIN_ACTIONS, Conflict, ValidationError

MAX_IDS = 5000                  # the same as lists.SHOW_ALL_MAX: every row a list can show can be selected


def register(app, c):
    store = c.store

    def selected_ids(form, slug, user):
        return [i for i in dict.fromkeys(form.getlist("ids")) if store.readable(user, i)][:MAX_IDS]

    def prepare_page(request, user, slug, ids, form=None, errors=None, status=200):
        schema = store.schemas[slug]
        editable = [i for i in ids if store.records[i]["type"] == slug and store.can_edit(user, store.records[i])]
        keys = {f["key"] for f in store.bulk_fields(slug)}
        fields = [d for d in c.form_descriptors(slug, keep_legacy=True) if d["key"] in keys]
        return c.render(request, "bulk.html", status=status, slug=slug, schema=schema, ids=ids, n_edit=len(editable),
                        n_skip=len(ids) - len(editable), fields=fields, multi=[d for d in fields if d["type"] in ("multiselect", "ref")],
                        people=[(u["uid"], u["name"], u["username"]) for u in c.users.all()], admin_actions=BULK_ADMIN_ACTIONS,
                        values=form or {}, errors=errors or {})

    @app.post("/t/{slug}/bulk", response_class=HTMLResponse)
    async def bulk_prepare(request: Request, slug: str):
        user = c.need_user(request)
        form = await request.form()
        c.check_csrf(request, form)
        if slug not in store.schemas or slug not in store.readable_types(user):
            raise HTTPException(404, "unknown type")
        ids = selected_ids(form, slug, user)
        if not ids:
            c.flash(request, "Select at least one record first.", "err")
            return RedirectResponse(f"/t/{slug}", 303)
        return prepare_page(request, user, slug, ids)

    @app.post("/t/{slug}/bulk/apply", response_class=HTMLResponse)
    async def bulk_apply(request: Request, slug: str):
        user = c.need_user(request)
        form = await request.form()
        c.check_csrf(request, form)
        if slug not in store.schemas or slug not in store.readable_types(user):
            raise HTTPException(404, "unknown type")
        ids = [i for i in dict.fromkeys(form.getlist("ids")) if store.readable(user, i)][:MAX_IDS]
        action, field = form.get("action", ""), form.get("field", "")
        value = None
        if action == "set":
            fdef = next((f for f in store.bulk_fields(slug) if f["key"] == field), None)
            kind = fdef["type"] if fdef else "text"
            value = (form.get(f"value_{field}") == "yes" if kind == "bool" else form.getlist(f"value_{field}") if kind == "multiselect"
                     else form.get(f"value_{field}", ""))
        elif action in ("add", "remove"):
            value = form.get(f"opt_{field}", "")
        elif action == "owner":
            value = form.get("owner", "")
        names = {i: store.records[i]["name"] for i in ids}                      # taken before the change: archiving other people's records hides them from you
        try:
            result = store.bulk_apply(user, slug, ids, action, field, value)
        except ValidationError as e:
            return prepare_page(request, user, slug, ids, form, e.errors, status=422)
        return c.render(request, "bulk_result.html", slug=slug, schema=store.schemas[slug], res=result, n=len(ids), names=names)

    @app.post("/bulk/undo")
    async def bulk_undo(request: Request):
        user = c.need_user(request)
        form = await request.form()
        c.check_csrf(request, form)
        back = form.get("back", "/")
        back = back if re.fullmatch(r"/t/[a-z0-9_-]+", back or "") else "/"
        try:
            store.undo(user, form.get("sha", ""))
            c.flash(request, "The bulk edit was undone.")
        except (ValidationError, Conflict) as e:
            c.flash(request, str(e).split(": ", 1)[-1], "err")
        return RedirectResponse(back, 303)

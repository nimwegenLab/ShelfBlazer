"""Export the selected records of a type as a tab-separated file: choose the columns, then download."""
import datetime, re

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.requests import Request

from shelfblazer import export as tsv_export

MAX_IDS = 5000


def register(app, c):
    store = c.store

    def chosen(request_form, slug, user):
        return [i for i in dict.fromkeys(request_form.getlist("ids")) if (r := store.readable(user, i)) and r["type"] == slug][:MAX_IDS]

    async def read(request, slug):
        user = c.need_user(request)
        form = await request.form()
        c.check_csrf(request, form)
        if slug not in store.schemas or slug not in store.readable_types(user):
            raise HTTPException(404, "unknown type")
        return user, form

    @app.post("/t/{slug}/export", response_class=HTMLResponse)
    async def export_page(request: Request, slug: str):
        user, form = await read(request, slug)
        ids = chosen(form, slug, user)
        if not ids:
            c.flash(request, "Select at least one record first.", "err")
            return RedirectResponse(f"/t/{slug}", 303)
        return c.render(request, "export.html", slug=slug, schema=store.schemas[slug], ids=ids, cols=tsv_export.columns(store, slug),
                        picked=None, error=None)

    @app.post("/t/{slug}/export/download")
    async def export_download(request: Request, slug: str):
        user, form = await read(request, slug)
        ids, keys = chosen(form, slug, user), form.getlist("col")
        if not ids:
            c.flash(request, "Select at least one record first.", "err")
            return RedirectResponse(f"/t/{slug}", 303)
        if not keys:
            return c.render(request, "export.html", status=422, slug=slug, schema=store.schemas[slug], ids=ids,
                            cols=tsv_export.columns(store, slug), picked=set(), error="Choose at least one column.")
        text, _ = tsv_export.tsv(store, user, slug, ids, keys)
        name = re.sub(r"[^a-z0-9_-]", "", slug) or "records"
        return Response(text, media_type="text/tab-separated-values; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}-{datetime.date.today().isoformat()}.tsv"',
                                 "Cache-Control": "no-store"})

"""The Help page (``/help``) and the administrators' editor for the lab's own section of it (``/admin/help``).

The page is one template. What it says follows the instance: examples come from the type definitions, and the parts about personal
collections, scanning and the assistant appear only where those are on. The lab's own section is ``config/help.md`` in the data
repository (shelfblazer/helptext.py), shown right after the summary under the title of its first heading.
"""
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.requests import Request

from shelfblazer import fields as fieldrules, helptext
from shelfblazer.auth import TOKEN_DEFAULT_DAYS, TOKEN_MAX_DAYS
from shelfblazer.errors import ValidationError

STARTER = """# How our records are organised

Say which type to use for what, and how similar types differ.

**<Type A> or <Type B>?** The rule that tells them apart, with one example of each.

**Where is it kept?** What to fill in, and where, for each type.

**Who to ask:** a name for accounts and types, a name for orders.
"""


def register(app, c):
    store = c.store

    def examples(u):
        """Names for the examples on the page, from the person's own types: the first type they may add to, and the first link field."""
        readable = [s for s in c.ordered_types() if s in store.readable_types(u)]
        pick = next((s for s in readable if store.can_create(u, s)), readable[0] if readable else None)
        link = None
        for s in readable:
            for f in store.schemas[s]["fields"]:
                if f["type"] == "ref" and not f.get("hidden") and f.get("to") in store.schemas:
                    link = dict(type=store.schemas[s]["name"].lower(), field=f["label"], to=store.schemas[f["to"]]["name"].lower())
                    break
            if link:
                break
        sch = store.schemas.get(pick) if pick else None
        return dict(type=sch["name"].lower() if sch else "record", prefix=sch["prefix"] if sch else "AB", link=link)

    @app.get("/help", response_class=HTMLResponse)
    async def help_page(request: Request):
        u = c.need_user(request)
        title, body = helptext.split(helptext.read(store))
        readable = [s for s in c.ordered_types() if s in store.readable_types(u)]
        return c.render(request, "help.html", ex=examples(u), lab_title=title or "In this lab", lab_body=body if (title or body) else None,
                        personal=bool(c.my_types(u)), mcp_enabled=c.mcp, token_days=TOKEN_DEFAULT_DAYS, token_max=TOKEN_MAX_DAYS,
                        sequences=any(fieldrules.is_sequence(f) for s in readable for f in store.schemas[s]["fields"]))

    def editor(request, text, status=200, preview=False, error=None):
        title, body = helptext.split(text)
        return c.render(request, "admin_help.html", status=status, text=text, error=error, preview=preview, title=title, body=body,
                        starter=not helptext.read(store).strip(), max_chars=helptext.MAX_CHARS)

    @app.get("/admin/help", response_class=HTMLResponse)
    async def help_editor(request: Request):
        c.need_admin(request)
        return editor(request, helptext.read(store) or STARTER)

    @app.post("/admin/help", response_class=HTMLResponse)
    async def help_save(request: Request):
        user = c.need_admin(request)
        form = await request.form()
        c.check_csrf(request, form)
        text = str(form.get("text", ""))
        if form.get("action") == "preview":
            return editor(request, text, preview=True)
        try:
            helptext.save(store, text, user)
        except ValidationError as e:
            return editor(request, text, status=422, error="; ".join(e.errors.values()))
        c.flash(request, "Help text saved." if text.strip() else "Help text removed.")
        return RedirectResponse("/admin/help", 303)

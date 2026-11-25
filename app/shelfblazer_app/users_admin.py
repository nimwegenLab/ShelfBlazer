"""User administration (admins only): the users table, one person's entries, create and update accounts, set-password links,
the sign-up passphrase."""
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from shelfblazer import ids

ENTRIES_PER_TYPE = 50   # rows per type on a person's entries page; the heading links to the full, filterable list


def register(app, c):
    users, store, render, flash, check_csrf, need_admin, off_loop = c.users, c.store, c.render, c.flash, c.check_csrf, c.need_admin, c.off_loop

    @app.get("/admin/users", response_class=HTMLResponse)
    async def admin_users(request: Request):
        need_admin(request)
        counts = {}
        for r in store.records.values():
            counts[r["created_by"]] = counts.get(r["created_by"], 0) + 1
        rows = [dict(u, n=counts.get(u["uid"], 0), has_pw=bool(u["pw"]), since=(u.get("created") or "")[:10], on=bool(u["active"] and u["pw"])) for u in users.all()]
        rows.sort(key=lambda r: r["name"].lower())
        rows.sort(key=lambda r: r.get("created") or "", reverse=True)            # newest accounts first, people created together by name
        return render(request, "admin_users.html", rows=rows, n_inactive=sum(1 for r in rows if not r["on"]), passphrase_set=users.signup_enabled())

    @app.get("/admin/users/{username}/entries", response_class=HTMLResponse)
    async def admin_user_entries(request: Request, username: str):
        """Everything one person created, one table per record type (the numbers in the user table link here)."""
        need_admin(request)
        person = users.get(username)
        if not person:
            raise HTTPException(404, "unknown user")
        by_type = {}
        for r in store.records.values():
            if r["created_by"] == person["uid"]:
                by_type.setdefault(r["type"], []).append(r)
        sections = []
        for slug in store.ordered_slugs():
            recs = sorted(by_type.get(slug, []), key=lambda r: (ids.sort_key(r), r["id"]))
            if recs:
                sections.append(dict(slug=slug, name=store.schemas[slug]["name"], total=len(recs), shown=recs[:ENTRIES_PER_TYPE]))
        return render(request, "admin_user_entries.html", person=person, sections=sections, total=sum(x["total"] for x in sections),
                      cap=ENTRIES_PER_TYPE)

    @app.post("/admin/users/create")
    async def admin_create(request: Request):
        need_admin(request)
        form = await request.form()
        check_csrf(request, form)
        try:
            users.create((form.get("username") or "").strip(), (form.get("name") or "").strip(), form.get("role", "user"))
            flash(request, "User created without a password. Generate a set-password link for them.")
        except ValueError as e:
            flash(request, str(e), "err")
        return RedirectResponse("/admin/users", 303)

    @app.post("/admin/users/{username}/update")
    async def admin_update(request: Request, username: str):
        me_ = need_admin(request)
        form = await request.form()
        check_csrf(request, form)
        try:
            kw = {}
            if form.get("role"):
                kw["role"] = form["role"]
            if form.get("active") in ("0", "1"):
                kw["active"] = form["active"] == "1"
            if username == me_["username"] and (kw.get("active") is False or kw.get("role") == "user"):
                raise ValueError("you cannot demote or deactivate yourself")
            users.update(username, **kw)
            flash(request, f"Updated {username}.")
        except (ValueError, KeyError) as e:
            flash(request, str(e), "err")
        return RedirectResponse("/admin/users", 303)

    @app.post("/admin/users/{username}/reset-link")
    async def admin_reset_link(request: Request, username: str):
        need_admin(request)
        form = await request.form()
        check_csrf(request, form)
        try:
            token = users.make_reset_token(username)
        except KeyError:
            raise HTTPException(404, "unknown user")
        return render(request, "reset_link.html", target=username, link=str(request.base_url).rstrip("/") + f"/reset/{token}")

    @app.post("/admin/passphrase")
    async def admin_passphrase(request: Request):
        need_admin(request)
        form = await request.form()
        check_csrf(request, form)
        phrase = (form.get("passphrase") or "").strip()
        if form.get("clear"):
            users.set_signup_passphrase(None)
            flash(request, "Sign-up disabled.")
        elif len(phrase) < 8:
            flash(request, "Passphrase: use at least 8 characters.", "err")
        else:
            await off_loop(users.set_signup_passphrase, phrase)
            flash(request, "Sign-up passphrase updated.")
        return RedirectResponse("/admin/users", 303)

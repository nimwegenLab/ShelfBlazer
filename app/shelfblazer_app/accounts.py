"""Signing in and out, sign-up with the passphrase, set-password links, your own page and password, and the look and colour mode."""
import secrets, time

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from shelfblazer import perms
from shelfblazer.auth import (ADMIN_TOKEN_MAX_DAYS, TOKEN_DEFAULT_DAYS, TOKEN_MAX_DAYS, check_password_strength)

APPEARANCES = ("auto", "day", "night")   # colour mode; auto follows the system setting
LOOKS = ("spectrum", "classic")           # spectrum: a colour per record type (default); classic: one accent colour


def register(app, c):
    users, render, flash, check_csrf, need_user, user_of = c.users, c.render, c.flash, c.check_csrf, c.need_user, c.user_of
    off_loop, safe_next, https_only = c.off_loop, c.safe_next, c.https_only

    def name_of(username):
        u = users.get(username)
        return u["name"] if u else username

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request, next: str = "/"):
        if user_of(request):
            return RedirectResponse("/", 303)
        return render(request, "login.html", next=safe_next(next))

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        check_csrf(request, form)
        ip = request.client.host if request.client else "?"
        u, err = await off_loop(users.authenticate, str(form.get("username", "")), str(form.get("password", "")), ip)
        if not u:
            return render(request, "login.html", status=401, error=err, next=safe_next(form.get("next")), username=form.get("username", ""))
        request.session.clear()
        request.session.update(u=u["username"], pwv=users.password_version(u), sid=users.start_session(u["username"]), csrf=secrets.token_urlsafe(24))
        return RedirectResponse(safe_next(form.get("next")), 303)

    @app.post("/logout")
    async def logout(request: Request):
        form = await request.form()
        check_csrf(request, form)
        if request.session.get("u"):
            users.end_session(request.session["u"], request.session.get("sid"))   # a copy of the cookie stops working too
        request.session.clear()                                                  # only the session: look, colour mode and list prefs stay
        return RedirectResponse("/login", 303)

    @app.get("/signup", response_class=HTMLResponse)
    async def signup_form(request: Request):
        return render(request, "signup.html")

    @app.post("/signup")
    async def signup(request: Request):
        form = await request.form()
        check_csrf(request, form)
        username = (form.get("username") or "").strip().lower()
        ip = request.client.host if request.client else "?"
        err = None
        if users.signup_locked(ip):
            err = "Too many attempts. Wait a few minutes."
        elif not await off_loop(users.check_signup_passphrase, str(form.get("passphrase", ""))):
            users.record_signup_failure(ip)
            err = "Wrong sign-up passphrase."
        elif form.get("password") != form.get("password2"):
            err = "The two passwords differ."
        elif check_password_strength(form.get("password", "")):
            err = "Password: " + check_password_strength(form.get("password", ""))
        elif users.get(username):
            err = "That username is taken. If it is yours, ask an administrator for a set-password link."
        else:
            try:
                await off_loop(users.create, username, (form.get("name") or username).strip(), "user", form.get("password"))
            except ValueError as e:
                err = str(e)
        if err:
            return render(request, "signup.html", status=400, error=err, username=username, name=form.get("name", ""))
        flash(request, "Account created. You can sign in now.")
        return RedirectResponse("/login", 303)

    @app.get("/reset/{token}", response_class=HTMLResponse)
    async def reset_form(request: Request, token: str):
        who = users.reset_user(token)
        if not who:
            raise HTTPException(404, "This link is invalid or has expired.")
        return render(request, "reset.html", who=who, display_name=name_of(who))

    @app.post("/reset/{token}")
    async def reset(request: Request, token: str):
        form = await request.form()
        check_csrf(request, form)
        who = users.reset_user(token)
        if not who:
            raise HTTPException(404, "This link is invalid or has expired.")
        pw = form.get("password", "")
        err = None if pw == form.get("password2") else "The two passwords differ."
        err = err or check_password_strength(pw)
        if err:
            return render(request, "reset.html", status=400, who=who, display_name=name_of(who), error=err)
        await off_loop(users.set_password, who, pw)
        flash(request, "Password set. You can sign in now.")
        return RedirectResponse("/login", 303)

    TABS = {"general": "me.html", "assistant": "me_assistant.html", "password": "me_password.html"}

    def example_type(u):
        """A type the person adds records to, for the examples on the assistant tab ("Add a strain called ...")."""
        slug = next((s for s in c.ordered_types() if c.store.can_create(u, s)), None)
        return c.store.schemas[slug]["name"].lower() if slug else "record"

    def me_page(request, u, tab="general", status=200, **ctx):
        """One tab of the account page: general (display, own collection), AI assistant (tokens; only with the MCP endpoint), password."""
        if tab == "assistant" and not c.mcp:
            raise HTTPException(404, "the AI assistant is not enabled")
        extra = {}
        if tab == "assistant":
            when = lambda ts: time.strftime("%d %b %Y", time.localtime(ts)) if ts else "never"
            extra = dict(tokens=[dict(t, expires_text=when(t["expires"]), used_text=when(t["last_used"])) for t in users.tokens_of(u["username"])],
                         token_default=TOKEN_DEFAULT_DAYS, token_max=TOKEN_MAX_DAYS, admin_token_max=ADMIN_TOKEN_MAX_DAYS, ex=example_type(u))
            extra["ex_a"] = ("an " if extra["ex"][:1] in "aeiou" else "a ") + extra["ex"]         # "an item", "a strain"
        return render(request, TABS[tab], status=status, tab=tab, **extra, **ctx)

    @app.get("/me", response_class=HTMLResponse)
    async def me(request: Request):
        return me_page(request, need_user(request))

    @app.get("/me/assistant", response_class=HTMLResponse)
    async def me_assistant(request: Request):
        """How to connect an AI assistant, step by step, and the person's tokens (only with the MCP endpoint)."""
        return me_page(request, need_user(request), "assistant")

    @app.get("/me/password", response_class=HTMLResponse)
    async def me_password_form(request: Request):
        return me_page(request, need_user(request), "password")

    @app.post("/me/tokens", response_class=HTMLResponse)
    async def me_token_create(request: Request):
        """A new access token for the MCP endpoint, shown once on the page that answers (never in a redirect, a URL or a flash). Asks for
        the password: a token lasts longer than a session and survives signing out, so a stolen session cookie alone must not make one."""
        u = need_user(request)
        form = await request.form()
        check_csrf(request, form)
        if not c.mcp:
            raise HTTPException(404, "access tokens are not enabled")
        ip = request.client.host if request.client else "?"
        if not (await off_loop(users.authenticate, u["username"], str(form.get("current", "")), ip))[0]:
            return me_page(request, u, "assistant", status=400, error="Your ShelfBlazer password is wrong.")
        scope = "admin" if form.get("admin") and perms.is_admin(u) else "member"
        try:
            days = int(form.get("days") or TOKEN_DEFAULT_DAYS)
            text, rec = users.create_token(u["username"], form.get("name", ""), days, scope)
        except ValueError as e:
            return me_page(request, u, "assistant", status=400, error=str(e))
        return me_page(request, u, "assistant", new_token=dict(text=text, name=rec["name"]))

    @app.post("/me/tokens/{tid}/revoke")
    async def me_token_revoke(request: Request, tid: str):
        u = need_user(request)
        form = await request.form()
        check_csrf(request, form)
        flash(request, "Token revoked." if users.revoke_token(u["username"], tid) else "No such token.")
        return RedirectResponse("/me/assistant#tokens", 303)

    @app.post("/me/collections")
    async def me_collections(request: Request):
        """"Visible to others" for each type of the person's personal collections (the box ticked = visible)."""
        u = need_user(request)
        form = await request.form()
        check_csrf(request, form)
        for slug, _ in c.my_types(u):
            users.set_collection_visible(u["username"], slug, f"visible_{slug}" in form)
        flash(request, "Saved.")
        return RedirectResponse("/me#collections", 303)

    @app.post("/me/password")
    async def me_password(request: Request):
        u = need_user(request)
        form = await request.form()
        check_csrf(request, form)
        pw = form.get("password", "")
        err = None
        if not (await off_loop(users.authenticate, u["username"], str(form.get("current", "")), request.client.host if request.client else "?"))[0]:
            err = "Current password is wrong."
        elif pw != form.get("password2"):
            err = "The two passwords differ."
        else:
            err = check_password_strength(pw)
        if err:
            return me_page(request, u, "password", status=400, error=err)
        had_tokens = bool(users.tokens_of(u["username"]))
        await off_loop(users.set_password, u["username"], pw)             # revokes the access tokens too
        request.session["pwv"] = users.password_version(users.get(u["username"]))
        flash(request, "Password changed. Your access tokens were revoked: make new ones if you still need them." if had_tokens else "Password changed.")
        return RedirectResponse("/me/password", 303)

    @app.get("/look")
    async def choose_look(request: Request, name: str = "spectrum", next: str = "/"):
        resp = RedirectResponse(safe_next(next), 303)
        if name in LOOKS:
            resp.set_cookie("look", name, max_age=365 * 24 * 3600, samesite="lax", httponly=True, secure=https_only)
        return resp

    @app.get("/appearance")
    async def choose_appearance(request: Request, name: str = "auto", next: str = "/"):
        resp = RedirectResponse(safe_next(next), 303)
        if name in APPEARANCES:
            resp.set_cookie("appearance", name, max_age=365 * 24 * 3600, samesite="lax", httponly=True, secure=https_only)
        return resp

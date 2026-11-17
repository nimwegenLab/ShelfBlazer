"""Signing in and out, sign-up with the passphrase, set-password links, your own page and password, and the look and colour mode."""
import secrets

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from shelfblazer.auth import check_password_strength

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

    @app.get("/me", response_class=HTMLResponse)
    async def me(request: Request):
        need_user(request)
        return render(request, "me.html")

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
            return render(request, "me.html", status=400, error=err)
        await off_loop(users.set_password, u["username"], pw)
        request.session["pwv"] = users.password_version(users.get(u["username"]))
        flash(request, "Password changed.")
        return RedirectResponse("/me", 303)

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

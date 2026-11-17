"""Hardening from the security audit: lockout cannot be aimed at someone else, throttled sign-up passphrase, request size limits,
real sign-out, NUL characters, display names, and the display preferences surviving every session change."""
import re

from shelfblazer import auth
from test_app import PW, Base


def multipart(boundary, parts_mb, token=None, name="x"):
    """A chunked multipart body (no Content-Length) with one file part of `parts_mb` MB."""
    head = ""
    if token:
        head += f"--{boundary}\r\nContent-Disposition: form-data; name=\"csrf_token\"\r\n\r\n{token}\r\n"
    head += f"--{boundary}\r\nContent-Disposition: form-data; name=\"name\"\r\n\r\n{name}\r\n"
    head += f"--{boundary}\r\nContent-Disposition: form-data; name=\"pad\"; filename=\"a.bin\"\r\n\r\n"
    yield head.encode()
    for _ in range(parts_mb):
        yield b"0" * (1024 * 1024)
    yield f"\r\n--{boundary}--\r\n".encode()


class TestLockout(Base):
    def test_failures_from_another_address_do_not_lock_the_account(self):
        for _ in range(auth.MAX_FAILS + 3):
            self.users.authenticate("root", "wrong", "6.6.6.6")
        self.assertIn("Too many", self.users.authenticate("root", PW, "6.6.6.6")[1])      # the guessing address is locked
        user, err = self.users.authenticate("root", PW, "10.0.0.2")                       # the real admin elsewhere is not
        self.assertIsNotNone(user, err)

    def test_one_address_spraying_many_usernames_is_locked(self):
        for i in range(auth.MAX_IP_FAILS):
            self.users.authenticate(f"user{i}", "wrong", "6.6.6.6")
        self.assertIn("Too many", self.users.authenticate("alice", PW, "6.6.6.6")[1])
        self.assertIsNotNone(self.users.authenticate("alice", PW, "10.0.0.2")[0])

    def test_quiet_entries_are_forgotten(self):
        self.users.record_failure("alice", "1.1.1.1")
        for key in list(self.users._fails):
            self.users._fails[key] = [0.0]                                                  # long ago
        self.users.is_locked("alice", "1.1.1.1")
        self.assertEqual(self.users._fails, {})
        self.users.record_signup_failure("1.1.1.1")
        self.users._fails["signup|1.1.1.1"] = [0.0]
        self.users.signup_locked("1.1.1.1")
        self.assertFalse(any(k.startswith("signup|") for k in self.users._fails))


class TestSignupThrottle(Base):
    def test_wrong_passphrases_are_throttled_per_address(self):
        c = self.client()
        base = {"username": "eve", "name": "Eve", "password": PW, "password2": PW}
        for i in range(auth.MAX_SIGNUP_FAILS):
            r = self.post(c, "/signup", {**base, "passphrase": f"guess {i}"}, anon=True)
            self.assertIn("Wrong sign-up passphrase", r.text)
        r = self.post(c, "/signup", {**base, "passphrase": "lab-secret-phrase"}, anon=True)    # even the right one, for now
        self.assertEqual(r.status_code, 400)
        self.assertIn("Too many", r.text)
        self.assertIsNone(self.users.get("eve"))


class TestSessions(Base):
    def test_copied_cookie_stops_working_after_sign_out(self):
        c = self.client("alice")
        stolen = c.cookies.get("shelfblazer")
        self.post(c, "/logout")
        other = self.client()
        other.cookies.set("shelfblazer", stolen)
        self.assertEqual(other.get("/me").status_code, 303)

    def test_signing_out_one_browser_keeps_the_others(self):
        c1, c2 = self.client("alice"), self.client("alice")
        self.post(c1, "/logout")
        self.assertEqual(c1.get("/").status_code, 303)
        self.assertEqual(c2.get("/").status_code, 200)

    def test_session_ids_are_stored_hashed_and_bounded(self):
        sids = [self.users.start_session("bob") for _ in range(auth.MAX_SESSIONS + 5)]
        stored = self.users.get("bob")["sessions"]
        self.assertEqual(len(stored), auth.MAX_SESSIONS)
        self.assertFalse(any(s in stored for s in sids))                      # never the raw id
        self.assertTrue(self.users.session_valid(self.users.get("bob"), sids[-1]))
        self.assertFalse(self.users.session_valid(self.users.get("bob"), sids[0]))   # the oldest were signed out
        self.assertNotIn("sessions", self.users.public(self.users.get("bob")))

    def test_old_cookie_without_session_id_must_sign_in_again(self):
        c = self.client("alice")
        u = self.users.get("alice")
        with self.users._txn() as d:                                          # as after the upgrade: no stored sessions
            d["users"]["alice"].pop("sessions", None)
        self.assertTrue(u)
        self.assertEqual(c.get("/").status_code, 303)


class TestDisplayPrefsSurvive(Base):
    """Look, colour mode and list preferences are browser cookies of their own: no session change may drop them."""

    def prefs(self, c):
        c.get("/look?name=classic&next=/")
        c.get("/appearance?name=night&next=/")
        c.get("/t/strain?sort=name&dir=desc&view=cards")
        return {k: c.cookies.get(k) for k in ("look", "appearance", "listprefs")}

    def assertShown(self, c):
        page = c.get("/t/strain").text
        self.assertIn('data-appearance="night"', page)
        self.assertIn('data-look="classic"', page)
        self.assertIn('aria-current="true">Cards', page)                    # the remembered list view

    def test_kept_through_sign_out_sign_in_and_revoked_sessions(self):
        c = self.client("alice")
        before = self.prefs(c)
        self.assertTrue(all(before.values()), before)
        r = self.post(c, "/logout")
        self.assertFalse(re.search(r"\b(look|appearance|listprefs)=", r.headers.get("set-cookie", "")))   # sign-out does not touch them
        self.assertEqual({k: c.cookies.get(k) for k in before}, before)
        self.assertIn('data-appearance="night"', c.get("/login").text)                                 # sign-in page still in Night
        self.post(c, "/login", {"username": "alice", "password": PW}, anon=True)
        self.assertShown(c)
        with self.users._txn() as d:                                                                    # session ended elsewhere
            d["users"]["alice"]["sessions"] = {}
        self.assertEqual(c.get("/").status_code, 303)
        self.assertEqual({k: c.cookies.get(k) for k in before}, before)
        self.post(c, "/login", {"username": "alice", "password": PW}, anon=True)
        self.assertShown(c)

    def test_kept_through_password_change(self):
        c = self.client("alice")
        before = self.prefs(c)
        self.post(c, "/me/password", {"current": PW, "password": "another long pass", "password2": "another long pass"})
        self.assertEqual({k: c.cookies.get(k) for k in before}, before)
        self.assertShown(c)


class TestBodyLimits(Base):
    def test_declared_size_over_the_form_limit_is_refused_before_sign_in(self):
        c = self.client()
        r = c.post("/login", content=b"x" * 10, headers={"content-type": "application/x-www-form-urlencoded",
                                                          "content-length": str(5 * 1024 * 1024)})
        self.assertEqual(r.status_code, 413)

    def test_chunked_body_to_login_is_cut_off(self):
        r = self.client().post("/login", content=multipart("B", 6), headers={"content-type": "multipart/form-data; boundary=B"})
        self.assertEqual(r.status_code, 413)

    def test_chunked_upload_without_size_is_refused(self):
        c = self.client("alice")
        r = c.post("/t/strain/new", content=multipart("B", 1, self.token(c)), headers={"content-type": "multipart/form-data; boundary=B"})
        self.assertEqual(r.status_code, 411)
        self.assertEqual(len(self.store.records), 0)

    def test_normal_forms_and_uploads_still_work(self):
        c = self.client("alice")
        r = c.post("/t/plasmid/new", data={"csrf_token": self.token(c), "name": "with file"}, files={"f_attachments": ("a.gb", b"LOCUS", "text/plain")})
        self.assertEqual(r.status_code, 303)
        rid = r.headers["location"].split("/")[-1]
        r = c.post(f"/r/{rid}/files", data={"csrf_token": self.token(c), "field": "attachments"}, files={"files": ("b.gb", b"x" * (5 * 1024 * 1024), "text/plain")})
        self.assertEqual(r.status_code, 303)                     # 5 MB: over the form limit, fine on an upload route
        self.assertEqual(len(self.store.records[rid]["fields"]["attachments"]), 2)
        self.assertEqual(self.post(c, f"/r/{rid}/edit", {"name": "n", "notes": "y" * 100_000, "version": self.version(rid)}).status_code, 303)


class TestTextHygiene(Base):
    def test_nul_in_notes_is_dropped_and_never_breaks_the_page(self):
        c = self.client("alice")
        rid = self.post(c, "/t/strain/new", {"name": "n\x00ul", "notes": "hello \x0099\x00 world"}).headers["location"].split("/")[-1]
        self.assertEqual(self.store.records[rid]["name"], "nul")
        self.assertNotIn("\x00", self.store.records[rid]["notes"])
        self.assertEqual(self.client("alice").get(f"/r/{rid}").status_code, 200)

    def test_markdown_ignores_forged_placeholders(self):
        from shelfblazer import markdown
        self.assertNotIn("\x00", markdown.render("a \x007\x00 b [x](https://e.org)"))

    def test_display_names_are_bounded_and_single_line(self):
        c = self.client()
        base = {"passphrase": "lab-secret-phrase", "password": PW, "password2": PW}
        r = self.post(c, "/signup", {**base, "username": "long1", "name": "A" * (auth.MAX_NAME_LENGTH + 1)}, anon=True)
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(self.users.get("long1"))
        self.assertEqual(self.post(c, "/signup", {**base, "username": "tabby", "name": "Ta\tbby\nCat"}, anon=True).status_code, 303)
        self.assertEqual(self.users.get("tabby")["name"], "Ta bby Cat")

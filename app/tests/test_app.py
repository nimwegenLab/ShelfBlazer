"""Permission, account and integrity tests. Run: .venv/bin/python -m unittest discover -s tests -v"""
import json, os, re, subprocess, tempfile, unittest

from starlette.testclient import TestClient

from shelfblazer import auth
from shelfblazer_app.web import create_app

auth.SCRYPT.update(n=2**10)  # fast hashing in tests; stored hashes carry their own parameters

PW = "correct horse battery"
STRAIN = {"name": "Strain", "prefix": "ST", "list_columns": ["id", "name", "plasmid", "storage", "created_by"], "fields": [
    {"key": "name", "label": "Name", "type": "text", "required": True},
    {"key": "genotype", "label": "Genotype", "type": "text"},
    {"key": "storage", "label": "Storage", "type": "text", "legacy": True},
    {"key": "plasmid", "label": "Plasmid(s)", "type": "ref", "to": "plasmid", "multiple": True},
    {"key": "status", "label": "Status", "type": "select", "options": ["ok", "bad"]},
    {"key": "legacy_owner", "label": "Legacy owner", "type": "text"}]}
PLASMID = {"name": "Plasmid", "prefix": "PL", "list_columns": ["id", "name", "created_by"], "fields": [
    {"key": "name", "label": "Name", "type": "text", "required": True},
    {"key": "resistance", "label": "Resistance", "type": "multiselect", "options": ["Kan", "Amp"]},
    {"key": "sequence", "label": "Sequence", "type": "longtext"},
    {"key": "attachments", "label": "Files", "type": "file", "multiple": True}]}


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, check=True).stdout


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo, self.inst = os.path.join(self.tmp.name, "repo"), os.path.join(self.tmp.name, "inst")
        os.makedirs(os.path.join(self.repo, "config", "types"))
        os.makedirs(os.path.join(self.repo, "data"))
        for slug, s in (("strain", STRAIN), ("plasmid", PLASMID)):
            with open(os.path.join(self.repo, "config", "types", slug + ".yaml"), "w") as fh:
                json.dump(s, fh)
        with open(os.path.join(self.repo, "config", "storage_places.yaml"), "w") as fh:
            fh.write("[]")
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True)
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
        self.app = create_app(self.repo, self.inst)
        self.users = self.app.state.users
        self.store = self.app.state.store
        self.users.create("root", "Root Admin", "admin", PW)
        self.users.create("alice", "Alice A", "user", PW)
        self.users.create("bob", "Bob B", "user", PW)
        self.users.set_signup_passphrase("lab-secret-phrase")

    @staticmethod
    def read(path):
        with open(path) as fh:
            return fh.read()

    def tearDown(self):
        self.tmp.cleanup()

    # helpers
    def client(self, who=None):
        c = TestClient(self.app, follow_redirects=False)
        if who:
            r = self.post(c, "/login", {"username": who, "password": PW}, anon=True)
            self.assertEqual(r.status_code, 303, r.text[:200])
        return c

    def token(self, c, anon=False):
        page = c.get("/login" if anon else "/me")
        m = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        self.assertTrue(m, page.text[:300])
        return m.group(1)

    def post(self, c, path, data=None, anon=False, token=True):
        data = dict(data or {})
        if token:
            data["csrf_token"] = self.token(c, anon)
        return c.post(path, data=data)

    def create(self, who, type_slug="strain", **fields):
        c = self.client(who)
        r = self.post(c, f"/t/{type_slug}/new", {"name": fields.pop("name", "x"), **{f"f_{k}": v for k, v in fields.items()}})
        self.assertEqual(r.status_code, 303, r.text[:300])
        return r.headers["location"].split("/")[-1]

    def version(self, rid):
        return self.store.records[rid]["_version"]

    def uid(self, username):
        """The user ID records and commits use for this person."""
        return self.users.get(username)["uid"]

    def purge(self, rid, message=None):
        """Stand-in for the planned purge command: the record file is gone from the repository and the store (a record that no longer
        exists). The app never deletes records, it archives them; ``message`` can imitate the "delete" commits of older versions."""
        path = self.store.path_of(rid, self.store.records[rid]["type"])
        os.remove(path)
        self.store.commit([path], message or f"{rid}: purge", self.users.get("root"))
        del self.store.records[rid]
        self.store._notify(rid)


class TestAccounts(Base):
    def test_anonymous_is_redirected_to_login(self):
        r = self.client().get("/")
        self.assertEqual(r.status_code, 303)
        self.assertIn("/login", r.headers["location"])

    def test_login_wrong_password_and_lockout(self):
        c = self.client()
        for _ in range(5):
            self.assertEqual(self.post(c, "/login", {"username": "alice", "password": "nope"}, anon=True).status_code, 401)
        r = self.post(c, "/login", {"username": "alice", "password": PW}, anon=True)  # now locked, even with the right password
        self.assertEqual(r.status_code, 401)
        self.assertIn("Too many", r.text)

    def test_stub_without_password_cannot_login(self):
        self.users.create("ghost", "Ghost", "user", None)
        self.assertEqual(self.post(self.client(), "/login", {"username": "ghost", "password": ""}, anon=True).status_code, 401)

    def test_csrf_required(self):
        c = self.client("alice")
        self.assertEqual(self.post(c, "/t/strain/new", {"name": "x"}, token=False).status_code, 403)
        self.assertEqual(len(self.store.records), 0)

    def test_signup_rules(self):
        c = self.client()
        base = {"username": "carol", "name": "Carol", "password": PW, "password2": PW}
        self.assertEqual(self.post(c, "/signup", {**base, "passphrase": "wrong"}, anon=True).status_code, 400)
        self.assertEqual(self.post(c, "/signup", {**base, "username": "alice", "passphrase": "lab-secret-phrase"}, anon=True).status_code, 400)
        self.assertEqual(self.post(c, "/signup", {**base, "password": "short", "password2": "short", "passphrase": "lab-secret-phrase"}, anon=True).status_code, 400)
        self.assertEqual(self.post(c, "/signup", {**base, "passphrase": "lab-secret-phrase"}, anon=True).status_code, 303)
        self.assertEqual(self.users.get("carol")["role"], "user")  # never admin through sign-up
        self.client("carol")

    def test_signup_accepts_eight_character_password_but_not_seven(self):
        c = self.client()
        base = {"name": "Fay", "passphrase": "lab-secret-phrase"}
        self.assertEqual(self.post(c, "/signup", {**base, "username": "fay1", "password": "abcdefg", "password2": "abcdefg"}, anon=True).status_code, 400)
        self.assertEqual(self.post(c, "/signup", {**base, "username": "fay2", "password": "abcdefgh", "password2": "abcdefgh"}, anon=True).status_code, 303)
        r = self.post(self.client(), "/login", {"username": "fay2", "password": "abcdefgh"}, anon=True)
        self.assertEqual(r.status_code, 303)
        self.assertIn("8+ characters", c.get("/signup").text)

    def test_signup_disabled_without_passphrase(self):
        self.users.set_signup_passphrase(None)
        r = self.post(self.client(), "/signup", {"username": "dave", "password": PW, "password2": PW, "passphrase": ""}, anon=True)
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(self.users.get("dave"))

    def test_reset_link_single_use_and_invalidates_sessions(self):
        self.users.create("erin", "Erin", "user", None)
        admin = self.client("root")
        r = self.post(admin, "/admin/users/erin/reset-link")
        link = re.search(r"/reset/([\w-]+)", r.text).group(1)
        anon = self.client()
        self.assertEqual(self.post(anon, f"/reset/{link}", {"password": "brand new pass 1", "password2": "brand new pass 1"}, anon=True).status_code, 303)
        self.assertEqual(anon.get(f"/reset/{link}").status_code, 404)  # one-time
        self.assertEqual(self.post(self.client(), "/login", {"username": "erin", "password": "brand new pass 1"}, anon=True).status_code, 303)

    def test_password_change_logs_out_other_sessions(self):
        c1, c2 = self.client("alice"), self.client("alice")
        r = self.post(c1, "/me/password", {"current": PW, "password": "another long pass", "password2": "another long pass"})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(c2.get("/").status_code, 303)  # old session dropped
        self.assertEqual(c1.get("/").status_code, 200)

    def test_deactivated_user_loses_access(self):
        c = self.client("bob")
        self.assertEqual(c.get("/").status_code, 200)
        self.users.update("bob", active=False)
        self.assertEqual(c.get("/").status_code, 303)
        self.assertEqual(self.post(self.client(), "/login", {"username": "bob", "password": PW}, anon=True).status_code, 401)

    def test_admin_pages_are_admin_only(self):
        self.assertEqual(self.client("alice").get("/admin/users").status_code, 403)
        self.assertEqual(self.client("root").get("/admin/users").status_code, 200)
        c = self.client("alice")
        self.assertEqual(self.post(c, "/admin/users/alice/update", {"role": "admin"}).status_code, 403)
        self.assertEqual(self.users.get("alice")["role"], "user")

    def test_admin_cannot_remove_own_admin_or_last_admin(self):
        c = self.client("root")
        self.post(c, "/admin/users/root/update", {"role": "user"})
        self.assertEqual(self.users.get("root")["role"], "admin")
        self.post(c, "/admin/users/root/update", {"active": "0"})
        self.assertTrue(self.users.get("root")["active"])


class TestOwnership(Base):
    def test_create_sets_owner_and_git_author(self):
        rid = self.create("alice", name="DH10B", genotype="wt")
        self.assertEqual(rid, "ST-1")
        rec = self.store.records[rid]
        self.assertEqual(rec["created_by"], self.uid("alice"))
        self.assertEqual(git(self.repo, "log", "-1", "--format=%an <%ae>").strip(), f"{self.uid('alice')} <{self.uid('alice')}@shelfblazer.invalid>")
        self.assertNotIn("Alice", git(self.repo, "log", "-1", "--format=%an %ae %s"))
        self.assertEqual(git(self.repo, "status", "--porcelain").strip(), "")

    def test_owner_cannot_be_spoofed(self):
        c = self.client("alice")
        r = self.post(c, "/t/strain/new", {"name": "x", "created_by": "bob", "f_created_by": "bob", "legacy_id": "999"})
        rid = r.headers["location"].split("/")[-1]
        self.assertEqual(self.store.records[rid]["created_by"], self.uid("alice"))
        self.assertEqual(rid, "ST-1")

    def test_other_user_cannot_edit_or_archive_and_there_is_no_delete(self):
        rid = self.create("alice", name="mine")
        before = self.read(self.store.path_of(rid, "strain"))
        bob = self.client("bob")
        self.assertEqual(bob.get(f"/r/{rid}/edit").status_code, 403)
        self.assertEqual(self.post(bob, f"/r/{rid}/edit", {"name": "hijacked", "version": self.version(rid)}).status_code, 403)
        self.assertEqual(self.post(bob, f"/r/{rid}/archive").status_code, 403)
        self.assertIn(self.post(self.client("alice"), f"/r/{rid}/delete").status_code, (404, 405))       # records are archived, never deleted
        self.assertIn(rid, self.store.records)
        self.assertEqual(self.read(self.store.path_of(rid, "strain")), before)
        self.assertEqual(self.store.records[rid]["name"], "mine")

    def test_owner_and_admin_can_edit(self):
        rid = self.create("alice", name="mine")
        for who, newname in (("alice", "by owner"), ("root", "by admin")):
            c = self.client(who)
            r = self.post(c, f"/r/{rid}/edit", {"name": newname, "version": self.version(rid)})
            self.assertEqual(r.status_code, 303, r.text[:200])
            self.assertEqual(self.store.records[rid]["name"], newname)
            self.assertEqual(self.store.records[rid]["created_by"], self.uid("alice"))  # ownership unchanged by an admin edit
        self.assertEqual(git(self.repo, "log", "-1", "--format=%an").strip(), self.uid("root"))

    def test_edit_buttons_follow_permissions(self):
        rid = self.create("alice", name="mine")
        self.assertIn("/edit", self.client("alice").get(f"/r/{rid}").text)
        self.assertNotIn(f"/r/{rid}/edit", self.client("bob").get(f"/r/{rid}").text)
        self.assertIn(f"/r/{rid}/edit", self.client("root").get(f"/r/{rid}").text)

    def test_stale_edit_is_rejected(self):
        rid = self.create("alice", name="v1")
        old = self.version(rid)
        c = self.client("alice")
        self.assertEqual(self.post(c, f"/r/{rid}/edit", {"name": "v2", "version": old}).status_code, 303)
        r = self.post(c, f"/r/{rid}/edit", {"name": "v3", "version": old})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.store.records[rid]["name"], "v2")

    def test_numbers_are_never_reused(self):
        a = self.create("alice", name="a")
        b = self.create("alice", name="b")
        self.purge(b)
        self.assertEqual(self.create("alice", name="c"), "ST-3")
        self.assertEqual((a, b), ("ST-1", "ST-2"))

    def test_archive_hides_from_default_list_but_keeps_record(self):
        rid = self.create("alice", name="oldstrain")
        self.assertEqual(self.post(self.client("alice"), f"/r/{rid}/archive").status_code, 303)
        for who in ("alice", "root"):                                                # the owner and administrators
            c = self.client(who)
            self.assertNotIn(rid, c.get("/t/strain").text.replace("/r/" + rid + "/edit", ""))
            self.assertIn(rid, c.get("/t/strain?archived=1").text)
        bob = self.client("bob")                                                     # everybody else never sees it
        self.assertNotIn(rid, bob.get("/t/strain?archived=1").text)

    def test_validation_rejects_bad_input_without_commit(self):
        c = self.client("alice")
        head = git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(self.post(c, "/t/strain/new", {"name": "", }).status_code, 422)
        self.assertEqual(self.post(c, "/t/strain/new", {"name": "x", "f_status": "weird"}).status_code, 422)
        self.assertEqual(self.post(c, "/t/strain/new", {"name": "x", "f_plasmid": "PL-99"}).status_code, 422)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual(len(self.store.records), 0)

    def test_record_file_is_stable_through_edit_roundtrip(self):
        rid = self.create("alice", name="n", genotype="g")
        path = self.store.path_of(rid, "strain")
        before = self.read(path).splitlines()
        c = self.client("alice")
        self.post(c, f"/r/{rid}/edit", {"name": "n", "f_genotype": "g", "version": self.version(rid), "notes": ""})
        after = self.read(path).splitlines()
        changed = [(a, b) for a, b in zip(before, after) if a != b]
        self.assertEqual(len(before), len(after))
        self.assertTrue(all(a.startswith("updated_at") for a, _ in changed), changed)  # an unchanged form changes nothing else


    def test_browser_crlf_is_normalised(self):
        rid = self.create("alice", name="n")
        c = self.client("alice")
        self.post(c, f"/r/{rid}/edit", {"name": "n", "version": self.version(rid), "notes": "line1\r\n\r\nline2\r\n", "f_genotype": "a\r\nb"})
        with open(self.store.path_of(rid, "strain"), newline="") as fh:
            raw = fh.read()
        self.assertNotIn("\r", raw)
        self.assertEqual(self.store.records[rid]["notes"], "line1\n\nline2")


class TestSearchAndSafety(Base):
    def test_fuzzy_and_exact_id(self):
        self.create("alice", name="MG1655 ybaL wt", genotype="wild type")
        c = self.client("bob")
        self.assertEqual(c.get("/?q=ST-1").status_code, 303)  # exact ID jumps
        self.create("alice", name="DH5alpha Bla reporter")  # must not win for the typo "ybla"
        r = c.get("/?q=ybla")  # typo: swapped letters (ybaL)
        self.assertEqual(r.status_code, 200)  # no surprise redirect for text queries
        self.assertIn("/r/ST-1", r.text)
        self.assertNotIn("/r/ST-2", r.text)

    def test_html_is_escaped_everywhere(self):
        rid = self.create("alice", name="<script>alert(1)</script>", genotype="<img src=x onerror=alert(1)>")
        c = self.client("alice")
        self.post(c, f"/r/{rid}/edit", {"name": "<script>alert(1)</script>", "f_genotype": "<b>x</b>", "version": self.version(rid),
                                        "notes": "hello <script>evil()</script> **bold** [x](javascript:alert(1))"})
        for path in (f"/r/{rid}", "/t/strain", f"/r/{rid}/edit", "/?q=script&archived=1"):
            page = c.get(path).text
            self.assertNotIn("<script>alert(1)", page, path)
            self.assertNotIn("<script>evil", page, path)
            self.assertNotIn('href="javascript:', page, path)

    def test_file_endpoint_blocks_traversal(self):
        os.makedirs(os.path.join(self.repo, "data", "files", "PL-1"))
        with open(os.path.join(self.repo, "data", "files", "PL-1", "a.gb"), "w") as fh:
            fh.write("LOCUS")
        c = self.client("alice")
        self.assertEqual(c.get("/files/PL-1/a.gb").status_code, 200)
        self.assertEqual(c.get("/files/..%2f..%2finst%2fusers.json").status_code, 404)
        self.assertEqual(c.get("/files/../../../etc/passwd").status_code, 404)
        self.assertEqual(self.client().get("/files/PL-1/a.gb").status_code, 303)  # login required

    def test_security_headers_present(self):
        h = self.client().get("/login").headers
        self.assertIn("default-src 'self'", h["content-security-policy"])
        self.assertEqual(h["x-frame-options"], "DENY")

    def test_users_file_never_contains_plain_passwords(self):
        raw = self.read(os.path.join(self.inst, "users.json"))
        self.assertNotIn(PW, raw)
        self.assertIn("scrypt$", raw)


if __name__ == "__main__":
    unittest.main()

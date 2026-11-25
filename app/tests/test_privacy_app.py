"""Personal data stays out of git (docs/design.md 16.2): records and commits name people only by user ID; names come from users.json;
erasing a person leaves a tombstone ID and "former member"."""
import json, os, re

from shelfblazer import auth
from test_app import PW, Base, git

NAMES = ("alice", "bob", "root", "Alice A", "Bob B", "Root Admin")


class TestNothingPersonalInGit(Base):
    def everything_in_git(self):
        """Every author, committer, message and file content in the whole history."""
        log = git(self.repo, "log", "--all", "--format=%an|%ae|%cn|%ce|%B")
        files = git(self.repo, "log", "--all", "-p", "--format=")
        return log + files

    def test_a_full_pass_through_the_ui_leaves_only_user_ids(self):
        alice, root = self.client("alice"), self.client("root")
        pl = self.create("alice", "plasmid", name="pA")
        rid = self.create("alice", name="s1", plasmid=pl)
        self.post(alice, f"/r/{rid}/edit", {"name": "s1b", "notes": "hello", "f_storage_place_new": "", "version": self.version(rid)})
        self.post(alice, f"/r/{rid}/archive")
        self.post(alice, f"/r/{rid}/archive")
        alice.post(f"/r/{rid}/files", data={"csrf_token": self.token(alice), "field": "attachments"}, files={"files": ("m.gb", b"LOCUS", "text/plain")})
        r = self.post(root, "/t/strain/bulk/apply", {"ids": rid, "action": "owner", "owner": self.users.get("bob")["uid"]})
        self.assertEqual(r.status_code, 200, r.text[:300])
        sha = git(self.repo, "rev-parse", "HEAD").strip()
        self.post(root, "/bulk/undo", {"sha": sha, "back": "/t/strain"})
        self.post(root, "/admin/types/strain/fields/add", {"label": "Extra", "type": "text"})
        blob = self.everything_in_git()
        for word in NAMES:
            self.assertNotRegex(blob, rf"(?<![\w-]){re.escape(word)}(?![\w-])", f"{word!r} reached git")
        for who in ("alice", "root"):
            self.assertIn(self.users.get(who)["uid"], blob)
        self.assertEqual(self.store.records[rid]["created_by"], self.users.get("alice")["uid"])

    def test_pages_show_names(self):
        rid = self.create("alice", name="named")
        c = self.client("bob")
        page = c.get(f"/r/{rid}").text
        self.assertIn("Alice A", page)
        self.assertRegex(page, r"create by Alice A")                     # the History line, resolved
        self.assertNotIn(self.users.get("alice")["uid"], page)
        listing = c.get("/t/strain").text
        self.assertIn("Alice A", listing)
        self.assertIn(f'value="{self.users.get("alice")["uid"]}"', listing)   # the owner filter uses IDs
        self.assertIn("Alice A", c.get("/").text)                         # recent activity

    def test_person_fields_take_a_username_and_store_the_id(self):
        root = self.client("root")
        self.post(root, "/admin/types/strain/fields/add", {"label": "Contact person", "type": "user"})
        key = next(f["key"] for f in self.store.schemas["strain"]["fields"] if f["type"] == "user")
        rid = self.create("alice", name="p", **{key: "bob"})
        self.assertEqual(self.store.records[rid]["fields"][key], self.users.get("bob")["uid"])
        self.assertIn('value="bob"', self.client("alice").get(f"/r/{rid}/edit").text)
        self.assertIn("Bob B", self.client("alice").get(f"/r/{rid}").text)
        self.assertEqual(self.post(self.client("alice"), "/t/strain/new", {"name": "x", f"f_{key}": "nobody"}).status_code, 422)

    def test_formula_owner_is_the_name(self):
        from shelfblazer import formula
        rid = self.create("alice", name="f")
        value, err = formula.evaluate("owner", self.store.records[rid], self.store)
        self.assertEqual((value, err), ("Alice A", None))


class TestErasure(Base):
    def test_erased_person_becomes_former_member_and_the_id_is_never_reissued(self):
        rid = self.create("bob", name="bobs")
        uid = self.users.get("bob")["uid"]
        self.assertEqual(self.users.erase("bob"), uid)
        self.assertIsNone(self.users.get("bob"))
        self.assertTrue(self.users.is_erased(uid))
        self.assertIn("former member", self.client("alice").get(f"/r/{rid}").text)
        self.assertNotIn("Bob B", json.dumps(self.users._load()))
        self.assertEqual(self.post(self.client(), "/login", {"username": "bob", "password": PW}, anon=True).status_code, 401)
        self.users.create("bob", "Another Bob", "user", PW)                # the username may come back, the ID does not
        self.assertNotEqual(self.users.get("bob")["uid"], uid)
        self.assertNotEqual(self.store.records[rid]["created_by"], self.users.get("bob")["uid"])

    def test_last_admin_cannot_be_erased(self):
        with self.assertRaises(ValueError):
            self.users.erase("root")
        self.assertIsNotNone(self.users.get("root"))

    def test_erase_user_command(self):
        from shelfblazer_app import cli
        run = lambda *args: cli.main(["--instance", self.inst, "erase-user", "--data", self.repo, *args])
        with self.assertRaises(SystemExit):
            run("alice")                                                    # needs --yes
        self.assertIsNotNone(self.users.get("alice"))
        run("alice", "--yes")
        self.assertIsNone(self.users.get("alice"))

    def test_erase_user_refuses_while_personal_records_are_left(self):
        from shelfblazer_app import cli
        run = lambda *args: cli.main(["--instance", self.inst, "erase-user", "--data", self.repo, *args])
        from test_app import STRAIN
        self.store.save_schema("strain", dict(STRAIN, permissions={"personal": "all"}), self.users.get("root"))
        self.store.create("strain", self.users.get("alice"), "mine", {}, personal=True)
        with self.assertRaises(SystemExit) as e:
            run("alice", "--yes")
        self.assertIn("1 personal strain", str(e.exception))
        self.assertIsNotNone(self.users.get("alice"))
        run("alice", "--yes", "--leave-personal")                           # the deliberate way
        self.assertIsNone(self.users.get("alice"))


class TestUpgradeAndIds(Base):
    def test_old_users_file_gets_ids_and_keeps_passwords(self):
        path = os.path.join(self.inst, "old.json")
        with open(path, "w") as fh:
            json.dump({"users": {"zed": {"name": "Zed", "role": "admin", "active": True, "pw": auth.hash_password(PW), "created": "", "legacy": {}}},
                       "reset": {}, "signup_passphrase": None}, fh)
        u = auth.Users(path)
        uid = u.get("zed")["uid"]
        self.assertRegex(uid, auth.UID_RE)
        self.assertIsNotNone(u.authenticate("zed", PW, "1.1.1.1")[0])
        self.assertEqual(auth.Users(path).get("zed")["uid"], uid)          # stable
        self.assertEqual(u.by_uid(uid)["username"], "zed")
        self.assertEqual(u.resolve("zed")["uid"], uid)
        self.assertEqual(u.resolve(uid)["username"], "zed")

    def test_ids_are_unique_and_never_look_like_usernames(self):
        uids = {self.users.get(n)["uid"] for n in ("alice", "bob", "root")}
        self.assertEqual(len(uids), 3)
        for uid in uids:
            self.assertRegex(uid, auth.UID_RE)
            self.assertIsNone(auth.USERNAME_RE.match(uid))

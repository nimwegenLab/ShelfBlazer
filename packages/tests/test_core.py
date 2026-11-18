"""Core library tests (no web code). Run: .venv/bin/python -m unittest discover -s packages/tests -v"""
import json, os, subprocess, tempfile, unittest

from helpers import ALICE, BOB, ROOT, purge
from shelfblazer import auth, markdown, perms
from shelfblazer.search import Index
from shelfblazer.store import (Conflict, Forbidden, Store, ValidationError, parse_record, serialize_record,
                               unix_newlines, version_of)

auth.SCRYPT.update(n=2**10)

SCHEMA = {"name": "Strain", "prefix": "ST", "list_columns": ["id", "name"], "fields": [
    {"key": "name", "label": "Name", "type": "text", "required": True},
    {"key": "genotype", "label": "Genotype", "type": "text"},
    {"key": "n", "label": "Count", "type": "number"},
    {"key": "date", "label": "Date", "type": "date"},
    {"key": "tags", "label": "Tags", "type": "multiselect", "options": ["a", "b"]},
    {"key": "ok", "label": "OK", "type": "bool"}]}


class TestPerms(unittest.TestCase):
    def test_matrix(self):
        rec = {"created_by": "alice"}
        self.assertTrue(perms.can_edit(ALICE, rec, SCHEMA))
        self.assertFalse(perms.can_edit(BOB, rec, SCHEMA))
        self.assertTrue(perms.can_edit(ROOT, rec, SCHEMA))
        self.assertFalse(perms.can_edit(dict(ALICE, active=False), rec, SCHEMA))
        self.assertFalse(perms.can_edit(None, rec, SCHEMA))
        self.assertFalse(perms.can_manage_users(ALICE))
        self.assertTrue(perms.can_manage_users(ROOT))
        self.assertFalse(perms.is_admin(dict(ROOT, active=False)))


class TestAuth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.users = auth.Users(os.path.join(self.tmp.name, "users.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_hash_roundtrip_and_rejects(self):
        h = auth.hash_password("a long enough pass")
        self.assertTrue(auth.verify_password("a long enough pass", h))
        self.assertFalse(auth.verify_password("wrong", h))
        self.assertFalse(auth.verify_password("x", None))
        self.assertNotEqual(h, auth.hash_password("a long enough pass"))  # salted

    def test_password_length_boundary(self):
        self.assertEqual(auth.MIN_PASSWORD_LENGTH, 8)
        self.assertIsNotNone(auth.check_password_strength("a" * 7))
        self.assertIsNone(auth.check_password_strength("a" * 8))

    def test_username_rules_and_last_admin(self):
        with self.assertRaises(ValueError):
            self.users.create("Bad Name!", "x")
        self.users.create("root", "Root", "admin", "a long enough pass")
        with self.assertRaises(ValueError):
            self.users.update("root", role="user")
        with self.assertRaises(ValueError):
            self.users.create("root", "again")

    def test_reset_token_expires_and_is_single_use(self):
        self.users.create("root", "Root", "admin", "a long enough pass")
        self.users.create("erin", "Erin")
        t = self.users.make_reset_token("erin")
        self.assertEqual(self.users.reset_user(t), "erin")
        self.users.set_password("erin", "another long pass")
        self.assertIsNone(self.users.reset_user(t))
        self.assertIsNone(self.users.reset_user("garbage"))

    def test_lockout_window(self):
        self.users.create("root", "Root", "admin", "a long enough pass")
        for _ in range(auth.MAX_FAILS):
            self.assertIsNone(self.users.authenticate("root", "no", "1.2.3.4")[0])
        user, err = self.users.authenticate("root", "a long enough pass", "1.2.3.4")
        self.assertIsNone(user)
        self.assertIn("Too many", err)


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(self.repo, "config", "types"))
        os.makedirs(os.path.join(self.repo, "data"))
        with open(os.path.join(self.repo, "config", "types", "strain.yaml"), "w") as fh:
            json.dump(SCHEMA, fh)
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True)
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"], check=True)
        self.store = Store(self.repo)

    def tearDown(self):
        self.tmp.cleanup()

    def test_serialize_parse_roundtrip(self):
        rid = self.store.create("strain", ALICE, "DH10B", {"genotype": "wt", "n": "3", "tags": ["a"], "ok": True}, "note\nline2")
        path = self.store.path_of(rid, "strain")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        rec = parse_record(text)
        self.assertEqual(serialize_record(rec, self.store.order_of("strain")), text)
        self.assertEqual(rec["fields"], {"genotype": "wt", "n": 3, "tags": ["a"], "ok": True})
        # a fresh Store reading the same files sees the same record
        again = Store(self.repo).records[rid]
        self.assertEqual((again["name"], again["notes"], again["created_by"]), ("DH10B", "note\nline2", "alice"))

    def test_permissions_enforced_in_store(self):
        rid = self.store.create("strain", ALICE, "mine", {})
        v = self.store.records[rid]["_version"]
        with self.assertRaises(Forbidden):
            self.store.update(rid, BOB, "x", {}, "", v)
        with self.assertRaises(Forbidden):
            self.store.set_archived(rid, BOB, True)
        self.store.update(rid, ROOT, "by admin", {}, "", v)
        self.assertEqual(self.store.records[rid]["created_by"], "alice")

    def test_conflict_and_validation(self):
        rid = self.store.create("strain", ALICE, "a", {})
        v = self.store.records[rid]["_version"]
        self.store.update(rid, ALICE, "b", {}, "n1", v)
        with self.assertRaises(Conflict):
            self.store.update(rid, ALICE, "c", {}, "n2", v)
        for bad in ({"n": "abc"}, {"date": "05/11/2026"}, {"tags": ["zzz"]}):
            with self.assertRaises(ValidationError):
                self.store.create("strain", ALICE, "x", bad)
        with self.assertRaises(ValidationError):
            self.store.create("strain", ALICE, "  ", {})

    def test_ids_continue_and_are_not_reused(self):
        ids = [self.store.create("strain", ALICE, f"s{i}", {}) for i in range(3)]
        self.assertEqual(ids, ["ST-1", "ST-2", "ST-3"])
        purge(self.store, "ST-3", ALICE)
        self.assertEqual(self.store.create("strain", ALICE, "s4", {}), "ST-4")

    def test_type_colour_names_are_validated(self):
        from shelfblazer.store import TYPE_COLORS
        self.assertEqual(len(TYPE_COLORS), 8)
        path = os.path.join(self.repo, "config", "types", "strain.yaml")
        for color, ok in (("teal", True), ("gold", True), ("#ff0000", False), ("magenta", False), ("", False)):
            with open(path, "w") as fh:
                json.dump(dict(SCHEMA, color=color), fh)
            store = Store(self.repo)
            self.assertEqual(store.schema_warnings == [], ok, color)
            if not ok:
                self.assertIn("ignored", store.schema_warnings[0])
        with open(path, "w") as fh:
            json.dump(SCHEMA, fh)                      # no "color" key at all is fine
        self.assertEqual(Store(self.repo).schema_warnings, [])

    def test_drop_field_removes_it_from_the_type_and_every_record_in_one_commit(self):
        a = self.store.create("strain", ALICE, "a", {"genotype": "g1", "n": "3"})
        b = self.store.create("strain", BOB, "b", {"genotype": "g2"})
        c = self.store.create("strain", BOB, "c", {"n": "5"})
        head_before = self.store.git("rev-list", "--count", "HEAD").strip()
        self.assertEqual(self.store.drop_field("strain", "genotype"), 2)
        self.assertEqual(int(self.store.git("rev-list", "--count", "HEAD")), int(head_before) + 1)     # exactly one commit
        self.assertIn("remove field 'genotype' (2 records)", self.store.git("log", "-1", "--format=%s"))
        self.assertEqual(self.store.git("status", "--porcelain").strip(), "")
        with open(os.path.join(self.repo, "config", "types", "strain.yaml")) as fh:
            schema = json.load(fh)
        self.assertNotIn("genotype", [f["key"] for f in schema["fields"]])
        self.assertEqual([f["key"] for f in schema["fields"]][:2], ["name", "n"])                      # others keep their order
        fresh = Store(self.repo)                                                                      # what is on disk, reloaded
        for rid, expect in ((a, {"n": 3}), (b, {}), (c, {"n": 5})):
            self.assertEqual(fresh.records[rid]["fields"], expect)
            self.assertEqual(fresh.records[rid]["created_by"], self.store.records[rid]["created_by"])   # ownership untouched
        self.assertEqual(fresh.records[a]["updated_at"], self.store.records[a]["updated_at"])         # not presented as a user edit
        old = self.store.git("show", f"HEAD~1:data/strain/{a}.md")
        self.assertIn('genotype: "g1"', old)                                                         # the values stay in git history
        self.store.update(a, ALICE, "a2", {}, "", self.store.records[a]["_version"])                   # the store keeps working
        self.assertEqual(self.store.drop_field("strain", "n"), 2)

    def test_drop_field_refuses_bad_requests_and_changes_nothing(self):
        rid = self.store.create("strain", ALICE, "a", {"genotype": "g"})
        head = self.store.git("rev-parse", "HEAD")
        for args, exc in ((("strain", "name"), ValidationError), (("strain", "nope"), ValidationError), (("ghost", "x"), Exception)):
            with self.assertRaises(exc):
                self.store.drop_field(*args)
        self.assertEqual(self.store.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.store.records[rid]["fields"], {"genotype": "g"})
        self.assertEqual(self.store.drop_field("strain", "genotype"), 1)
        with self.assertRaises(ValidationError):
            self.store.drop_field("strain", "genotype")                                               # already gone

    def test_drop_field_removes_the_column_from_the_list_view(self):
        path = os.path.join(self.repo, "config", "types", "strain.yaml")
        with open(path, "w") as fh:
            json.dump(dict(SCHEMA, list_columns=["id", "name", "genotype"]), fh)
        store = Store(self.repo)
        store.drop_field("strain", "genotype")
        self.assertEqual(store.schemas["strain"]["list_columns"], ["id", "name"])

    def test_field_display_setting_is_validated(self):
        from shelfblazer.store import FIELD_DISPLAYS
        self.assertEqual(FIELD_DISPLAYS, ("short", "full", "outdated"))
        path = os.path.join(self.repo, "config", "types", "strain.yaml")
        for display, ok in (("short", True), ("full", True), ("outdated", True), ("Outdated", False), ("old", False), ("", False)):
            schema = json.loads(json.dumps(SCHEMA))
            schema["fields"][1]["display"] = display
            with open(path, "w") as fh:
                json.dump(schema, fh)
            warnings = Store(self.repo).schema_warnings
            self.assertEqual(warnings == [], ok, display)
            if not ok:
                self.assertIn("unknown display", warnings[0])

    def test_crlf_normalised(self):
        self.assertEqual(unix_newlines("a\r\nb\rc"), "a\nb\nc")
        rid = self.store.create("strain", ALICE, "x", {"genotype": "a\r\nb"}, "l1\r\n\r\nl2\r\n")
        self.assertEqual(self.store.records[rid]["notes"], "l1\n\nl2")

    def test_recover_commits_dirty_tree(self):
        rid = self.store.create("strain", ALICE, "x", {})
        with open(self.store.path_of(rid, "strain"), "a") as fh:
            fh.write("\nstray edit\n")
        self.store.recover()
        self.assertEqual(self.store.git("status", "--porcelain").strip(), "")
        self.assertIn("recover", self.store.git("log", "-1", "--format=%s"))

    def test_search_can_be_limited_to_several_types(self):
        a = self.store.create("strain", ALICE, "pUA66 strain", {})
        idx = Index(self.store, lambda n: None)
        self.assertEqual([d["id"] for d in idx.search("pua66", types={"strain"})], [a])
        self.assertEqual(idx.search("pua66", types={"plasmid"}), [])
        self.assertEqual([d["id"] for d in idx.search("pua66", types={"plasmid", "strain"})], [a])
        self.assertEqual([d["id"] for d in idx.search("pua66", types=None)], [a])

    def test_search_typo_and_archived(self):
        a = self.store.create("strain", ALICE, "MG1655 ybaL wt", {})
        self.store.create("strain", ALICE, "DH5alpha Bla reporter", {})
        idx = Index(self.store, lambda n: None)
        self.assertEqual([d["id"] for d in idx.search("ybla")], [a])
        self.assertEqual([d["id"] for d in idx.search("ST-1")][0], a)
        self.store.set_archived(a, ALICE, True)
        self.assertEqual(idx.search("ybaL"), [])
        self.assertEqual([d["id"] for d in idx.search("ybaL", include_archived=True)], [a])


class TestMarkdown(unittest.TestCase):
    def test_escapes_and_links(self):
        out = markdown.render("<script>x</script> **b** [l](javascript:alert(1)) [ok](https://e.org) ST-1 ST-9", {"ST-1": {}})
        self.assertNotIn("<script>", out)
        self.assertNotIn('href="javascript', out)
        self.assertIn("<strong>b</strong>", out)
        self.assertIn('href="https://e.org"', out)
        self.assertIn('<a class="rid" href="/r/ST-1">ST-1</a>', out)   # record IDs are marked for monospace display
        self.assertNotIn('href="/r/ST-9"', out)  # unknown IDs are not linked

    def test_bare_links_with_ampersands_are_escaped_once(self):
        out = markdown.render('see https://e.org/q?a=1&b=2 and https://e.org/"x', {})
        self.assertIn('href="https://e.org/q?a=1&amp;b=2"', out)
        self.assertNotIn("&amp;amp;", out)
        self.assertNotIn('"x"', out)


if __name__ == "__main__":
    unittest.main()

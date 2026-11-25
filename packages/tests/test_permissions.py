"""Per-type permissions (roles only): the pure rule matrix and the store enforcing it."""
import copy, json, os, subprocess, tempfile, unittest

from shelfblazer import formula, perms, schema_edit as se
from shelfblazer.search import Index
from shelfblazer.store import Forbidden, NotFound, Store, ValidationError
from helpers import ALICE, BOB, ROOT

GONE = dict(BOB, active=False)
USERS = {u["username"]: u for u in (ROOT, ALICE, BOB)}


def schema(name, prefix, permissions=None, extra=()):
    s = {"name": name, "prefix": prefix, "list_columns": ["id", "name"],
         "fields": [{"key": "name", "label": "Name", "type": "text", "required": True}, *extra]}
    if permissions:
        s["permissions"] = permissions
    return s


class TestMatrix(unittest.TestCase):
    rec = {"created_by": "alice"}

    def row(self, permissions):
        s = schema("T", "TT", permissions)
        out = {}
        for who, u in (("alice", ALICE), ("bob", BOB), ("root", ROOT), ("gone", GONE), ("nobody", None)):
            out[who] = (perms.can_read_type(u, s), perms.can_create(u, s), perms.can_edit(u, self.rec, s))
        return out

    def test_defaults_keep_the_original_rule(self):
        for p in (None, {}, {"read": "all", "create": "all", "edit": "owner"}):
            r = self.row(p)
            self.assertEqual(r["alice"], (True, True, True))                       # creator
            self.assertEqual(r["bob"], (True, True, False))                        # reads and creates, cannot edit another's
            self.assertEqual(r["root"], (True, True, True))
            self.assertEqual(r["gone"], (False,) * 3)
            self.assertEqual(r["nobody"], (False,) * 3)

    def test_edit_admin_means_only_administrators_change(self):
        r = self.row({"edit": "admin"})
        self.assertEqual(r["alice"], (True, True, False))                          # even the creator
        self.assertEqual(r["bob"], (True, True, False))
        self.assertEqual(r["root"], (True, True, True))

    def test_edit_all_opens_changes(self):
        r = self.row({"edit": "all"})
        self.assertEqual(r["bob"], (True, True, True))
        self.assertEqual(r["alice"], (True, True, True))
        self.assertEqual(r["gone"], (False,) * 3)

    def test_archived_records_are_for_their_creator_and_administrators(self):
        self.assertEqual([perms.can_see_archived(u, self.rec) for u in (ALICE, BOB, ROOT, GONE, None)], [True, False, True, False, False])

    def test_create_admin(self):
        r = self.row({"create": "admin"})
        self.assertEqual((r["alice"][1], r["bob"][1], r["root"][1]), (False, False, True))
        self.assertTrue(r["alice"][2])                                              # existing own records still editable

    def test_read_admin_hides_everything_from_ordinary_users(self):
        r = self.row({"read": "admin", "edit": "all", "create": "all"})
        self.assertEqual(r["alice"], (False,) * 3)                                  # not even the creator, not even with edit=all
        self.assertEqual(r["bob"], (False,) * 3)
        self.assertEqual(r["root"], (True, True, True))

    def test_unknown_values_fall_back_to_the_safe_default(self):
        self.assertEqual(perms.setting({"permissions": {"read": "everybody"}}, "read"), "all")
        self.assertEqual(perms.setting({"permissions": {"edit": "friends"}}, "edit"), "owner")
        self.assertEqual(perms.setting(None, "create"), "all")

    def test_restricted(self):
        self.assertFalse(perms.restricted(schema("T", "TT")))
        self.assertFalse(perms.restricted(schema("T", "TT", {"edit": "owner"})))
        self.assertTrue(perms.restricted(schema("T", "TT", {"create": "admin"})))

    def test_validation_accepts_exactly_the_documented_values(self):
        ok = lambda p: se.validate_schema("t1", schema("T", "TT", p), {}, old=None)
        self.assertEqual(ok({"read": "admin", "create": "admin", "edit": "all"}), [])
        for bad in ({"read": "owner"}, {"create": "all2"}, {"edit": "nobody"}, {"delete": "all"}):
            self.assertTrue(ok(bad), bad)

    def test_settings_roundtrip_drops_defaults_to_nothing(self):
        s = se.update_settings(schema("T", "TT"), permissions={"read": "admin", "create": "", "edit": "all"})
        self.assertEqual(s["permissions"], {"read": "admin", "edit": "all"})
        s = se.update_settings(s, permissions={"read": "", "create": "", "edit": ""})
        self.assertNotIn("permissions", s)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(self.repo, "config", "types"))
        os.makedirs(os.path.join(self.repo, "data"))
        self.defs = {"open": schema("Open", "OP", extra=[{"key": "link", "label": "Link", "type": "ref", "to": "*", "multiple": True},
                                                         {"key": "kind", "label": "Kind", "type": "text"}]),
                     "secret": schema("Secret", "SE", {"read": "admin"}),
                     "locked": schema("Locked", "LK", {"edit": "admin"}),
                     "shared": schema("Shared", "SH", {"edit": "all"}, extra=[{"key": "files", "label": "Files", "type": "file", "multiple": True}]),
                     "staff": schema("Staff", "SF", {"create": "admin"})}
        self.defs["open"]["fields"].append({"key": "how_many", "label": "How many", "type": "computed",
                                            "expr": "count(refs('link')) + count(referenced_by('open'))"})
        for slug, s in self.defs.items():
            with open(os.path.join(self.repo, "config", "types", slug + ".yaml"), "w") as fh:
                json.dump(s, fh)
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True)
        subprocess.run(["git", "-C", self.repo, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "i"], check=True)
        self.store = Store(self.repo, users_lookup=USERS.get)
        self.index = Index(self.store, USERS.get)

    def tearDown(self):
        self.tmp.cleanup()


class TestStoreEnforcement(StoreCase):
    def test_create_rules(self):
        self.assertEqual(self.store.create("open", BOB, "x", {}), "OP-1")
        self.assertEqual(self.store.create("shared", BOB, "x", {}), "SH-1")
        self.assertEqual(self.store.create("locked", BOB, "x", {}), "LK-1")           # create is open, only editing is closed
        with self.assertRaises(Forbidden):
            self.store.create("staff", BOB, "x", {})
        with self.assertRaises(NotFound):                                              # a hidden type does not even admit to existing
            self.store.create("secret", BOB, "x", {})
        self.assertEqual(self.store.create("staff", ROOT, "x", {}), "SF-1")
        self.assertEqual(self.store.create("secret", ROOT, "x", {}), "SE-1")
        with self.assertRaises(Forbidden):
            self.store.create("open", GONE, "x", {})

    def test_hidden_records_look_missing_for_every_operation(self):
        rid = self.store.create("secret", ROOT, "plan", {})
        for call in (lambda: self.store.update(rid, ALICE, "n", {}, "", "v"), lambda: self.store.set_archived(rid, ALICE, True),
                     lambda: self.store.add_attachment(rid, ALICE, "a.txt", b"x"),
                     lambda: self.store.remove_attachment(rid, ALICE, "a.txt")):
            with self.assertRaises(NotFound):
                call()
        self.assertIsNone(self.store.readable(ALICE, rid))
        self.assertIsNotNone(self.store.readable(ROOT, rid))
        self.assertIn(rid, self.store.records)

    def test_edit_admin_blocks_the_creator_too(self):
        rid = self.store.create("locked", ALICE, "mine", {})
        v = self.store.records[rid]["_version"]
        for call in (lambda: self.store.update(rid, ALICE, "n", {}, "", v), lambda: self.store.set_archived(rid, ALICE, True),
                     lambda: self.store.add_attachment(rid, ALICE, "a.txt", b"x")):
            with self.assertRaises(Forbidden):
                call()
        self.store.update(rid, ROOT, "renamed", {}, "", v)
        self.assertEqual(self.store.records[rid]["name"], "renamed")

    def test_edit_all_lets_anyone_change_and_archive(self):
        rid = self.store.create("shared", ALICE, "wiki", {})
        v = self.store.records[rid]["_version"]
        self.store.update(rid, BOB, "bobs edit", {}, "", v)
        self.store.add_attachment(rid, BOB, "a.txt", b"hello")
        self.store.remove_attachment(rid, BOB, "a.txt")
        self.store.set_archived(rid, BOB, True)
        self.assertIsNone(self.store.readable(BOB, rid))                              # archived: now only for the creator and administrators
        self.assertIsNotNone(self.store.readable(ALICE, rid))
        self.store.set_archived(rid, ALICE, False)
        self.assertIsNotNone(self.store.readable(BOB, rid))

    def test_archived_records_are_hidden_from_everybody_but_creator_and_admins(self):
        rid = self.store.create("open", ALICE, "mine", {})
        other = self.store.create("open", BOB, "bobs", {})
        idx = Index(self.store, lambda n: None)
        self.store.set_archived(rid, ALICE, True)
        self.assertIsNotNone(self.store.readable(ALICE, rid))
        self.assertIsNotNone(self.store.readable(ROOT, rid))
        self.assertIsNone(self.store.readable(BOB, rid))
        self.assertIn(rid, self.store.visible_records(ALICE))
        self.assertIn(rid, self.store.visible_records(ROOT))
        self.assertNotIn(rid, self.store.visible_records(BOB))
        self.assertIn(other, self.store.visible_records(BOB))
        with self.assertRaises(NotFound):                                              # looks missing, like a hidden type
            self.store.set_archived(rid, BOB, False)
        with self.assertRaises(ValidationError):                                       # and cannot be linked to
            self.store.create("open", BOB, "o", {"link": [rid]})
        self.store.set_archived(other, BOB, True)
        ids = lambda q, **kw: {d["id"] for d in idx.search(q, include_archived=True, **kw)}
        self.assertEqual(ids("mine", visible=self.store.visibility(BOB)), set())        # someone else's archived record: not found
        self.assertEqual(ids("bobs", visible=self.store.visibility(BOB)), {other})      # their own: found
        self.assertEqual(ids("mine", visible=self.store.visibility(ROOT)), {rid})       # administrators: everything
        self.assertEqual(ids("mine"), {rid})                                            # no rule given (internal use): everything
        self.assertEqual(idx.search("mine"), [])                                        # archived stay out unless asked for

    def test_formulas_do_not_count_archived_records_for_others(self):
        sid = self.store.create("open", ROOT, "s", {})
        oid = self.store.create("open", ROOT, "o", {"link": [sid]})
        fdef = next(f for f in self.store.schemas["open"]["fields"] if f["key"] == "how_many")
        self.store.set_archived(sid, ROOT, True)
        self.assertEqual(self.store.compute(self.store.records[oid], fdef, ROOT)[0], 1)
        self.assertEqual(self.store.compute(self.store.records[oid], fdef, ALICE)[0], 0)

    def test_default_type_unchanged(self):
        rid = self.store.create("open", ALICE, "a", {})
        with self.assertRaises(Forbidden):
            self.store.set_archived(rid, BOB, True)
        self.store.set_archived(rid, ALICE, True)

    def test_bulk(self):
        a = [self.store.create("locked", ALICE, f"l{i}", {}) for i in range(2)]
        r = self.store.bulk_apply(ALICE, "locked", a, "archive")
        self.assertEqual((r["changed"], [x[0] for x in r["skipped"]]), ([], a))
        self.assertTrue(all(reason == "not allowed" for _, reason in r["skipped"]))
        r = self.store.bulk_apply(ROOT, "locked", a, "archive")
        self.assertEqual(len(r["changed"]), 2)
        s = [self.store.create("shared", ALICE, f"s{i}", {}) for i in range(2)]
        r = self.store.bulk_apply(BOB, "shared", s, "archive")
        self.assertEqual(len(r["changed"]), 2)                                         # edit = all
        with self.assertRaises(NotFound):
            self.store.bulk_apply(BOB, "secret", [], "archive")
        with self.assertRaises(NotFound):
            self.store.bulk_apply(ALICE, "nope", [], "archive")
        o = self.store.create("open", ALICE, "o", {})
        r = self.store.bulk_apply(BOB, "open", [o], "archive")
        self.assertEqual(r["skipped"], [(o, "not yours")])

    def test_links_to_hidden_records_cannot_be_created_but_existing_ones_survive(self):
        sid = self.store.create("secret", ROOT, "s", {})
        with self.assertRaises(ValidationError):
            self.store.create("open", ALICE, "o", {"link": [sid]})
        oid = self.store.create("open", ROOT, "o", {"link": [sid]})                         # an administrator may
        self.store.save_schema("open", se.update_settings(self.store.schemas["open"], permissions={"edit": "all"}), ROOT)
        v = self.store.records[oid]["_version"]
        self.store.update(oid, ALICE, "renamed", {"link": f"{sid}"}, "", v)                 # unchanged link: kept
        self.assertEqual(self.store.records[oid]["fields"]["link"], [sid])
        other = self.store.create("open", ALICE, "p", {})
        r = self.store.bulk_apply(ALICE, "open", [other], "add", "link", sid)
        self.assertEqual(r["changed"], [])
        self.assertIn(other, r["errors"])

    def test_visibility_helpers(self):
        sid = self.store.create("secret", ROOT, "s", {})
        oid = self.store.create("open", ALICE, "o", {})
        self.assertEqual(self.store.readable_types(ALICE), {"open", "locked", "shared", "staff"})
        self.assertEqual(self.store.readable_types(ROOT), set(self.store.schemas))
        self.assertEqual(self.store.readable_types(None), set())
        self.assertNotIn(sid, self.store.visible_records(ALICE))
        self.assertIn(oid, self.store.visible_records(ALICE))
        self.assertIn(sid, self.store.visible_records(ROOT))
        self.assertTrue(self.store.can_create(ALICE, "open") and not self.store.can_create(ALICE, "staff") and not self.store.can_create(ALICE, "secret"))
        self.assertFalse(self.store.can_create(ALICE, "nope"))

    def test_administrators_get_the_whole_mapping_without_a_copy(self):
        self.assertIs(self.store.visible_records(ROOT), self.store.records)

    def test_formulas_never_reveal_hidden_records(self):
        sid = self.store.create("secret", ROOT, "s", {})
        oid = self.store.create("open", ROOT, "o", {"link": [sid]})
        fdef = next(f for f in self.store.schemas["open"]["fields"] if f["key"] == "how_many")
        self.assertEqual(self.store.compute(self.store.records[oid], fdef, ROOT)[0], 1)
        self.assertEqual(self.store.compute(self.store.records[oid], fdef, ALICE)[0], 0)

    def test_search_by_type_filter_excludes_hidden(self):
        sid = self.store.create("secret", ROOT, "unicorn", {})
        self.store.create("open", ALICE, "unicorn too", {})
        everything = {d["id"] for d in self.index.search("unicorn")}
        allowed = {d["id"] for d in self.index.search("unicorn", types=self.store.readable_types(ALICE))}
        self.assertIn(sid, everything)
        self.assertNotIn(sid, allowed)
        self.assertEqual(len(allowed), 1)


class TestUndoAndSchema(StoreCase):
    def test_changing_permissions_is_one_commit_and_takes_effect_at_once(self):
        rid = self.store.create("open", ALICE, "o", {})
        self.assertIsNotNone(self.store.readable(BOB, rid))
        before = int(self.store.git("rev-list", "--count", "HEAD"))
        self.store.save_schema("open", se.update_settings(self.store.schemas["open"], permissions={"read": "admin"}), ROOT, message="type open: settings")
        self.assertEqual(int(self.store.git("rev-list", "--count", "HEAD")), before + 1)
        self.assertIsNone(self.store.readable(BOB, rid))
        with open(os.path.join(self.repo, "config", "types", "open.yaml")) as fh:
            self.assertEqual(json.load(fh)["permissions"], {"read": "admin"})
        with self.assertRaises(Forbidden):
            self.store.save_schema("open", self.store.schemas["open"], ALICE)

    def test_a_reloaded_store_keeps_the_permissions(self):
        self.store.reload()
        self.assertEqual(self.store.schemas["secret"]["permissions"], {"read": "admin"})
        self.assertIsNone(self.store.readable(ALICE, self.store.create("secret", ROOT, "s", {})))



class OneRule(unittest.TestCase):
    """Reading a record is decided by perms.can_see_record only: the archive rule used anywhere else would miss a change of the rule."""
    def test_the_archive_rule_is_used_only_by_the_visibility_rule(self):
        import glob, os
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        files = glob.glob(os.path.join(root, "packages", "shelfblazer", "*.py")) + glob.glob(os.path.join(root, "app", "shelfblazer_app", "*.py"))
        users = [os.path.relpath(f, root) for f in files if os.path.basename(f) != "perms.py" and "can_see_archived" in open(f, encoding="utf-8").read()]
        self.assertEqual(users, [])

if __name__ == "__main__":
    unittest.main()

"""Bulk edit in the core: permissions, validation, one commit, delete rules, dry run, undo."""
import os, unittest

from shelfblazer.search import Index
from shelfblazer.store import Conflict, Forbidden, NotFound, Store, ValidationError

from helpers import ALICE, BOB, ROOT, Base


class TestBulkApply(Base):
    def test_set_changes_own_records_skips_others_in_one_commit(self):
        before = self.commits()
        res = self.store.bulk_apply(ALICE, "strain", self.a + self.b, "set", "genotype", "new")
        self.assertEqual(res["changed"], self.a)
        self.assertEqual([r for r, why in res["skipped"]], self.b)
        self.assertEqual({why for _, why in res["skipped"]}, {"not yours"})
        self.assertEqual(self.commits(), before + 1)
        self.assertIn("bulk: set genotype (3 strain records)", self.head_message())
        self.assertIn(self.a[0], self.store.git("log", "-1", "--format=%b"))
        self.assertEqual(self.store.git("log", "-1", "--format=%an <%ae>").strip(), "alice <alice@shelfblazer.invalid>")   # the user ID only, never the name
        for rid in self.a:
            self.assertEqual(self.store.records[rid]["fields"]["genotype"], "new")
        for rid in self.b:
            self.assertEqual(self.store.records[rid]["fields"]["genotype"], "g")          # untouched
        self.assertEqual(self.store.git("status", "--porcelain").strip(), "")
        fresh = Store(self.repo)
        self.assertEqual(fresh.records[self.a[1]]["fields"]["genotype"], "new")             # and on disk
        self.assertEqual(fresh.records[self.a[1]]["created_by"], "alice")

    def test_admin_can_edit_everything(self):
        res = self.store.bulk_apply(ROOT, "strain", self.a + self.b, "set", "genotype", "z")
        self.assertEqual(len(res["changed"]), 5)
        self.assertEqual(res["skipped"], [])

    def test_unchanged_unknown_and_other_type_are_reported_not_applied(self):
        before = self.commits()
        res = self.store.bulk_apply(ALICE, "strain", self.a + ["ST-999", self.p], "set", "genotype", "g")   # already "g"
        self.assertEqual(res["unchanged"], self.a)
        self.assertEqual(dict(res["skipped"]), {"ST-999": "does not exist", self.p: "is not a strain"})
        self.assertEqual(res["changed"], [])
        self.assertIsNone(res["sha"])
        self.assertEqual(self.commits(), before)                                           # nothing to write, nothing committed

    def test_invalid_value_is_reported_per_record_and_nothing_is_half_applied(self):
        before = self.commits()
        res = self.store.bulk_apply(ALICE, "strain", self.a, "set", "tags", ["zzz"])
        self.assertEqual(res["changed"], [])
        self.assertEqual(set(res["errors"]), set(self.a))
        self.assertEqual(self.commits(), before)
        self.assertEqual(self.store.git("status", "--porcelain").strip(), "")

    def test_clearing_and_bool(self):
        self.store.bulk_apply(ALICE, "strain", self.a, "set", "genotype", "")
        self.assertNotIn("genotype", self.store.records[self.a[0]]["fields"])
        self.store.bulk_apply(ALICE, "strain", self.a, "set", "ok", True)
        self.assertIs(self.store.records[self.a[0]]["fields"]["ok"], True)

    def test_add_and_remove_options_on_multiselect_and_ref(self):
        self.store.bulk_apply(ALICE, "strain", self.a, "set", "tags", ["a"])
        res = self.store.bulk_apply(ALICE, "strain", self.a, "add", "tags", "b")
        self.assertEqual(res["changed"], self.a)
        self.assertEqual(self.store.records[self.a[0]]["fields"]["tags"], ["a", "b"])
        self.assertEqual(self.store.bulk_apply(ALICE, "strain", self.a, "add", "tags", "b")["unchanged"], self.a)   # already there
        self.store.bulk_apply(ALICE, "strain", self.a, "remove", "tags", "a")
        self.assertEqual(self.store.records[self.a[0]]["fields"]["tags"], ["b"])
        self.store.bulk_apply(ALICE, "strain", self.a, "add", "plasmid", self.p)
        self.assertEqual(self.store.records[self.a[2]]["fields"]["plasmid"], [self.p])
        bad = self.store.bulk_apply(ALICE, "strain", self.a, "add", "plasmid", "PL-999")
        self.assertEqual(set(bad["errors"]), set(self.a))
        with self.assertRaises(ValidationError):
            self.store.bulk_apply(ALICE, "strain", self.a, "add", "genotype", "x")           # a text field has no options

    def test_fields_that_cannot_be_bulk_edited(self):
        for key in ("name", "legacy_owner", "hiddenf", "calc", "nope"):
            with self.assertRaises(ValidationError, msg=key):
                self.store.bulk_apply(ALICE, "strain", self.a, "set", key, "x")
        self.assertEqual([f["key"] for f in self.store.bulk_fields("strain")], ["genotype", "tags", "ok", "plasmid"])

    def test_owner_is_admin_only_and_validated(self):
        with self.assertRaises(Forbidden):
            self.store.bulk_apply(ALICE, "strain", self.a, "owner", value="bob")
        with self.assertRaises(ValidationError):
            self.store.bulk_apply(ROOT, "strain", self.a, "owner", value="ghost")
        res = self.store.bulk_apply(ROOT, "strain", self.a + self.b, "owner", value="bob")
        self.assertEqual(res["changed"], self.a)
        self.assertEqual(res["unchanged"], self.b)
        self.assertEqual(self.store.records[self.a[0]]["created_by"], "bob")
        self.assertEqual(Store(self.repo, users_lookup=lambda n: True).records[self.a[0]]["created_by"], "bob")
        self.assertTrue(self.store.bulk_apply(BOB, "strain", self.a, "set", "genotype", "mine")["changed"])   # new owner may now edit

    def test_archive_and_restore(self):
        self.assertEqual(self.store.bulk_apply(ALICE, "strain", self.a, "archive")["changed"], self.a)
        self.assertTrue(all(self.store.records[r]["archived"] for r in self.a))
        self.assertEqual(self.store.bulk_apply(ALICE, "strain", self.a, "archive")["unchanged"], self.a)
        self.assertEqual(self.store.bulk_apply(ALICE, "strain", self.a, "restore")["changed"], self.a)
        self.assertFalse(self.store.records[self.a[0]]["archived"])

    def test_delete_is_not_an_action(self):
        with self.assertRaises(ValidationError):
            self.store.bulk_apply(ROOT, "strain", self.a, "delete")
        self.assertTrue(all(r in self.store.records for r in self.a))

    def test_dry_run_reports_and_writes_nothing(self):
        before, head = self.commits(), self.store.git("rev-parse", "HEAD")
        res = self.store.bulk_apply(ALICE, "strain", self.a + self.b, "set", "genotype", "new", dry_run=True)
        self.assertEqual((res["changed"], [r for r, _ in res["skipped"]], res["sha"]), (self.a, self.b, None))
        self.assertEqual(self.store.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.store.records[self.a[0]]["fields"]["genotype"], "g")

    def test_bad_requests(self):
        with self.assertRaises(NotFound):
            self.store.bulk_apply(ALICE, "ghost", [], "archive")
        with self.assertRaises(ValidationError):
            self.store.bulk_apply(ALICE, "strain", self.a, "explode")
        self.assertEqual(self.store.bulk_apply(ALICE, "strain", [], "archive")["changed"], [])

    def test_search_index_follows_bulk_changes(self):
        idx = Index(self.store, lambda n: None)
        self.store.bulk_apply(ALICE, "strain", self.a, "set", "genotype", "zebrafishgene")
        self.assertEqual({d["id"] for d in idx.search("zebrafishgene")}, set(self.a))
        self.store.bulk_apply(ALICE, "strain", self.a, "archive")
        self.assertEqual(idx.search("zebrafishgene"), [])


class TestUndo(Base):
    def test_undo_restores_the_records_with_one_new_commit(self):
        res = self.store.bulk_apply(ALICE, "strain", self.a, "set", "genotype", "new")
        before = self.commits()
        self.store.undo(ALICE, res["sha"])
        self.assertEqual(self.commits(), before + 1)
        self.assertTrue(self.head_message().startswith("Revert"))
        self.assertEqual(self.store.records[self.a[0]]["fields"]["genotype"], "g")
        self.assertEqual(self.store.git("status", "--porcelain").strip(), "")
        self.assertEqual(self.store.git("log", "-1", "--format=%an <%ae>").strip(), "alice <alice@shelfblazer.invalid>")   # the user ID only, never the name

    def test_undo_of_an_archive_brings_the_records_back_into_search(self):
        idx = Index(self.store, lambda n: None)
        res = self.store.bulk_apply(ALICE, "strain", self.a, "archive")
        self.assertFalse(set(self.a) & {d["id"] for d in idx.search("g")})
        self.store.undo(ALICE, res["sha"])
        self.assertFalse(any(self.store.records[r]["archived"] for r in self.a))
        self.assertTrue(set(self.a) <= {d["id"] for d in idx.search("g")})                   # the search index is rebuilt too

    def test_who_may_undo(self):
        res = self.store.bulk_apply(ALICE, "strain", self.a, "set", "genotype", "new")
        with self.assertRaises(Forbidden):
            self.store.undo(BOB, res["sha"])
        self.store.undo(ROOT, res["sha"])                                                    # an admin may

    def test_cannot_undo_twice_or_non_bulk_commits_or_garbage(self):
        res = self.store.bulk_apply(ALICE, "strain", self.a, "set", "genotype", "new")
        self.store.undo(ALICE, res["sha"])
        with self.assertRaises(ValidationError):
            self.store.undo(ALICE, res["sha"])
        plain = self.store.git("log", "-1", "--format=%H", "--", f"data/plasmid/{self.p}.md").strip()
        with self.assertRaises(ValidationError):
            self.store.undo(ALICE, plain)
        for bad in ("", "xyz", "../../etc", "0" * 40, None):
            with self.assertRaises((ValidationError, NotFound)):
                self.store.undo(ALICE, bad)

    def test_conflicting_later_edit_blocks_the_undo_and_leaves_the_repo_clean(self):
        res = self.store.bulk_apply(ALICE, "strain", self.a, "set", "genotype", "new")
        self.store.update(self.a[0], ALICE, "a0", {"genotype": "edited later"}, "", self.store.records[self.a[0]]["_version"])
        head = self.store.git("rev-parse", "HEAD")
        with self.assertRaises(Conflict):
            self.store.undo(ALICE, res["sha"])
        self.assertEqual(self.store.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.store.git("status", "--porcelain").strip(), "")
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "REVERT_HEAD")))
        self.assertEqual(self.store.records[self.a[0]]["fields"]["genotype"], "edited later")


if __name__ == "__main__":
    unittest.main()

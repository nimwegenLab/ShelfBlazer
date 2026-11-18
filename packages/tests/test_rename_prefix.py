"""Maintenance: a type gets a new ID prefix (ST-12 becomes BS-12), with links, notes, history and old IDs kept working."""
import json, os, subprocess, tempfile, unittest

from shelfblazer import auth, schema_edit
from shelfblazer.store import Store, StoreError, ValidationError
from helpers import ALICE

auth.SCRYPT.update(n=2**10)

STRAIN = {"name": "Strain", "prefix": "ST", "list_columns": ["id", "name"], "fields": [
    {"key": "name", "label": "Name", "type": "text", "required": True},
    {"key": "parent", "label": "Parent", "type": "ref", "to": "strain", "multiple": True}]}
DOC = {"name": "Document", "prefix": "DC", "list_columns": ["id", "name"], "fields": [
    {"key": "name", "label": "Title", "type": "text", "required": True},
    {"key": "related", "label": "Related", "type": "ref", "to": "*", "multiple": True}]}


class RenamePrefix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(self.repo, "config", "types"))
        os.makedirs(os.path.join(self.repo, "data"))
        for slug, s in (("strain", STRAIN), ("document", DOC)):
            with open(os.path.join(self.repo, "config", "types", slug + ".yaml"), "w") as fh:
                json.dump(s, fh)
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True)
        subprocess.run(["git", "-C", self.repo, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"], check=True)
        self.store = Store(self.repo)
        s = self.store
        self.a = s.create("strain", ALICE, "alpha", {})
        self.b = s.create("strain", ALICE, "beta", {"parent": [self.a]}, "derived from ST-1, not from pST-1 or ST-10")
        s.update(self.a, ALICE, "alpha", {}, "first note", s.records[self.a]["_version"])
        self.d = s.create("document", ALICE, "list", {"related": [self.a, self.b]}, "see ST-2")

    def tearDown(self):
        self.tmp.cleanup()

    def test_preview_changes_nothing(self):
        before = self.store.git("rev-parse", "HEAD")
        r = self.store.rename_prefix("strain", "BS", dry_run=True)
        self.assertEqual((r["old"], r["new"], r["renamed"]), ("ST", "BS", 2))
        self.assertEqual(self.store.git("rev-parse", "HEAD"), before)
        self.assertIn("ST-1", self.store.records)

    def test_files_ids_links_and_notes_follow(self):
        self.store.rename_prefix("strain", "BS")
        s = Store(self.repo)                                                    # as a fresh start sees it
        self.assertEqual(sorted(i for i, r in s.records.items() if r["type"] == "strain"), ["BS-1", "BS-2"])
        self.assertFalse(os.path.exists(os.path.join(self.repo, "data", "strain", "ST-1.md")))
        self.assertEqual(s.records["BS-2"]["fields"]["parent"], ["BS-1"])
        self.assertEqual(s.records["DC-1"]["fields"]["related"], ["BS-1", "BS-2"])
        self.assertEqual(s.records["DC-1"]["notes"], "see BS-2")
        self.assertEqual(s.records["BS-2"]["notes"], "derived from BS-1, not from pST-1 or BS-10")   # a mention inside a longer word (pST-1) is left alone
        self.assertEqual(s.schemas["strain"]["prefix"], "BS")
        self.assertEqual(s.schemas["strain"]["former_prefixes"], ["ST"])
        self.assertEqual(s.records["BS-1"]["legacy_id"], 1)

    def test_one_commit_and_numbering_goes_on(self):
        n = len(self.store.git("log", "--format=%h").split())
        self.store.rename_prefix("strain", "BS")
        self.assertEqual(len(self.store.git("log", "--format=%h").split()), n + 1)
        self.assertEqual(self.store.create("strain", ALICE, "gamma", {}), "BS-3")

    def test_old_ids_and_history(self):
        self.store.rename_prefix("strain", "BS")
        s = self.store
        self.assertEqual(s.canonical_id("ST-2"), "BS-2")
        self.assertEqual(s.canonical_id("st-2"), "BS-2")
        self.assertEqual(s.canonical_id("PL-2"), "PL-2")
        self.assertEqual(s.canonical_id("hello"), "hello")
        subjects = [h["subject"] for h in s.history("BS-1")]
        self.assertTrue(any("ST-1: create" in x for x in subjects), subjects)       # from before the rename
        self.assertTrue(any("ID prefix ST -> BS" in x for x in subjects), subjects)
        old_sha = next(h["sha"] for h in s.history("BS-1") if "ST-1: create" in h["subject"])
        v = s.record_at("BS-1", old_sha)                                              # the version as saved under the old name
        self.assertEqual(v["rec"]["name"], "alpha")
        self.assertFalse(v["current"])
        act = s.recent_activity(dict(ALICE, role="admin"), 20)
        self.assertTrue(all(not x["deleted"] for x in act if x["id"] in ("BS-1", "BS-2")), act)

    def test_refusals(self):
        for bad in ("B", "bsx", "12", "DC"):
            with self.assertRaises(ValidationError):
                self.store.rename_prefix("strain", bad)
        with self.assertRaises(ValidationError):
            self.store.rename_prefix("strain", "ST")
        with open(os.path.join(self.repo, "data", "stray.txt"), "w") as fh:
            fh.write("x")
        with self.assertRaises(StoreError):                                           # uncommitted changes in the data repo
            self.store.rename_prefix("strain", "BS")
        os.remove(os.path.join(self.repo, "data", "stray.txt"))
        self.store.rename_prefix("strain", "BS")
        with self.assertRaises(ValidationError):                                      # the former prefix is not free to reuse
            self.store.rename_prefix("document", "ST")

    def test_former_prefixes_are_checked_in_type_definitions(self):
        sch = dict(STRAIN, former_prefixes="ST")
        self.assertTrue(schema_edit.validate_schema("strain", sch, {}, old=STRAIN))
        sch = dict(STRAIN, former_prefixes=["XX"])
        self.assertEqual([p for p in schema_edit.validate_schema("strain", sch, {}, old=STRAIN) if "former" in p], [])


if __name__ == "__main__":
    unittest.main()

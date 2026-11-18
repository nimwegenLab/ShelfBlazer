"""A store notices commits made by another process (a maintenance command, a restore, a manual commit) and reloads before
it writes or shows anything, so a running server never writes an outdated copy of a record or type back."""
import json, os, subprocess, tempfile, unittest

from shelfblazer.store import Conflict, Store
from helpers import ALICE

STRAIN = {"name": "Strain", "prefix": "ST", "list_columns": ["id", "name"], "fields": [
    {"key": "name", "label": "Name", "type": "text", "required": True},
    {"key": "genotype", "label": "Genotype", "type": "text"},
    {"key": "notes2", "label": "Old notes", "type": "text"}]}


class OutsideChanges(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(self.repo, "config", "types"))
        os.makedirs(os.path.join(self.repo, "data"))
        with open(os.path.join(self.repo, "config", "types", "strain.yaml"), "w") as fh:
            json.dump(STRAIN, fh)
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True)
        subprocess.run(["git", "-C", self.repo, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "i"], check=True)
        self.server = Store(self.repo)
        self.rid = self.server.create("strain", ALICE, "a", {"genotype": "wt", "notes2": "old"})

    def tearDown(self):
        self.tmp.cleanup()

    def read(self):
        with open(self.server.path_of(self.rid, "strain"), encoding="utf-8") as fh:
            return fh.read()

    def test_a_dropped_field_does_not_come_back_with_the_next_save(self):
        Store(self.repo).drop_field("strain", "notes2")                     # the maintenance command, in another process
        v = self.server.records[self.rid]["_version"]                        # what the open edit form carries
        with self.assertRaises(Conflict):                                    # the record changed under the form: redo the edit
            self.server.update(self.rid, ALICE, "a", {"genotype": "mut"}, "", v)
        self.server.update(self.rid, ALICE, "a", {"genotype": "mut"}, "", self.server.records[self.rid]["_version"])
        self.assertNotIn("notes2", self.read())
        self.assertNotIn("notes2", [f["key"] for f in self.server.schemas["strain"]["fields"]])

    def test_an_edit_made_elsewhere_is_never_overwritten(self):
        other = Store(self.repo)
        stale = self.server.records[self.rid]["_version"]
        other.update(self.rid, ALICE, "a", {"genotype": "elsewhere"}, "", stale)
        with self.assertRaises(Conflict):
            self.server.update(self.rid, ALICE, "a", {"genotype": "here"}, "", stale)
        self.assertIn("elsewhere", self.read())

    def test_refresh_reloads_only_after_an_outside_commit(self):
        self.assertFalse(self.server.refresh())
        self.server.update(self.rid, ALICE, "b", {}, "", self.server.records[self.rid]["_version"])
        self.assertFalse(self.server.refresh())                              # its own commits do not count
        Store(self.repo).update(self.rid, ALICE, "c", {}, "", self.server.records[self.rid]["_version"])
        seen = []
        self.server.on_change.append(seen.append)
        self.assertTrue(self.server.refresh())
        self.assertEqual(self.server.records[self.rid]["name"], "c")
        self.assertEqual(seen, [None])                                       # the search index is rebuilt

    def test_new_numbers_follow_records_created_elsewhere(self):
        other_id = Store(self.repo).create("strain", ALICE, "x", {})
        mine = self.server.create("strain", ALICE, "y", {})
        self.assertNotEqual(mine, other_id)
        self.assertEqual(self.server.records[other_id]["name"], "x")


if __name__ == "__main__":
    unittest.main()

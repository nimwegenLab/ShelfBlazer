"""After a type's ID prefix changes (ST -> BS), the old IDs still open the records, in the address and in the search."""
import unittest

from test_app import Base, git


class RenamedPrefix(Base):
    def setUp(self):
        super().setUp()
        self.rid = self.create("alice", name="alpha")                      # ST-1
        git(self.repo, "add", "-A")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "setup")
        self.store.rename_prefix("strain", "BS")
        self.c = self.client("alice")

    def test_the_record_is_under_the_new_id(self):
        self.assertEqual(self.rid, "ST-1")
        r = self.c.get("/r/BS-1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("alpha", r.text)

    def test_the_old_id_redirects(self):
        for old in ("/r/ST-1", "/r/st-1"):
            r = self.c.get(old)
            self.assertEqual((r.status_code, r.headers["location"]), (302, "/r/BS-1"))

    def test_searching_the_old_id_jumps_to_the_record(self):
        r = self.c.get("/", params={"q": "ST-1"})
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/r/BS-1"))

    def test_the_history_reaches_back_across_the_rename(self):
        page = self.c.get("/r/BS-1").text
        self.assertIn("ST-1: create", page)
        self.assertIn("ID prefix ST -&gt; BS", page)


if __name__ == "__main__":
    unittest.main()

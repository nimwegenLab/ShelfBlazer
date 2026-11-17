"""Each History entry on a record page links to the record as that commit saved it (read-only, with the type definition of that commit)."""
import json, os, unittest

from test_app import Base, git


class RecordVersions(Base):
    def setUp(self):
        super().setUp()
        self.alice = self.client("alice")
        self.rid = self.create("alice", name="first", genotype="old-genotype")
        self.edit(self.rid, "second", "new-genotype")

    def edit(self, rid, name, genotype):
        r = self.post(self.alice, f"/r/{rid}/edit", {"name": name, "f_genotype": genotype, "version": self.version(rid)})
        self.assertEqual(r.status_code, 303, r.text[:300])

    def shas(self, rid):
        return [h["sha"] for h in self.store.history(rid)]                     # newest first

    def test_history_entries_link_to_their_version(self):
        page = self.alice.get(f"/r/{self.rid}").text
        newest, *older = self.shas(self.rid)
        for sha in older:
            self.assertIn(f'href="/r/{self.rid}/at/{sha}"', page)
        self.assertNotIn(f'/at/{newest}"', page)                              # the last change is the record as it is now: nothing to open
        self.assertIn(f"<code title=\"This change is the current version of {self.rid}\">{newest}</code>", page)

    def test_the_last_change_of_the_record_is_the_current_record(self):
        """Even when later commits touched other records, the record's own last commit is its present version."""
        self.create("alice", name="unrelated")                                # a newer commit for the data as a whole
        newest = self.shas(self.rid)[0]
        r = self.alice.get(f"/r/{self.rid}/at/{newest}")
        self.assertEqual((r.status_code, r.headers["location"]), (303, f"/r/{self.rid}"))
        self.assertNotIn(f'/at/{newest}"', self.alice.get(f"/r/{self.rid}").text)

    def test_old_version_shows_the_old_values_and_is_read_only(self):
        oldest = self.shas(self.rid)[-1]
        old = self.alice.get(f"/r/{self.rid}/at/{oldest}")
        self.assertEqual(old.status_code, 200)
        self.assertIn("old-genotype", old.text)
        self.assertIn("first", old.text)
        self.assertNotIn("new-genotype", old.text)
        self.assertIn(f"This is an earlier version of <a href=\"/r/{self.rid}\">{self.rid}</a>", old.text)
        self.assertNotIn("/edit", old.text)
        self.assertNotIn("<form", old.text.split("<main", 1)[-1])
        self.assertIn("new-genotype", self.alice.get(f"/r/{self.rid}").text)

    def test_the_commit_message_is_quoted_at_the_end_of_the_banner(self):
        oldest = self.store.history(self.rid)[-1]
        box = self.alice.get(f"/r/{self.rid}/at/{oldest['sha']}").text.split('class="callout"', 1)[1].split("</div>", 1)[0]
        self.assertRegex(box, r"<blockquote>.*create.*</blockquote>$")
        self.assertLess(box.index("read-only"), box.index("Saved on"))
        self.assertLess(box.index("Saved on"), box.index("<blockquote>"))

    def test_a_full_hash_works_too(self):
        full = git(self.store.repo, "rev-parse", self.shas(self.rid)[-1]).strip()
        self.assertEqual(self.alice.get(f"/r/{self.rid}/at/{full}").status_code, 200)

    def test_bad_or_foreign_commits_are_not_found(self):
        other = self.create("alice", name="other")
        other_sha = self.shas(other)[0]
        head = git(self.store.repo, "rev-parse", "--short", "HEAD").strip()
        for sha in (other_sha, "deadbeef", "zzzzzzz", "HEAD", "--all", self.shas(self.rid)[0][:5]):
            self.assertEqual(self.alice.get(f"/r/{self.rid}/at/{sha}").status_code, 404, sha)
        self.assertEqual(self.alice.get(f"/r/ST-999/at/{head}").status_code, 404)

    def test_anonymous_is_sent_to_login(self):
        r = self.client().get(f"/r/{self.rid}/at/{self.shas(self.rid)[0]}")
        self.assertEqual(r.status_code, 303)
        self.assertIn("/login", r.headers["location"])

    def test_old_version_uses_the_type_definition_of_that_commit(self):
        """A field dropped from the type later still shows in the versions saved while it existed."""
        sha_before = self.shas(self.rid)[-1]
        path = os.path.join(self.repo, "config", "types", "strain.yaml")
        with open(path) as fh:
            schema = json.load(fh)
        schema["fields"] = [f for f in schema["fields"] if f["key"] != "genotype"]
        with open(path, "w") as fh:
            json.dump(schema, fh)
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", "strain: remove field 'genotype'")
        self.store.reload()
        self.assertNotIn("new-genotype", self.alice.get(f"/r/{self.rid}").text)
        old = self.alice.get(f"/r/{self.rid}/at/{sha_before}").text
        self.assertIn("Genotype", old)
        self.assertIn("old-genotype", old)


if __name__ == "__main__":
    unittest.main()

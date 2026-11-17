"""Archive is the only way to remove a record (git keeps every version anyway): an archived record stays, but only its owner
and administrators can see it, find it, open it or download its files; restoring brings it back."""
import re, unittest

from test_app import Base


class Archived(Base):
    def setUp(self):
        super().setUp()
        self.alice, self.bob, self.root = self.client("alice"), self.client("bob"), self.client("root")
        self.pl = self.create("alice", "plasmid", name="pArchiveMe")
        self.st = self.create("bob", name="bobs strain", plasmid=self.pl)
        self.store.add_attachment(self.pl, self.users.get("alice"), "map.gb", b"LOCUS")
        self.post(self.alice, f"/r/{self.pl}/archive")

    def test_only_owner_and_admins_open_it(self):
        self.assertEqual(self.bob.get(f"/r/{self.pl}").status_code, 404)
        self.assertEqual(self.bob.get(f"/r/{self.pl}/edit").status_code, 404)
        self.assertEqual(self.post(self.bob, f"/r/{self.pl}/archive").status_code, 404)           # nor can they restore it
        self.assertTrue(self.store.records[self.pl]["archived"])
        for who in (self.alice, self.root):
            page = who.get(f"/r/{self.pl}")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Archived: hidden from everybody except its owner and administrators", page.text)

    def test_nobody_gets_a_delete_button(self):
        for who in (self.alice, self.root):
            self.assertNotIn(f"/r/{self.pl}/delete", who.get(f"/r/{self.pl}").text)

    def test_history_of_an_archived_record_is_browsable_by_owner_and_admins(self):
        sha = self.store.history(self.pl)[-1]["sha"]
        for who in (self.alice, self.root):
            self.assertEqual(who.get(f"/r/{self.pl}/at/{sha}").status_code, 200)
        self.assertEqual(self.bob.get(f"/r/{self.pl}/at/{sha}").status_code, 404)

    def test_lists_and_searches(self):
        for url in ("/t/plasmid?archived=1", "/?q=pArchiveMe&archived=1"):
            self.assertNotIn(self.pl, self.bob.get(url).text, url)
            self.assertIn(self.pl, self.alice.get(url).text, url)
            self.assertIn(self.pl, self.root.get(url).text, url)
        self.assertNotIn(self.pl, self.alice.get("/t/plasmid").text.replace(f"/r/{self.pl}/", ""))          # hidden by default for everybody

    def test_picker_api(self):
        ids = lambda c, qs: [r["id"] for r in c.get("/api/records?" + qs).json()["results"]]
        self.assertNotIn(self.pl, ids(self.bob, "q=pArchiveMe&archived=1"))
        self.assertNotIn(self.pl, ids(self.bob, f"ids={self.pl}"))
        self.assertNotIn(self.pl, ids(self.bob, "type=plasmid&archived=1"))
        self.assertIn(self.pl, ids(self.alice, "q=pArchiveMe&archived=1"))
        self.assertIn(self.pl, ids(self.alice, "type=plasmid&archived=1"))
        self.assertIn(self.pl, ids(self.root, f"ids={self.pl}"))

    def test_files_follow_the_record(self):
        url = f"/files/{self.pl}/map.gb"
        self.assertEqual(self.bob.get(url).status_code, 404)
        self.assertEqual(self.alice.get(url).status_code, 200)
        self.assertEqual(self.root.get(url).status_code, 200)

    def test_links_to_it_show_the_id_but_do_not_open_it_for_others(self):
        page = self.bob.get(f"/r/{self.st}").text
        self.assertIn(self.pl, page)
        self.assertIn("(archived)", page)
        self.assertNotIn(f'href="/r/{self.pl}"', page)
        self.assertNotIn("pArchiveMe", page)                                      # the name stays hidden
        self.assertIn(f'href="/r/{self.pl}"', self.root.get(f"/r/{self.st}").text)
        self.assertNotIn(self.st, self.bob.get(f"/r/{self.pl}").text)             # the other way round: bob cannot even open it

    def test_others_cannot_link_to_it(self):
        r = self.post(self.bob, f"/r/{self.st}/edit", {"name": "x", "f_plasmid": self.pl, "version": self.version(self.st)})
        self.assertEqual(self.store.records[self.st]["fields"]["plasmid"], [self.pl])        # the existing link survives an unrelated save
        other = self.create("bob", name="second")
        r = self.post(self.bob, f"/r/{other}/edit", {"name": "second", "f_plasmid": self.pl, "version": self.version(other)})
        self.assertEqual(r.status_code, 422)

    def test_home_counts_and_recent_activity(self):
        bob_home, alice_home = self.bob.get("/").text, self.alice.get("/").text
        self.assertNotIn("archived " + self.pl, re.sub(r"<[^>]+>", "", bob_home))
        self.assertNotIn(f'href="/r/{self.pl}"', bob_home)
        self.assertIn(f'href="/r/{self.pl}"', alice_home)
        self.assertIn("+1 archived", alice_home)
        self.assertNotIn("archived</span>", re.sub(r"\s+", " ", bob_home).split("Record types")[-1].split("Recent activity")[0].replace("archived-", ""))

    def test_owner_restores_it(self):
        self.assertEqual(self.post(self.alice, f"/r/{self.pl}/archive").status_code, 303)
        self.assertFalse(self.store.records[self.pl]["archived"])
        self.assertEqual(self.bob.get(f"/r/{self.pl}").status_code, 200)
        self.assertIn(self.pl, self.bob.get("/t/plasmid").text)

    def test_archiving_someone_elses_record_in_an_open_type_does_not_end_on_a_404(self):
        """With edit = all, whoever archives another person's record stops seeing it: send them to the list, not to a page that is gone."""
        self.store.save_schema("plasmid", {**self.store.schemas["plasmid"], "permissions": {"edit": "all"}}, self.users.get("root"))
        self.post(self.alice, f"/r/{self.pl}/archive")                                         # alice restores her own
        r = self.post(self.bob, f"/r/{self.pl}/archive")
        self.assertEqual(r.status_code, 303)
        self.assertTrue(self.store.records[self.pl]["archived"])
        self.assertEqual(r.headers["location"], "/t/plasmid")
        self.assertEqual(self.bob.get(f"/r/{self.pl}").status_code, 404)
        self.assertEqual(self.post(self.alice, f"/r/{self.pl}/archive").headers["location"], f"/r/{self.pl}")   # the owner still sees it

    def test_bulk_selection_only_includes_what_you_may_see(self):
        r = self.post(self.bob, "/t/plasmid/bulk", {"ids": self.pl})
        self.assertEqual(r.status_code, 303)                                               # nothing selectable: back to the list


if __name__ == "__main__":
    unittest.main()

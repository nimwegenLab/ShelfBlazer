"""The pages that move personal records to the lab collection (app/shelfblazer_app/move.py): one page with the button, preceded by a
question page only when a linked record has a look-alike in the lab."""
import re

from test_app import Base, PLASMID, STRAIN


class MovePages(Base):
    def setUp(self):
        super().setUp()
        root = self.users.get("root")
        self.store.save_schema("strain", dict(STRAIN, permissions={"personal": "all"}), root)
        self.store.save_schema("plasmid", dict(PLASMID, permissions={"personal": "all"}), root)
        self.alice = self.users.get("alice")
        self.lab_pl = self.create("bob", "plasmid", name="pLab")
        self.pl = self.store.create("plasmid", self.alice, "plab", {}, personal=True)          # looks like the lab plasmid
        self.st = self.store.create("strain", self.alice, "my strain", {"plasmid": [self.pl]}, personal=True)
        self.c = self.client("alice")

    def test_a_record_without_a_decision_to_make_has_one_page_with_the_button(self):
        alone = self.store.create("strain", self.alice, "alone", {}, personal=True)
        page = self.c.get("/move", params={"ids": alone}).text
        self.assertNotIn("First, what to do", page)
        self.assertNotIn(">Continue</button>", page)
        self.assertIn('<code class="rid">ST-1</code>', page)                              # the next lab strain number
        self.assertIn("You cannot undo it.", page)
        self.assertIn("Relabel the tubes.", page)
        self.assertIn("Move 1 strain to the lab collection</button>", page)
        r = self.post(self.c, "/move", {"ids": [alone]})
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/r/ST-1"))
        self.assertIn('is now <code class="rid">ST-1</code>', self.c.get("/r/ST-1").text)
        old = self.c.get(f"/r/{alone}")                                                     # the old ID still opens it
        self.assertEqual((old.status_code, old.headers["location"]), (302, "/r/ST-1"))

    def test_a_linked_record_with_a_lookalike_asks_first_and_then_confirms(self):
        page = self.c.get("/move", params={"ids": self.st}).text
        self.assertIn("First, what to do about the records they link to", page)
        self.assertIn(f'name="r_{self.pl}" value="{self.lab_pl}"', page)
        self.assertIn(f'name="r_{self.pl}" value="" checked', page)                        # moving it too is the default
        self.assertIn(">Continue</button>", page)
        self.assertNotIn("You cannot undo it.", page)                                     # said once, on the page with the button
        page = self.c.get("/move/confirm", params={"ids": self.st}).text
        self.assertIn("Move 1 strain and 1 plasmid to the lab collection</button>", page)
        self.assertIn("You cannot undo it.", page)
        self.assertIn(f'href="/move?ids={self.st}"', page)                                # and a way back to the question
        r = self.post(self.c, "/move", {"ids": [self.st]})
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/r/ST-1"))
        self.assertEqual(self.store.records["ST-1"]["fields"]["plasmid"], ["PL-2"])

    def test_a_linked_record_without_a_lookalike_moves_along_with_no_question(self):
        self.store.update(self.lab_pl, self.users.get("bob"), "something else", {}, "", self.store.records[self.lab_pl]["_version"])
        self.store.update(self.pl, self.alice, "my own plasmid", {}, "", self.store.records[self.pl]["_version"])
        page = self.c.get("/move", params={"ids": self.st}).text
        self.assertNotIn("First, what to do", page)
        self.assertIn("Move 1 strain and 1 plasmid to the lab collection</button>", page)
        self.assertIn("it moves too, because a lab record cannot link to a personal one", page)

    def test_choosing_the_lab_lookalike_links_to_it_instead(self):
        page = self.c.get("/move/confirm", params={"ids": self.st, f"r_{self.pl}": self.lab_pl}).text
        self.assertIn("Move 1 strain to the lab collection</button>", page)
        self.assertIn(f'name="relink" value="{self.pl}:{self.lab_pl}"', page)
        r = self.post(self.c, "/move", {"ids": [self.st], "relink": [f"{self.pl}:{self.lab_pl}"]})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.store.records["ST-1"]["fields"]["plasmid"], [self.lab_pl])
        self.assertIn(self.pl, self.store.records)

    def test_someone_else_cannot_move_it(self):
        bob = self.client("bob")
        page = bob.get("/move", params={"ids": self.st}).text
        self.assertIn("This cannot be moved yet", page)
        self.assertNotIn("Move 1 strain", page)
        self.assertEqual(self.post(bob, "/move", {"ids": [self.st]}).status_code, 409)
        self.assertIn(self.st, self.store.records)

    def test_the_form_token_is_required_and_nothing_to_move_is_404(self):
        self.assertEqual(self.post(self.c, "/move", {"ids": [self.st]}, token=False).status_code, 403)
        self.assertEqual(self.c.get("/move").status_code, 404)

    def test_the_record_page_offers_the_move_to_its_owner_only(self):
        self.assertIn(f'href="/move?ids={self.st}"', self.c.get(f"/r/{self.st}").text)
        self.assertNotIn("/move?ids=", self.client("bob").get(f"/r/{self.st}").text)
        lab = self.create("alice", name="lab one")
        self.assertNotIn("/move?ids=", self.c.get(f"/r/{lab}").text)

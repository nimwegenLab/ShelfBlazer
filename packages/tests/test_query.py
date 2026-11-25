"""The record query (shelfblazer/query.py): one selection used by lists, the home search and the picker, always within what the viewer may see."""
import json, os, unittest

from shelfblazer.query import Query, select, sequence_field
from shelfblazer.search import Index

from helpers import ALICE, BOB, ROOT, Base


class Select(Base):
    def setUp(self):
        super().setUp()
        self.index = Index(self.store, lambda u: None)
        self.store.set_archived(self.a[0], ALICE, True)

    def ids(self, viewer, **kw):
        recs, owners = select(self.store, self.index, viewer, Query(**kw))
        return {r["id"] for r in recs}, owners

    def test_a_type_without_filters_is_every_active_record(self):
        got, owners = self.ids(BOB, type="strain")
        self.assertEqual(got, set(self.a[1:] + self.b))
        self.assertEqual(owners, {"alice", "bob"})

    def test_archived_records_only_when_asked_and_only_for_whom_may_see_them(self):
        self.assertNotIn(self.a[0], self.ids(ALICE, type="strain")[0])
        self.assertIn(self.a[0], self.ids(ALICE, type="strain", archived=True)[0])     # the creator
        self.assertIn(self.a[0], self.ids(ROOT, type="strain", archived=True)[0])      # an administrator
        self.assertNotIn(self.a[0], self.ids(BOB, type="strain", archived=True)[0])    # nobody else
        self.assertNotIn(self.a[0], self.ids(BOB, text="a0", archived=True)[0])        # also when searching

    def test_owner_filter_keeps_the_owner_list_of_all_matches(self):
        got, owners = self.ids(BOB, type="strain", owner="bob")
        self.assertEqual(got, set(self.b))
        self.assertEqual(owners, {"alice", "bob"})

    def test_text_search_keeps_relevance_order_and_types_narrow(self):
        recs, _ = select(self.store, self.index, BOB, Query(text=self.b[1]))
        self.assertEqual(recs[0]["id"], self.b[1])
        self.assertEqual(self.ids(BOB, text="px")[0], {self.p})
        self.assertEqual(self.ids(BOB, text="px", types=frozenset({"strain"}))[0], set())
        self.assertEqual(self.ids(BOB, types=frozenset({"plasmid"}))[0], {self.p})

    def test_a_hidden_type_is_never_selected(self):
        path = os.path.join(self.repo, "config", "types", "plasmid.yaml")
        with open(path) as fh:
            sch = json.load(fh)
        sch["permissions"] = {"read": "admin"}
        self.store.save_schema("plasmid", sch, ROOT)
        self.assertEqual(self.ids(BOB, text="px")[0], set())
        self.assertEqual(self.ids(BOB, type="plasmid")[0], set())
        self.assertEqual(self.ids(ROOT, type="plasmid")[0], {self.p})

    def test_sequence_search_needs_the_type_setting(self):
        self.assertIsNone(sequence_field(self.store, "strain"))
        self.assertIsNone(sequence_field(self.store, "nope"))
        got, _ = self.ids(BOB, type="strain", text="g", sequence=True)                # no sequence field: an ordinary search
        self.assertEqual(got, set(self.a[1:] + self.b))


if __name__ == "__main__":
    unittest.main()

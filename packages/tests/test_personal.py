"""Personal collections in the core (design section 17, step 1): IDs and counters, the type setting, "Visible to others", edit and link rules."""
import json, os, tempfile, unittest

from shelfblazer import schema_edit
from shelfblazer.auth import Users
from shelfblazer.search import Index
from shelfblazer.store import Forbidden, ValidationError

from helpers import ALICE, BOB, ROOT, Base, PLASMID, STRAIN


class Personal(Base):
    def setUp(self):
        super().setUp()
        self.people = {"alice": dict(ALICE), "bob": dict(BOB), "root": dict(ROOT)}
        self.store.users_lookup = lambda key: self.people.get(key)
        self.allow("strain", "all")
        self.allow("plasmid", "all")

    def allow(self, slug, mode):
        sch = dict(self.store.schemas[slug])
        sch["permissions"] = dict(sch.get("permissions", {}), personal=mode)
        self.store.save_schema(slug, sch, ROOT)

    def hide(self, who, slug):
        self.people[who]["hidden_collections"] = [slug]

    def counters(self):
        with open(os.path.join(self.repo, "config", "counters.yaml")) as fh:
            return json.load(fh)

    # ---- IDs and the type setting
    def test_personal_records_have_their_own_numbers_and_the_lab_counter_does_not_move(self):
        p1 = self.store.create("strain", ALICE, "mine", {}, personal=True)
        p2 = self.store.create("strain", BOB, "his", {}, personal=True)
        self.assertEqual((p1, p2), ("ST-P1~alice", "ST-P1~bob"))             # each person counts their own, from 1; the owner is in the key
        self.assertEqual(self.store.create("strain", ALICE, "mine too", {}, personal=True), "ST-P2~alice")
        lab = self.store.create("strain", ALICE, "lab", {})
        self.assertEqual(lab, "ST-6")                                       # 5 lab strains before: the personal ones took no number
        self.assertEqual((self.counters()["strain.P.alice"], self.counters()["strain.P.bob"]), (3, 2))
        self.assertEqual(self.head_message(), "ST-6: create by alice")
        self.assertTrue(os.path.exists(os.path.join(self.repo, "data", "strain", "ST-P1~alice.md")))
        self.assertEqual(self.store.records[p2]["legacy_id"], 1)

    def test_the_type_setting_is_off_by_default_and_admin_keeps_it_to_administrators(self):
        self.store.save_schema("plasmid", dict(PLASMID), ROOT)               # no "personal": off
        with self.assertRaises(Forbidden):
            self.store.create("plasmid", ALICE, "p", {}, personal=True)
        self.allow("strain", "admin")
        with self.assertRaises(Forbidden):
            self.store.create("strain", ALICE, "s", {}, personal=True)
        self.assertEqual(self.store.create("strain", ROOT, "s", {}, personal=True), "ST-P1~root")

    def test_the_setting_is_validated_and_kept_by_the_settings_form(self):
        bad = dict(STRAIN, permissions={"personal": "everyone"})
        self.assertTrue(schema_edit.validate_schema("strain", bad, {}, old=STRAIN))
        sch = schema_edit.update_settings(json.loads(json.dumps(self.store.schemas["strain"])), permissions={"read": "", "create": "", "edit": "all"})
        self.assertEqual(sch["permissions"], {"personal": "all", "edit": "all"})

    # ---- visibility
    def test_others_see_personal_records_unless_the_owner_hides_that_collection(self):
        rid = self.store.create("strain", ALICE, "zebra", {}, personal=True)
        pl = self.store.create("plasmid", ALICE, "pz", {}, personal=True)
        index = Index(self.store, lambda u: None)
        self.assertTrue(self.store.readable(BOB, rid))
        self.hide("alice", "strain")
        self.assertIsNone(self.store.readable(BOB, rid))
        self.assertNotIn(rid, self.store.visible_records(BOB))
        self.assertEqual([d["id"] for d in index.search("zebra", visible=self.store.visibility(BOB))], [])
        self.assertTrue(self.store.readable(BOB, pl))                         # per type: her plasmids stay visible
        self.assertTrue(self.store.readable(ALICE, rid))                      # the owner
        self.assertTrue(self.store.readable(ROOT, rid))                       # administrators
        self.store.set_archived(pl, ALICE, True)
        self.assertIsNone(self.store.readable(BOB, pl))                       # archived: owner and administrators only, as always

    def test_an_erased_owners_personal_records_are_for_administrators_only(self):
        rid = self.store.create("strain", ALICE, "zebra", {}, personal=True)
        self.hide("alice", "strain")
        self.assertIsNone(self.store.readable(BOB, rid))
        del self.people["alice"]                                              # the account is erased: its choice is gone with it
        self.assertIsNone(self.store.readable(BOB, rid))
        self.assertNotIn(rid, self.store.visible_records(BOB))
        self.assertTrue(self.store.readable(ROOT, rid))
        lab = self.store.create("strain", BOB, "lab", {})
        self.people.pop("bob")
        self.assertTrue(self.store.readable(ALICE, lab))                      # lab records of an erased person stay visible

    def test_a_hidden_type_hides_personal_records_too(self):
        rid = self.store.create("strain", ALICE, "s", {}, personal=True)
        sch = dict(self.store.schemas["strain"], permissions={"personal": "all", "read": "admin"})
        self.store.save_schema("strain", sch, ROOT)
        self.assertIsNone(self.store.readable(ALICE, rid))
        self.assertTrue(self.store.readable(ROOT, rid))

    # ---- editing
    def test_only_the_owner_and_administrators_change_a_personal_record_whatever_edit_says(self):
        sch = dict(self.store.schemas["strain"], permissions={"personal": "all", "edit": "all"})
        self.store.save_schema("strain", sch, ROOT)
        rid = self.store.create("strain", ALICE, "s", {}, personal=True)
        self.assertTrue(self.store.can_edit(BOB, self.store.records[self.a[0]]))      # edit: all opens lab records
        self.assertFalse(self.store.can_edit(BOB, self.store.records[rid]))           # but not someone's personal record
        with self.assertRaises(Forbidden):
            self.store.set_archived(rid, BOB, True)
        self.assertTrue(self.store.can_edit(ALICE, self.store.records[rid]))
        self.assertTrue(self.store.can_edit(ROOT, self.store.records[rid]))

    # ---- links
    def test_a_lab_record_cannot_link_to_a_personal_one(self):
        mine = self.store.create("plasmid", ALICE, "p", {}, personal=True)
        with self.assertRaises(ValidationError) as e:
            self.store.create("strain", ALICE, "lab", {"plasmid": [mine]})
        self.assertIn("a lab record cannot link to it", str(e.exception.errors))
        with self.assertRaises(ValidationError):
            self.store.update(self.a[0], ALICE, "a0", {"plasmid": [mine]}, "", self.store.records[self.a[0]]["_version"])

    def test_a_personal_record_keeps_its_owner_in_an_edit_and_in_a_bulk_edit(self):
        rid = self.store.create("strain", ALICE, "mine", {}, personal=True)
        with self.assertRaises(ValidationError) as cm:
            self.store.update(rid, ROOT, "mine", {}, "", self.store.records[rid]["_version"], owner="bob")
        self.assertIn("owner", cm.exception.errors)
        result = self.store.bulk_apply(ROOT, "strain", [rid], "owner", value="bob")
        self.assertEqual(list(result["errors"]), [rid])
        self.assertEqual(self.store.records[rid]["created_by"], "alice")
        lab = self.store.create("strain", ALICE, "lab", {})
        self.store.update(lab, ROOT, "lab", {}, "", self.store.records[lab]["_version"], owner="bob")      # lab records: as before
        self.assertEqual(self.store.records[lab]["created_by"], "bob")

    def test_a_personal_record_links_to_lab_records_and_its_owners_personal_records_only(self):
        mine = self.store.create("plasmid", ALICE, "p", {}, personal=True)
        his = self.store.create("plasmid", BOB, "q", {}, personal=True)       # visible to others, still not linkable
        rid = self.store.create("strain", ALICE, "s", {"plasmid": [self.p, mine]}, personal=True)
        self.assertEqual(self.store.records[rid]["fields"]["plasmid"], [self.p, mine])
        with self.assertRaises(ValidationError) as e:
            self.store.create("strain", ALICE, "t", {"plasmid": [his]}, personal=True)
        self.assertIn("another person's collection", str(e.exception.errors))

    def test_bulk_edit_applies_the_same_link_rule(self):
        mine = self.store.create("plasmid", ALICE, "p", {}, personal=True)
        result = self.store.bulk_apply(ALICE, "strain", [self.a[0]], "add", field="plasmid", value=mine)
        self.assertIn(self.a[0], result["errors"])
        rid = self.store.create("strain", ALICE, "s", {}, personal=True)
        result = self.store.bulk_apply(ALICE, "strain", [rid], "add", field="plasmid", value=mine)
        self.assertEqual(result["changed"], [rid])

    # ---- selecting, bulk edit, activity
    def test_the_record_query_selects_a_collection(self):
        from shelfblazer.query import Query, select
        index = Index(self.store, lambda u: None)
        mine = self.store.create("strain", ALICE, "ours", {}, personal=True)
        his = self.store.create("strain", BOB, "ours too", {}, personal=True)
        got = lambda viewer, **kw: {r["id"] for r in select(self.store, index, viewer, Query(type="strain", **kw))[0]}
        lab = set(self.a + self.b)
        self.assertEqual(got(ALICE), lab)                                            # the lab is the default
        self.assertEqual(got(ALICE, collection="personal"), {mine, his})
        self.assertEqual(got(ALICE, collection="personal", owner="alice"), {mine})
        self.assertEqual(got(ALICE, collection="lab+mine"), lab | {mine})
        self.assertEqual(got(ALICE, collection="any"), lab | {mine, his})
        self.assertEqual(got(ALICE, text="ours", collection="any"), {mine, his})
        self.assertEqual(got(ALICE, text="ours"), set())
        self.hide("bob", "strain")
        self.assertEqual(got(ALICE, collection="personal"), {mine})                  # a hidden collection is not selected
        self.assertEqual(got(BOB, collection="personal"), {mine, his})

    def test_a_bulk_edit_stays_within_one_collection(self):
        mine = self.store.create("strain", ALICE, "p", {}, personal=True)
        result = self.store.bulk_apply(ALICE, "strain", [mine, self.a[0]], "set", field="genotype", value="x")
        self.assertEqual((result["changed"], result["skipped"]), ([mine], [(self.a[0], "is in a different collection")]))
        result = self.store.bulk_apply(ALICE, "strain", [self.a[0], mine], "set", field="genotype", value="y")
        self.assertEqual((result["changed"], result["skipped"]), ([self.a[0]], [(mine, "is in a different collection")]))

    def test_personal_records_are_not_in_the_labs_recent_activity(self):
        self.store.create("strain", ALICE, "p", {}, personal=True)
        self.assertNotIn("ST-P1", [a["id"] for a in self.store.recent_activity(ALICE, 20)])
        self.assertIn(self.a[0], [a["id"] for a in self.store.recent_activity(ALICE, 20)])

    def test_a_bulk_edit_of_personal_records_only_is_not_in_the_labs_recent_activity(self):
        mine = self.store.create("strain", ALICE, "p", {}, personal=True)
        self.store.bulk_apply(ALICE, "strain", [mine], "archive")
        self.assertNotIn("bulk", [a["action"] for a in self.store.recent_activity(BOB, 20)])
        self.store.bulk_apply(ALICE, "strain", [self.a[0]], "archive")
        self.assertEqual([a["name"] for a in self.store.recent_activity(BOB, 20) if a["action"] == "bulk"], ["archive (1 strain records)"])

    # ---- renaming a prefix
    def test_renaming_a_prefix_renames_personal_records_and_their_mentions(self):
        mine = self.store.create("strain", ALICE, "s", {}, personal=True, notes="")
        bobs = self.store.create("strain", BOB, "his", {}, personal=True)
        other = self.store.create("strain", ALICE, "t", {}, personal=True, notes=f"from {mine}, short ST-P1, and Bob's {bobs}")
        self.store.rename_prefix("strain", "BS")
        self.assertIn("BS-P1~alice", self.store.records)
        self.assertIn("BS-P1~bob", self.store.records)
        notes = self.store.records["BS-P2~alice"]["notes"]
        self.assertEqual(notes, "from BS-P1~alice, short BS-P1, and Bob's BS-P1~bob")                # a short mention stays short
        self.assertEqual(self.store.canonical_id("ST-P1~alice"), "BS-P1~alice")
        self.assertEqual(self.store.resolve("ST-P1", "alice"), "BS-P1~alice")                         # the old prefix, read for Alice
        self.assertEqual(self.store.resolve("st-p1", "bob"), "BS-P1~bob")
        self.assertNotIn(other, self.store.records)

    # ---- the short form is read for someone
    def test_a_short_personal_id_is_read_for_the_person_it_is_typed_by(self):
        a1 = self.store.create("strain", ALICE, "mine", {}, personal=True)
        b1 = self.store.create("strain", BOB, "his", {}, personal=True)
        self.assertEqual((self.store.resolve("ST-P1", "alice"), self.store.resolve("st-p1", "bob")), (a1, b1))
        self.assertEqual(self.store.resolve("ST-P1~bob", "alice"), b1)                                # a full key is what it says
        self.assertEqual(self.store.resolve("ST-3", "alice"), "ST-3")                                  # lab IDs are the same for everybody
        self.assertEqual(self.store.resolve("nothing", "alice"), "nothing")
        self.assertEqual(self.store.resolve("ST-P1"), "ST-P1")                                        # no one to read it for
        link = self.store.create("strain", ALICE, "links", {"plasmid": "PL-1"}, personal=True)
        mine_pl = self.store.create("plasmid", ALICE, "pa", {}, personal=True)
        self.store.update(link, ALICE, "links", {"plasmid": "PL-P1"}, "", self.store.records[link]["_version"])
        self.assertEqual(self.store.records[link]["fields"]["plasmid"], [mine_pl])                    # stored as the key
        bobs_pl = self.store.create("plasmid", BOB, "pb", {}, personal=True)
        with self.assertRaises(ValidationError):
            self.store.update(link, ALICE, "links", {"plasmid": bobs_pl}, "", self.store.records[link]["_version"])

    def test_the_search_finds_a_personal_record_by_its_short_id_or_its_owner(self):
        a1 = self.store.create("strain", ALICE, "zebra", {}, personal=True)
        b1 = self.store.create("strain", BOB, "okapi", {}, personal=True)
        index = Index(self.store, lambda u: self.people.get(u))
        found = lambda q: [d["id"] for d in index.search(q, visible=self.store.visibility(ROOT))]
        self.assertEqual(sorted(found("ST-P1")), sorted([a1, b1]))                                    # both, until one is chosen
        self.assertEqual(found("Alice A zebra"), [a1])

    def test_a_bulk_edit_stays_within_one_persons_collection(self):
        a1 = self.store.create("strain", ALICE, "a", {}, personal=True)
        b1 = self.store.create("strain", BOB, "b", {}, personal=True)
        r = self.store.bulk_apply(ROOT, "strain", [a1, b1], "set", field="genotype", value="x")
        self.assertEqual((r["changed"], [i for i, _ in r["skipped"]]), ([a1], [b1]))


class VisibleToOthers(unittest.TestCase):
    def test_the_choice_is_stored_with_the_account_per_type(self):
        with tempfile.TemporaryDirectory() as d:
            users = Users(os.path.join(d, "users.json"))
            users.create("alice", "Alice A")
            self.assertTrue(Users.collection_visible(users.get("alice"), "strain"))
            users.set_collection_visible("alice", "strain", False)
            users.set_collection_visible("alice", "plasmid", False)
            users.set_collection_visible("alice", "plasmid", True)
            self.assertEqual(users.get("alice")["hidden_collections"], ["strain"])
            self.assertFalse(Users.collection_visible(users.get("alice"), "strain"))
            self.assertTrue(Users.collection_visible(users.get("alice"), "plasmid"))
            self.assertTrue(Users.collection_visible(None, "strain"))
            with self.assertRaises(KeyError):
                users.set_collection_visible("nobody", "strain", False)


if __name__ == "__main__":
    unittest.main()

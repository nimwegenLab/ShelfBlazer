"""Moving personal records to the lab collection (shelfblazer/personal.py, design section 17.5)."""
import json, os, unittest
from unittest import mock

from shelfblazer import ids, personal
from shelfblazer.store import ValidationError

from helpers import ALICE, BOB, ROOT, Base, PLASMID, STRAIN


class Setup(Base):
    def setUp(self):
        super().setUp()
        self.people = {"alice": dict(ALICE), "bob": dict(BOB), "root": dict(ROOT)}
        self.store.users_lookup = lambda key: self.people.get(key)
        self.store.save_schema("strain", dict(STRAIN, permissions={"personal": "all"},
                                              fields=STRAIN["fields"] + [{"key": "files", "label": "Files", "type": "file", "multiple": True}]), ROOT)
        self.store.save_schema("plasmid", dict(PLASMID, permissions={"personal": "all"}, sequence_search="seq",
                                               fields=PLASMID["fields"] + [{"key": "seq", "label": "Sequence", "type": "longtext", "validate": "dna"}]), ROOT)

    def mine(self, slug="strain", name="s", who=ALICE, **fields):
        return self.store.create(slug, who, name, fields, personal=True)

    def counters(self):
        with open(os.path.join(self.repo, "config", "counters.yaml")) as fh:
            return json.load(fh)


class Move(Setup):
    def test_a_move_gives_the_next_lab_number_keeps_the_old_id_and_the_owner(self):
        rid = self.mine()
        before = self.commits()
        plan = personal.plan_move(self.store, ALICE, [rid])
        self.assertEqual((plan["new_ids"], plan["problems"], plan["counts"]), ({rid: "ST-6"}, [], [("Strain", 1)]))
        self.assertEqual(self.commits(), before)                                      # planning writes nothing
        self.assertEqual(personal.apply_move(self.store, ALICE, [rid]), {rid: "ST-6"})
        self.assertEqual(self.commits(), before + 1)                                  # one commit
        rec = self.store.records["ST-6"]
        self.assertEqual((rec["former_ids"], rec["legacy_id"], rec["created_by"]), ([rid], 6, "alice"))
        self.assertNotIn(rid, self.store.records)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "data", "strain", f"{rid}.md")))
        self.assertEqual(self.store.canonical_id(rid), "ST-6")                        # the old ID still finds it
        self.assertEqual(self.store.canonical_id(rid.lower()), "ST-6")
        self.assertEqual(len(self.store.history("ST-6")), 2)                          # its history reads across the move
        self.assertEqual(self.counters()["strain"], 7)
        self.assertIn("move to the lab collection by alice: ST-P1~alice -> ST-6", self.head_message())

    def test_two_people_with_the_same_short_id_move_apart_and_their_short_mentions_follow_their_own(self):
        a, b = self.mine(name="alices"), self.mine(name="bobs", who=BOB)               # ST-P1 for each
        self.assertEqual((ids.short(a), ids.short(b)), ("ST-P1", "ST-P1"))
        na = self.store.create("strain", ALICE, "na", {}, notes="see ST-P1", personal=True)
        nb = self.store.create("strain", BOB, "nb", {}, notes="see ST-P1", personal=True)
        mapping = personal.apply_move(self.store, ROOT, [a, b])
        self.assertEqual(mapping, {a: "ST-6", b: "ST-7"})
        self.assertEqual((self.store.records[na]["notes"], self.store.records[nb]["notes"]), ("see ST-6", "see ST-7"))
        self.assertEqual((self.store.resolve("ST-P1", "alice"), self.store.resolve("ST-P1", "bob")), ("ST-6", "ST-7"))   # the tubes still lead to them

    def test_linked_personal_records_move_too_and_links_and_mentions_follow(self):
        pl = self.mine("plasmid", "pA")
        rid = self.mine(plasmid=[self.p, pl])
        note = self.store.create("strain", ALICE, "other", {}, notes=f"made from {rid} and {pl}", personal=True)
        plan = personal.plan_move(self.store, ALICE, [rid])
        self.assertEqual(plan["moving"], [rid, pl])
        self.assertEqual([(i["asked"], i["via"]) for i in plan["items"]], [(True, None), (False, rid)])
        mapping = personal.apply_move(self.store, ALICE, [rid])
        new, newpl = mapping[rid], mapping[pl]
        self.assertEqual(newpl, "PL-2")
        self.assertEqual(self.store.records[new]["fields"]["plasmid"], [self.p, newpl])
        self.assertEqual(self.store.records[note]["notes"], f"made from {new} and {newpl}")
        self.assertEqual(self.store.records[newpl]["former_ids"], [pl])

    def test_a_linked_record_can_be_replaced_by_a_lab_record_that_looks_the_same(self):
        by_name = self.mine("plasmid", "PX")                                         # same name as the lab plasmid pX
        seq_lab = self.store.create("plasmid", BOB, "pLab", {"seq": "ACGTACGTACGTACGTACGT"})
        by_seq = self.mine("plasmid", "my copy", seq="acgtacgtacgtacgtacgt")
        rid = self.mine(plasmid=[by_name, by_seq])
        plan = personal.plan_move(self.store, ALICE, [rid])
        self.assertEqual([[c["id"] for c in i["lookalikes"]] for i in plan["items"]], [[], [self.p], [seq_lab]])
        mapping = personal.apply_move(self.store, ALICE, [rid], relink={by_name: self.p, by_seq: seq_lab})
        self.assertEqual(list(mapping), [rid])                                        # only the strain moved
        self.assertEqual(self.store.records[mapping[rid]]["fields"]["plasmid"], [self.p, seq_lab])
        self.assertIn(by_name, self.store.records)                                    # still in Alice's collection
        self.assertIn(f"{by_name}: linked to {self.p} instead", self.store.git("log", "-1", "--format=%b"))

    def test_relink_only_to_an_offered_lookalike(self):
        pl = self.mine("plasmid", "unique")
        rid = self.mine(plasmid=[pl])
        plan = personal.plan_move(self.store, ALICE, [rid], relink={pl: self.p})       # pX does not look like "unique"
        self.assertEqual(plan["moving"], [rid, pl])

    def test_what_stops_a_move(self):
        rid = self.mine()
        self.assertEqual(personal.plan_move(self.store, BOB, [rid])["problems"], [(rid, "only its owner or an administrator can move it")])
        self.assertEqual(personal.plan_move(self.store, ALICE, [self.a[0]])["problems"], [(self.a[0], "is already in the lab collection")])
        self.assertEqual(personal.plan_move(self.store, ALICE, ["ST-P99"])["problems"], [("ST-P99", "does not exist")])
        pl = self.mine("plasmid", "pA")
        linked = self.mine(plasmid=[pl])
        self.store.set_archived(pl, ALICE, True)
        self.assertIn((pl, "is archived: restore it first, or link to a lab record instead"), personal.plan_move(self.store, ALICE, [linked])["problems"])
        with self.assertRaises(ValidationError):
            personal.apply_move(self.store, ALICE, [linked])
        self.assertIn(linked, self.store.records)

    def test_lab_rules_apply_create_admin_and_required_fields(self):
        rid = self.mine()
        sch = dict(self.store.schemas["strain"], permissions={"personal": "all", "create": "admin"})
        self.store.save_schema("strain", sch, ROOT)
        self.assertIn("only administrators can add strain records", personal.plan_move(self.store, ALICE, [rid])["problems"][0][1])
        self.assertEqual(personal.plan_move(self.store, ROOT, [rid])["problems"], [])
        fields = [dict(f, required=True) if f["key"] == "genotype" else f for f in sch["fields"]]
        self.store.save_schema("strain", dict(sch, fields=fields, permissions={"personal": "all"}), ROOT)
        self.assertEqual(personal.plan_move(self.store, ALICE, [rid])["problems"], [(rid, "Genotype is required in the lab collection")])

    def test_files_move_with_the_record(self):
        rid = self.mine()
        self.store.add_attachment(rid, ALICE, "map.txt", b"hello")
        new = personal.apply_move(self.store, ALICE, [rid])[rid]
        self.assertEqual(self.store.records[new]["fields"]["files"], [f"{new}/map.txt"])
        with open(os.path.join(self.repo, "data", "files", new, "map.txt"), "rb") as fh:
            self.assertEqual(fh.read(), b"hello")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "data", "files", rid)))

    def test_several_records_in_one_commit_and_an_administrator_can_revert_it(self):
        a, b = self.mine(name="one"), self.mine(name="two", who=BOB)
        before = self.commits()
        mapping = personal.apply_move(self.store, ROOT, [a, b])
        self.assertEqual(mapping, {a: "ST-6", b: "ST-7"})
        self.assertEqual(self.commits(), before + 1)
        sha = self.store.git("rev-parse", "HEAD").strip()
        with self.assertRaises(Exception):
            personal.undo_move(self.store, ALICE, sha)                                # administrators only
        personal.undo_move(self.store, ROOT, sha)
        self.assertIn(a, self.store.records)
        self.assertIn(b, self.store.records)
        self.assertNotIn("ST-6", self.store.records)
        self.assertEqual(self.store.canonical_id(a), a)
        self.assertEqual(self.store.create("strain", ALICE, "next", {}), "ST-8")  # ST-6 and ST-7 stay unused
        with self.assertRaises(ValidationError):
            personal.undo_move(self.store, ROOT, sha)                                 # once only
        with self.assertRaises(ValidationError):
            personal.undo_move(self.store, ROOT, self.store.git("rev-parse", "HEAD").strip())   # not a move

    def test_undo_works_after_later_records_of_the_type_and_refuses_after_an_edit_of_a_moved_one(self):
        a = self.mine(name="one")
        personal.apply_move(self.store, ROOT, [a])
        sha = self.store.git("rev-parse", "HEAD").strip()
        later = self.store.create("strain", BOB, "later", {})                     # changes the counters line the move changed
        personal.undo_move(self.store, ROOT, sha)
        self.assertIn(a, self.store.records)
        self.assertNotIn("ST-6", self.store.records)
        self.assertIn(later, self.store.records)
        self.assertEqual(self.store.git("status", "--porcelain").strip(), "")
        self.assertEqual(self.store.create("strain", ALICE, "next", {}), "ST-8")  # ST-6 stays unused, the counters went on
        b = self.mine(name="two")
        with mock.patch("shelfblazer.personal.now_iso", return_value="2098-01-01T00:00:00"):  # the move, the edit: each later than the last
            moved = personal.apply_move(self.store, ROOT, [b])[b]
        sha = self.store.git("rev-parse", "HEAD").strip()
        with mock.patch("shelfblazer.store.now_iso", return_value="2099-01-01T00:00:00"):  # a later edit (not within the same second)
            self.store.update(moved, ALICE, "two, edited", {}, "", self.store.records[moved]["_version"])
        with self.assertRaises(Exception) as cm:
            personal.undo_move(self.store, ROOT, sha)
        self.assertIn("can no longer be undone", str(cm.exception))
        self.assertIn(moved, self.store.records)
        self.assertEqual(self.store.git("status", "--porcelain").strip(), "")    # the failed revert left nothing behind


class ToPersonal(Setup):
    """Administrators only, from the command line: a person's lab records into their personal collection (design D43)."""

    def test_lab_records_get_personal_ids_keep_the_lab_id_files_and_links_follow(self):
        a = self.store.create("strain", ALICE, "one", {})
        b = self.store.create("strain", ALICE, "two", {}, notes=f"from {a}")
        self.store.add_attachment(a, ALICE, "map.txt", b"x")
        lab_counter = self.counters()["strain"]
        mapping = personal.apply_to_personal(self.store, ALICE, [a, b])
        self.assertEqual(mapping, {a: "ST-P1~alice", b: "ST-P2~alice"})
        self.assertNotIn(a, self.store.records)
        rec = self.store.records["ST-P1~alice"]
        self.assertEqual((rec["created_by"], rec["former_ids"], rec["legacy_id"]), ("alice", [a], 1))
        self.assertEqual(self.store.records["ST-P2~alice"]["notes"], "from ST-P1")
        self.assertEqual(self.store.canonical_id(a), "ST-P1~alice")                     # the lab ID on the tube still opens it
        self.assertTrue(os.path.exists(os.path.join(self.repo, "data", "files", "ST-P1~alice", "map.txt")))
        self.assertEqual(self.counters()["strain"], lab_counter)                   # lab numbers stay unused, never reissued
        self.assertTrue(self.head_message().startswith(personal.TO_PERSONAL_SUBJECT + "alice (2 strain)"))
        self.assertGreater(len(self.store.history("ST-P1~alice")), 1)                    # its history reads across the move
        self.assertEqual(self.store.git("status", "--porcelain").strip(), "")

    def test_each_person_counts_their_own_when_two_people_are_moved(self):
        a, b = self.store.create("strain", ALICE, "a", {}), self.store.create("strain", BOB, "b", {})
        self.assertEqual(personal.apply_to_personal(self.store, ALICE, [a]), {a: "ST-P1~alice"})
        self.assertEqual(personal.apply_to_personal(self.store, BOB, [b]), {b: "ST-P1~bob"})
        self.assertEqual(self.store.create("strain", ALICE, "next", {}, personal=True), "ST-P2~alice")
        self.assertEqual(self.store.resolve(a, "bob"), "ST-P1~alice")                  # a lab ID is the same for everybody

    def test_what_stops_it(self):
        mine, bobs = self.store.create("strain", ALICE, "mine", {}), self.store.create("strain", BOB, "his", {})
        pl = self.store.create("plasmid", ALICE, "p", {})
        linking = self.store.create("strain", BOB, "links", {"plasmid": [pl]})
        p = personal.plan_to_personal(self.store, ALICE, [mine, bobs, pl, "ST-999", "ST-P1"])
        texts = dict(p["problems"])
        self.assertIn("belongs to Bob B", texts[bobs])
        self.assertIn(f"{linking} links to it", texts[pl])                          # a lab record cannot link to a personal one
        self.assertEqual(texts["ST-999"], "does not exist")
        self.assertEqual(p["moving"], [mine, pl])
        self.store.save_schema("strain", dict(STRAIN), ROOT)                       # personal collections off for strains
        self.assertIn("cannot be in personal collections", dict(personal.plan_to_personal(self.store, ALICE, [mine])["problems"])[mine])
        with self.assertRaises(ValidationError):
            personal.apply_to_personal(self.store, ALICE, [mine, bobs])
        self.assertIn(mine, self.store.records)                                    # nothing written


if __name__ == "__main__":
    unittest.main()

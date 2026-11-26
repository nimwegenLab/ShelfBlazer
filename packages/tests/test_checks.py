"""Duplicate checks (checks.py): the default name key, rules files, normalisers, visibility, and the index following changes."""
import json, os, unittest

from shelfblazer import checks
from helpers import ALICE, BOB, ROOT, Base

RULES = {
    "tables": {"spelling": [["(?i)\\bwt1\\b", "WT-1"], ["[∆𝚫]", "Δ"]],
               "alias": [["\\bx7\\b", "fast"]]},
    "extract": {"background": {"field": "name", "pattern": "^(?P<value>\\S+)"}},
    "duplicate_keys": [
        {"label": "same background, changes and plasmids",
         "parts": [{"extract": "background", "normalise": ["spelling", "casefold"], "required": True},
                   {"field": "genotype", "normalise": ["spelling", "alias"], "set": True},
                   {"field": "plasmid"}]}],
    "guidance": "Names: background first.",
}


class Checks(Base):
    def rules(self, doc, slug="strain"):
        d = os.path.join(self.repo, "config", "checks")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, slug + ".yaml"), "w", encoding="utf-8") as fh:
            fh.write(doc if isinstance(doc, str) else json.dumps(doc))
        self.store.reload()
        self.store._notify(None)

    def dups(self, name, viewer=ALICE, rid=None, slug="strain", **fields):
        return [r["id"] for f in checks.review(self.store, viewer, slug, {"name": name, "fields": fields}, rid=rid)
                if f.kind == "duplicate" for r in f.records]

    # ---- the default key: the name
    def test_without_rules_the_name_is_compared_without_case_accents_or_spaces(self):
        self.assertEqual(self.dups("A0"), [self.a[0]])
        self.assertEqual(self.dups("  a0 "), [self.a[0]])
        self.store.create("strain", ALICE, "Müller  strain", {})
        self.assertEqual(len(self.dups("muller strain")), 1)
        self.assertEqual(self.dups("a00"), [])

    def test_a_record_is_not_its_own_duplicate(self):
        self.assertEqual(self.dups("a0", rid=self.a[0]), [])

    def test_the_message_names_the_record_and_the_key(self):
        (f,) = checks.review(self.store, ALICE, "strain", {"name": "a1", "fields": {}})
        self.assertEqual((f.kind, f.message), ("duplicate", f"{self.a[1]} has the same name"))
        self.assertEqual(f.records, [{"id": self.a[1], "name": "a1", "archived": False}])

    # ---- visibility
    def test_a_record_the_viewer_may_not_see_is_never_a_duplicate(self):
        self.store.set_archived(self.b[0], BOB, True)                  # archived: its creator and administrators only
        self.assertEqual(self.dups("b0", viewer=ALICE), [])
        self.assertEqual(self.dups("b0", viewer=BOB), [self.b[0]])
        (f,) = checks.review(self.store, ROOT, "strain", {"name": "b0", "fields": {}})
        self.assertTrue(f.records[0]["archived"])

    # ---- the index follows changes
    def test_new_and_changed_records_are_found_at_once(self):
        self.assertEqual(self.dups("fresh"), [])
        rid = self.store.create("strain", ALICE, "fresh", {})
        self.assertEqual(self.dups("fresh"), [rid])
        rec = self.store.records[rid]
        self.store.update(rid, ALICE, "renamed", {}, "", rec["_version"])
        self.assertEqual(self.dups("fresh"), [])
        self.assertEqual(self.dups("renamed"), [rid])

    # ---- rules files
    def test_a_key_of_several_parts(self):
        self.rules(RULES)
        s = self.store.create("strain", ALICE, "WT1 with a note", {"genotype": "∆abc ∆def", "plasmid": [self.p]})
        self.assertEqual(self.dups("wt-1 other words", genotype="Δdef Δabc", plasmid=[self.p]), [s])   # spelling, set order
        self.assertEqual(self.dups("wt-1", genotype="Δdef", plasmid=[self.p]), [])                       # a change missing
        self.assertEqual(self.dups("wt-1", genotype="Δdef Δabc", plasmid=[]), [])                        # plasmid missing
        self.assertEqual(self.dups("a0"), [])                         # the default name key is replaced by the rules
        self.assertEqual(checks.guidance(self.store, "strain"), "Names: background first.")
        self.assertEqual(checks.guidance(self.store, "plasmid"), "")

    def test_an_alias_table_makes_two_spellings_equal(self):
        self.rules(RULES)
        s = self.store.create("strain", ALICE, "WT-1", {"genotype": "x7"})
        self.assertEqual(self.dups("wt-1", genotype="fast"), [s])

    def test_a_required_part_left_empty_skips_the_key(self):
        self.rules(dict(RULES, extract={"background": {"field": "name", "pattern": "^(?P<value>WT-\\d+)"}}))
        self.store.create("strain", ALICE, "other", {"genotype": "g"})
        self.assertEqual(self.dups("another", genotype="g"), [])      # no background in either name: not compared

    def test_several_keys_any_one_is_enough(self):
        self.rules({"duplicate_keys": [{"label": "same name", "parts": [{"field": "name", "normalise": ["alnum", "casefold"]}]},
                                       {"label": "same sequence", "parts": [{"field": "genotype", "normalise": ["dna"]}]}]})
        seq = "ACGTTGCAAGGCTTACCGGATTAC"
        s = self.store.create("strain", ALICE, "p-One", {"genotype": seq})
        self.assertEqual(self.dups("P_one"), [s])
        rc = seq.translate(str.maketrans("ACGT", "TGCA"))[::-1]
        self.assertEqual(self.dups("different", genotype=rc.lower()), [s])                      # reverse complement
        self.assertEqual(self.dups("different", genotype="ACGT"), [])                            # too short to compare

    def test_min_filled_skips_a_key_that_only_has_the_shared_part(self):
        key = {"label": "same name and more", "min_filled": 2, "parts": [{"field": "name"}, {"field": "genotype"}, {"field": "plasmid"}]}
        self.rules({"duplicate_keys": [key]})
        self.store.create("strain", ALICE, "twin", {})
        self.assertEqual(self.dups("twin"), [])                                  # only the name is filled
        s = self.store.create("strain", ALICE, "twin2", {"genotype": "g"})
        self.assertEqual(self.dups("twin2", genotype="g"), [s])
        self.rules({"duplicate_keys": [dict(key, min_filled=4)]})
        self.assertTrue(any("min_filled" in w for w in self.store.schema_warnings))

    def test_both_keys_are_named_when_both_match(self):
        self.rules({"duplicate_keys": [{"label": "same name", "parts": [{"field": "name"}]},
                                       {"label": "same genotype", "parts": [{"field": "genotype"}]}]})
        s = self.store.create("strain", ALICE, "twin", {"genotype": "gx"})
        (f,) = checks.review(self.store, ALICE, "strain", {"name": "twin", "fields": {"genotype": "gx"}})
        self.assertEqual(f.message, f"{s} has the same name and the same genotype")

    def test_a_file_with_a_mistake_is_reported_and_the_name_is_compared(self):
        for doc, words in (("{not json", "only the name is compared"),
                           ({"tables": {"t": [["(", "x"]]}}, "bad pattern"),
                           ({"duplicate_keys": [{"parts": [{"field": "nope"}]}]}, "no field 'nope'"),
                           ({"duplicate_keys": [{"parts": [{"field": "name", "normalise": ["zz"]}]}]}, "unknown normaliser 'zz'"),
                           ({"extract": {"e": {"field": "name", "pattern": "x"}}}, "(?P<value>"),
                           ({"tables": {"casefold": []}}, "built-in")):
            self.rules(doc)
            self.assertTrue(any("config/checks/strain.yaml" in w and words in w for w in self.store.schema_warnings), (doc, self.store.schema_warnings))
            self.assertEqual(self.dups("a0"), [self.a[0]])

    def test_a_file_for_an_unknown_type_is_reported(self):
        self.rules({}, slug="nosuch")
        self.assertTrue(any("no type 'nosuch'" in w for w in self.store.schema_warnings))


CONV = {
    "tables": {"spelling": [["(?i)\\bdh5alfa\\b", "DH5alpha"], ["[∆𝚫]", "Δ"]]},
    "extract": {"bg": {"field": "name", "pattern": "^(?P<value>\\S+)"}, "gbg": {"field": "genotype", "pattern": "^(?P<value>\\S+)"}},
    "fields": {"name": {"fix": ["spelling"]},
               "genotype": {"fix": ["spelling"], "max_length": 40,
                            "warn": [{"extract": "bg", "message": "the background belongs in the name"},
                                     {"ref": "plasmid", "message": "plasmids are linked, not written here"},
                                     {"pattern": "(?i)\\b(into|with)\\b", "message": "looks like prose: use the notes"}]}},
    "mentions": {"plasmid": {"in": ["name", "notes"], "normalise": ["accents", "alnum", "casefold"],
                             "bracket": "\\[(?P<value>[^\\]]+)\\]", "brackets_in": ["name"]}},
    "agree": [{"extracts": ["bg", "gbg"], "message": "the name and the genotype name different backgrounds"}],
}


class Conventions(Checks):
    def setUp(self):
        super().setUp()
        self.pua = self.store.create("plasmid", ALICE, "pUA66_hi1", {})
        self.pbk = self.store.create("plasmid", ALICE, "pBackbone", {})
        self.rules(CONV)

    def review(self, name, notes="", viewer=ALICE, **fields):
        return checks.review(self.store, viewer, "strain", {"name": name, "fields": fields, "notes": notes})

    def messages(self, name, kind=None, **fields):
        return [f.message for f in self.review(name, **fields) if kind in (None, f.kind)]

    # ---- fixes
    def test_a_spelling_is_offered_as_a_fix_on_its_field(self):
        (f,) = [f for f in self.review("Dh5alfa [pUA66_hi1]", plasmid=[self.pua]) if f.kind == "fix"]
        self.assertEqual((f.field, f.suggestion, f.message), ("name", "DH5alpha [pUA66_hi1]", "Standard spelling"))
        (g,) = [f for f in self.review("MG ok", genotype="∆abc", plasmid=[]) if f.kind == "fix"]
        self.assertEqual((g.field, g.suggestion), ("genotype", "Δabc"))

    def test_a_correct_value_has_no_fix(self):
        self.assertEqual([f for f in self.review("DH5alpha [pUA66_hi1]", plasmid=[self.pua]) if f.kind == "fix"], [])

    def test_a_bracket_spelled_loosely_is_offered_as_the_records_name(self):
        for typed in ("[puA66-hi1]", "[PUA66_HI1]"):
            fixes = [f for f in self.review(f"MG {typed}", plasmid=[self.pua]) if f.kind == "fix"]
            self.assertEqual([(f.field, f.suggestion) for f in fixes], [("name", "MG [pUA66_hi1]")], typed)

    def test_a_spelling_fix_and_a_bracket_fix_become_one_suggestion(self):
        (f,) = [f for f in self.review("Dh5alfa [puA66-hi1]", plasmid=[self.pua]) if f.kind == "fix"]
        self.assertEqual(f.suggestion, "DH5alpha [pUA66_hi1]")
        self.assertEqual(f.message, "Standard spelling and the plasmid written as its record's name")

    # ---- mentions
    def test_a_plasmid_named_but_not_linked(self):
        msgs = self.messages("MG [pUA66_hi1]", "warning")
        self.assertEqual(msgs, [f"pUA66_hi1 ({self.pua}) is named in name but not linked in Plasmid"])
        self.assertEqual(self.messages("MG [pUA66_hi1]", "warning", plasmid=[self.pua]), [])
        self.assertTrue(self.messages("MG note", notes="", plasmid=[]) == [])

    def test_the_notes_are_searched_too(self):
        msgs = [f.message for f in self.review("MG", notes="made with pUA66_hi1 from Jenna")]
        self.assertEqual(msgs, [f"pUA66_hi1 ({self.pua}) is named in notes but not linked in Plasmid"])

    def test_a_name_inside_a_longer_name_is_not_a_mention(self):
        longer = self.store.create("plasmid", ALICE, "pUA66_hi1_extra", {})
        msgs = [f.message for f in self.review("MG [pUA66_hi1_extra]", plasmid=[longer])]
        self.assertEqual(msgs, [])

    def test_a_plasmid_outside_brackets(self):
        msgs = self.messages("MG with pUA66_hi1", "warning", plasmid=[self.pua])
        self.assertIn("pUA66_hi1 is written outside brackets in name; the convention is [pUA66_hi1]", msgs)

    def test_a_bracket_that_matches_no_plasmid(self):
        msgs = self.messages("MG [nothing known]", "warning")
        self.assertEqual(msgs, ["[nothing known] is not the name of a known plasmid"])

    def test_the_bracket_names_another_plasmid_than_the_linked_one(self):
        msgs = self.messages("MG [pBackbone]", "warning", plasmid=[self.pua])
        self.assertIn("the plasmid in brackets is not the one linked (pUA66_hi1)", msgs)

    def test_a_plasmid_the_person_may_not_see_is_not_looked_for(self):
        secret = self.store.create("plasmid", BOB, "pSecret_one", {})
        self.store.set_archived(secret, BOB, True)
        self.assertEqual(self.messages("MG [pSecret_one]", "warning"), ["[pSecret_one] is not the name of a known plasmid"])

    # ---- fields that hold the wrong thing
    def test_the_background_in_the_genotype(self):
        self.assertIn("the background belongs in the name", self.messages("MG1655 x", genotype="MG1655 Δabc"))
        self.assertNotIn("the background belongs in the name", self.messages("MG1655 x", genotype="Δabc"))

    def test_a_plasmid_in_the_genotype(self):
        self.assertIn("plasmids are linked, not written here", self.messages("MG1655 x", genotype="Δabc pUA66_hi1", plasmid=[self.pua]))
        self.assertNotIn("plasmids are linked, not written here", self.messages("MG1655 x", genotype="Δabc"))

    def test_prose_and_long_values_in_the_genotype(self):
        self.assertIn("looks like prose: use the notes", self.messages("MG x", genotype="integration into MG"))
        self.assertIn("Genotype is long (45 characters): is some of it prose for the notes?", self.messages("MG x", genotype="Δabc " * 9))

    def test_the_name_and_the_genotype_disagree_on_the_background(self):
        self.assertIn("the name and the genotype name different backgrounds", self.messages("SC1-G4 [pBackbone]", genotype="SC1-H1 Δabc", plasmid=[self.pbk]))
        self.assertNotIn("the name and the genotype name different backgrounds", self.messages("sc1-g4", genotype="SC1-G4 Δabc"))

    # ---- editing and the file
    def test_findings_that_a_stored_record_already_had_are_not_repeated_on_edit(self):
        rid = self.store.create("strain", ALICE, "Dh5alfa x", {})
        rec = self.store.records[rid]
        self.assertEqual(self.store.review(ALICE, "strain", "Dh5alfa x", {}, "", rid=rid), [])
        self.assertTrue(self.store.review(ALICE, "strain", "Dh5alfa y", {}, "", rid=rid))            # a changed name: a new suggestion

    def test_mistakes_in_the_new_sections_are_reported(self):
        for doc, words in (({"fields": {"nope": {}}}, "no field 'nope'"),
                           ({"fields": {"name": {"fix": ["zz"]}}}, "unknown normaliser 'zz'"),
                           ({"fields": {"name": {"warn": [{"pattern": "(", "message": "x"}]}}}, "bad pattern"),
                           ({"fields": {"genotype": {"warn": [{"ref": "genotype"}]}}}, "not a link field"),
                           ({"mentions": {"genotype": {}}}, "must be a link field"),
                           ({"mentions": {"plasmid": {"bracket": "\\[(.*)\\]"}}}, "(?P<value>"),
                           ({"agree": [{"extracts": ["a", "b"]}]}, "needs two extracts")):
            self.rules(doc)
            self.assertTrue(any(words in w for w in self.store.schema_warnings), (doc, self.store.schema_warnings))


class Normalise(unittest.TestCase):
    def test_builtins(self):
        self.assertEqual(checks.normalise("  Élan   Vital ", ["accents", "casefold", "space"], {}), "elan vital")
        self.assertEqual(checks.normalise("pUA66-bolA", ["alnum"], {}), "pUA66bolA")
        self.assertEqual(checks.normalise("acgt" * 5, ["dna"], {}), "ACGT" * 5)

    def test_tables_apply_in_order(self):
        import re
        tables = {"t": [(re.compile("a"), "b"), (re.compile("b"), "c")]}
        self.assertEqual(checks.normalise("a", ["t"], {**tables}), "c")


if __name__ == "__main__":
    unittest.main()

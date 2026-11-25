"""Record IDs (shelfblazer/ids.py): the grammar, and that no other file has its own copy of it."""
import glob, os, re, unittest

from shelfblazer import ids

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class Grammar(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(ids.parse("PL-142"), ("PL", 142, False, None))
        self.assertEqual(ids.parse("  pl-142 \n"), ("PL", 142, False, None))
        self.assertEqual(ids.parse("ST-P17"), ("ST", 17, True, None))
        self.assertEqual(ids.parse("st-p17").personal, True)
        self.assertEqual(ids.parse("st-p17~U7F3K2QA"), ("ST", 17, True, "U7F3K2QA"))                 # the key: the owner keeps its case
        for bad in ("PL142", "P-1", "PLA-1", "PL-", "PL-1a", "", None, "PL-1 PL-2", "PL-P", "PL-PP1", "PL-Q1", "PL-1~U7F3K2QA", "ST-P1~", "ST-P1~ab"):
            self.assertIsNone(ids.parse(bad), bad)

    def test_make_and_prefix(self):
        self.assertEqual(ids.make("ST", 12), "ST-12")
        self.assertEqual(ids.make("ST", 17, personal=True), "ST-P17")
        self.assertEqual(ids.make("ST", 17, True, "U7F3K2QA"), "ST-P17~U7F3K2QA")
        self.assertEqual(ids.make("ST", 17, False, "U7F3K2QA"), "ST-17")                           # a lab ID has no owner
        self.assertTrue(ids.is_personal("ST-P17"))
        for rid in ("ST-17", "", None, "ST-", "PL-P"):
            self.assertFalse(ids.is_personal(rid), rid)
        self.assertEqual(ids.prefix_of("st-12"), "ST")
        self.assertEqual(ids.prefix_of("ST-12x"), "ST")              # a folder name need not be a valid ID
        self.assertIsNone(ids.prefix_of("nothing"))
        self.assertIsNone(ids.prefix_of(None))

    def test_the_owner_makes_a_personal_id_unique_and_the_short_form_is_read_for_someone(self):
        key = "ST-P1~U7F3K2QA"
        self.assertEqual((ids.owner_of(key), ids.owner_of("ST-P1"), ids.owner_of("ST-1")), ("U7F3K2QA", None, None))
        self.assertEqual((ids.short(key), ids.short("ST-1")), ("ST-P1", "ST-1"))
        self.assertEqual((ids.qualify("ST-P1", "UBOB2222"), ids.qualify(key, "UBOB2222"), ids.qualify("ST-1", "UBOB2222")), ("ST-P1~UBOB2222", key, "ST-1"))
        name = {"U7F3K2QA": "Alice A"}.get
        self.assertEqual(ids.label(key, "U7F3K2QA", name), "ST-P1")                                 # yours
        self.assertEqual(ids.label(key, "UBOB2222", name), "ST-P1[Alice A]")                        # someone else's
        self.assertEqual(ids.label("ST-12", "UBOB2222", name), "ST-12")

    def test_ids_in_text(self):
        text = "see PL-142, (ST-3), ST-P17 and xPL-9, PL-10a, PL_ST-4, 1ST-5"
        self.assertEqual([m.group(0) for m in ids.IN_TEXT.finditer(text)], ["PL-142", "ST-3", "ST-P17"])
        self.assertEqual(ids.mentions("PL").sub(r"BS-\1", "PL-1, PL-P12; xPL-3"), "BS-1, BS-P12; xPL-3")
        both = [m.group(0) for m in ids.IN_TEXT.finditer("ST-P1~U7F3K2QA and ST-P1, ST-P2~alice.")]
        self.assertEqual(both, ["ST-P1~U7F3K2QA", "ST-P1", "ST-P2~alice"])                          # the whole key, not just ST-P1

    def test_the_browser_pattern_is_the_same(self):
        js = re.compile(ids.JS_FULL)
        for s in ("PL-142", "PL142", "pl-1", "PL-1 ", "ST-P17", "ST-P", "ST-P17~U7F3K2QA", "ST-P17~"):
            self.assertEqual(bool(js.match(s)), bool(ids.FULL.match(s)), s)

    def test_sort_key_orders_by_number_lab_records_first(self):
        recs = [{"id": "ST-10", "legacy_id": 10}, {"id": "ST-P2~UBOB2222", "legacy_id": 2}, {"id": "ST-9", "legacy_id": 9}, {"id": "X", "legacy_id": None},
                {"id": "ST-P2~UALICE22", "legacy_id": 2}]
        self.assertEqual([r["id"] for r in sorted(recs, key=ids.sort_key)], ["X", "ST-9", "ST-10", "ST-P2~UALICE22", "ST-P2~UBOB2222"])


class OneCopy(unittest.TestCase):
    """The ID grammar lives in ids.py only: another regular expression for IDs would miss a change of the grammar."""
    PATTERNS = (r"\[A-Z\]\{2\}-", r"\[A-Za-z\]\{2\}\)?-", r"\{old\}-", r'split\("-"\)')

    def test_no_other_file_parses_ids(self):
        files = [f for f in glob.glob(os.path.join(ROOT, "packages", "shelfblazer", "*.py")) + glob.glob(os.path.join(ROOT, "app", "shelfblazer_app", "*.py"))
                 + glob.glob(os.path.join(ROOT, "app", "shelfblazer_app", "static", "*.js"))
                 if os.path.basename(f) != "ids.py"]
        self.assertTrue(files)
        found = []
        for f in files:
            with open(f, encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    if any(re.search(p, line) for p in self.PATTERNS) and "the grammar of shelfblazer/ids.py" not in line:
                        found.append(f"{os.path.relpath(f, ROOT)}:{n}: {line.strip()[:100]}")
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()

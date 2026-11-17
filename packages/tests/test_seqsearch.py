import unittest

from shelfblazer import seqsearch as ss


def rec(rid, seq):
    return {"id": rid, "fields": {"sequence": seq}}


GENE = "ATGGTTTACTCCTATACCGAGAAAAAACGTTAGCCGGATTACA"


class SeqSearch(unittest.TestCase):
    def test_clean_ignores_case_spaces_headers_and_u(self):
        self.assertEqual(ss.clean(">x\nAT GC\nuu"), "atgctt")

    def test_revcomp(self):
        self.assertEqual(ss.revcomp("aacgtt"), "aacgtt")
        self.assertEqual(ss.revcomp("atgcn"), "ngcat")

    def test_exact_both_strands(self):
        recs = [rec("PR-1", GENE), rec("PR-2", "GGGGGGGGGGGG")]
        self.assertEqual([r["id"] for r, _ in ss.find(recs, "sequence", "tcc tat acc gag")], ["PR-1"])
        self.assertEqual([r["id"] for r, _ in ss.find(recs, "sequence", ss.revcomp("tcctataccgag"))], ["PR-1"])

    def test_mismatches(self):
        recs = [rec("PR-1", GENE)]
        q = "tcctattccgag"                                       # one substitution (a to t)
        self.assertEqual(ss.find(recs, "sequence", q, 0), [])
        self.assertEqual([m for _, m in ss.find(recs, "sequence", q, 1)], [1])
        self.assertEqual([m for _, m in ss.find(recs, "sequence", ss.revcomp(q), 1)], [1])
        self.assertEqual(ss.find(recs, "sequence", "tcctattcagag", 1), [])
        self.assertEqual([m for _, m in ss.find(recs, "sequence", "tcctattcagag", 2)], [2])

    def test_mismatch_at_either_end(self):
        recs = [rec("PR-1", "AAAAAATGGTTTACTCC")]
        self.assertEqual(len(ss.find(recs, "sequence", "ccgtttac", 1)), 0)      # 2 differences
        self.assertEqual(len(ss.find(recs, "sequence", "cggtttac", 1)), 1)       # first base
        self.assertEqual(len(ss.find(recs, "sequence", "atggtttag", 1)), 1)      # last base

    def test_best_hit_first(self):
        recs = [rec("PR-1", "cccccctcctgtaccgagcccc"), rec("PR-2", "cctataccgag")]
        self.assertEqual([(r["id"], m) for r, m in ss.find(recs, "sequence", "cctataccgag", 1)], [("PR-2", 0), ("PR-1", 1)])
        self.assertEqual([(r["id"], m) for r, m in ss.find(recs, "sequence", "cctgtaccgag", 1)], [("PR-1", 0), ("PR-2", 1)])

    def test_unusable_queries_find_nothing(self):
        recs = [rec("PR-1", GENE)]
        self.assertEqual(ss.find(recs, "sequence", "atg"), [])                 # too short
        self.assertEqual(ss.find(recs, "sequence", "hello world"), [])         # not a sequence
        self.assertFalse(ss.usable("atg"))
        self.assertTrue(ss.usable("atggtt"))

    def test_mismatches_are_limited(self):
        self.assertEqual(ss.allowed_mismatches("a" * 30, 99), 5)
        self.assertEqual(ss.allowed_mismatches("a" * 6, 5), 2)
        self.assertEqual(ss.allowed_mismatches("a" * 30, -3), 0)


if __name__ == "__main__":
    unittest.main()

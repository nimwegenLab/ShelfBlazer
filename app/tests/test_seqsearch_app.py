"""Sequence search on a type's list page: switched on by "sequence_search" in the type definition, both strands, a few mismatches."""
import unittest

from test_app import Base

GENE = "ATGGTTTACTCCTATACCGAGAAAAAACGTTAGCC"


def schema(search=True):
    s = {"name": "Oligo", "prefix": "OL", "list_columns": ["id", "name", "sequence"], "fields": [
        {"key": "name", "label": "Name", "type": "text", "required": True},
        {"key": "sequence", "label": "Sequence", "type": "text", "validate": "dna"}]}
    if search:
        s["sequence_search"] = "sequence"
    return s


class SequenceSearch(Base):
    def setUp(self):
        super().setUp()
        self.store.save_schema("oligo", schema(), self.users.get("root"), create=True)
        self.c = self.client("alice")
        a = self.users.get("alice")
        for name, seq in (("gene", GENE), ("other", "GGGGGGGGGGGGGGGG")):
            self.store.create("oligo", a, name, {"sequence": seq})

    def names(self, **q):
        page = self.c.get("/t/oligo", params=q).text
        return [n for n in ("gene", "other") if f">{n}<" in page or f"{n}</strong>" in page]

    def test_the_text_search_does_not_look_in_sequences(self):
        self.assertEqual(self.names(q="tcctatacc"), [])

    def test_sequence_search_finds_either_strand(self):
        self.assertEqual(self.names(q="tcc tat acc", seq="1"), ["gene"])
        self.assertEqual(self.names(q="GGTATAGGA", seq="1"), ["gene"])       # reverse complement of tcctatacc

    def test_mismatches(self):
        self.assertEqual(self.names(q="tcctattcc", seq="1"), [])
        self.assertEqual(self.names(q="tcctattcc", seq="1", mm="1"), ["gene"])

    def test_short_or_non_dna_queries_say_so(self):
        self.assertIn("at least 6 bases", self.c.get("/t/oligo", params={"q": "atg", "seq": "1"}).text)
        self.assertEqual(self.names(q="hello world", seq="1"), [])

    def test_controls_only_where_the_type_enables_it(self):
        self.assertIn('name="seq"', self.c.get("/t/oligo").text)
        self.store.save_schema("oligo", schema(search=False), self.users.get("root"))
        page = self.c.get("/t/oligo", params={"q": "tcctatacc", "seq": "1"}).text
        self.assertNotIn('name="seq"', page)
        self.assertEqual(self.names(q="tcctatacc", seq="1"), [])             # switched off: plain text search

    def test_the_setting_must_name_a_text_field(self):
        bad = schema()
        bad["sequence_search"] = "nope"
        with self.assertRaises(Exception):
            self.store.save_schema("oligo2", bad, self.users.get("root"), create=True)


if __name__ == "__main__":
    unittest.main()

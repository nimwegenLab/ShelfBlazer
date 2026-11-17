"""Computed fields in lists, records and forms."""
import unittest

from test_app import Base

N_STRAINS = {"key": "n_strains", "label": "Strains", "type": "computed", "expr": "count(referenced_by('strain'))"}
WHICH = {"key": "which", "label": "Used by", "type": "computed", "expr": "referenced_by('strain')"}
RESIST = {"key": "resist", "label": "Resistance", "type": "computed", "expr": "join(', ', unique(flatten(values(refs('plasmid'), 'resistance'))))"}


class ComputedBase(Base):
    def setUp(self):
        super().setUp()
        self.p = [self.create("alice", "plasmid", name=f"p{i}", resistance="Kan") for i in range(3)]
        self.s = [self.create("alice", name=f"s{i}", plasmid=self.p[0]) for i in range(2)]
        self.create("bob", name="s2", plasmid=self.p[1])
        pl = self.store.schemas["plasmid"]
        pl["fields"] += [N_STRAINS, WHICH]
        pl["list_columns"] = ["id", "name", "n_strains", "created_by"]
        st = self.store.schemas["strain"]
        st["fields"].append(RESIST)
        st["list_columns"] = ["id", "name", "resist", "created_by"]
        self.c = self.client("alice")


class TestLists(ComputedBase):
    def column(self, path, col):
        page = self.c.get(path).text
        import re
        rows = re.findall(r'<tr data-href="/r/(PL-\d+|ST-\d+)">(.*?)</tr>', page, re.S)
        out = []
        for rid, html in rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", html, re.S)
            out.append((rid, re.sub(r"<[^>]+>", "", cells[col]).strip()))
        return out

    def test_computed_column_shows_the_value(self):
        self.assertEqual(self.column("/t/plasmid", 3), [(self.p[0], "2"), (self.p[1], "1"), (self.p[2], "0")])
        self.assertIn("Kan", dict(self.column("/t/strain", 3))[self.s[0]])

    def test_sorting_by_a_computed_column_is_numeric_and_both_ways(self):
        asc = [r for r, _ in self.column("/t/plasmid?sort=n_strains&dir=asc", 3)]
        desc = [r for r, _ in self.column("/t/plasmid?sort=n_strains&dir=desc", 3)]
        self.assertEqual(asc, [self.p[2], self.p[1], self.p[0]])
        self.assertEqual(desc, [self.p[0], self.p[1], self.p[2]])
        for i in range(8):                                                                  # 10 > 9 numerically, not as text
            self.store.create("strain", self.users.get("alice"), f"x{i}", {"plasmid": [self.p[2]]})
        self.assertEqual(self.column("/t/plasmid?sort=n_strains&dir=desc", 3)[0][0], self.p[2])

    def test_header_label_comes_from_the_definition_and_cards_work(self):
        page = self.c.get("/t/plasmid").text
        self.assertIn(">Strains", page)
        self.assertIn("rcards", self.c.get("/t/plasmid?view=cards").text)
        self.assertEqual(self.c.get("/t/plasmid?view=cards").status_code, 200)

    def test_the_sort_choice_can_be_remembered_for_a_computed_column(self):
        self.c.get("/t/plasmid?sort=n_strains&dir=desc")
        self.assertIn('name="sort" value="n_strains"', self.c.get("/t/plasmid").text)


class TestRecordAndForms(ComputedBase):
    def test_record_page_shows_value_and_links_and_marks_it_calculated(self):
        page = self.c.get(f"/r/{self.p[0]}").text
        self.assertRegex(page, r"Strains <span[^>]*>calculated</span>")
        self.assertIn(f'<a class="rid" href="/r/{self.s[0]}" data-hue="1">{self.s[0]}</a>', page)          # a list of records becomes links
        self.assertRegex(page, r"Used by")
        self.assertIn("Kan", self.c.get(f"/r/{self.s[0]}").text)

    def test_forms_do_not_offer_computed_fields_and_ignore_forged_values(self):
        for path in (f"/r/{self.p[0]}/edit", "/t/plasmid/new"):
            page = self.c.get(path).text
            self.assertNotIn("f_n_strains", page)
            self.assertNotIn("f_which", page)
        v = self.store.records[self.p[0]]["_version"]
        self.post(self.c, f"/r/{self.p[0]}/edit", {"name": "p0", "f_n_strains": "999", "version": v})
        self.assertNotIn("n_strains", self.store.records[self.p[0]]["fields"])
        self.assertIn("2", self.c.get(f"/r/{self.p[0]}").text)

    def test_bulk_edit_does_not_offer_computed_fields(self):
        page = self.post(self.c, "/t/plasmid/bulk", {"ids": self.p}).text
        self.assertNotIn("value_n_strains", page)

    def test_updates_are_reflected_immediately(self):
        self.assertIn("1", [t for _, t in TestLists.column(self, "/t/plasmid", 3) if _ == self.p[1]])
        self.create("alice", name="s3", plasmid=self.p[1])
        self.assertEqual(dict(TestLists.column(self, "/t/plasmid", 3))[self.p[1]], "2")


class TestRobustness(ComputedBase):
    def test_a_broken_formula_leaves_the_page_working_and_the_cell_empty(self):
        self.store.schemas["plasmid"]["fields"].append({"key": "bad", "label": "Bad", "type": "computed", "expr": "__import__('os').system('echo pwned')"})
        self.store.schemas["plasmid"]["list_columns"].append("bad")
        page = self.c.get("/t/plasmid")
        self.assertEqual(page.status_code, 200)
        rec = self.c.get(f"/r/{self.p[0]}")
        self.assertEqual(rec.status_code, 200)
        self.assertIn("not available", rec.text)
        self.assertNotIn("pwned", rec.text)

    def test_text_results_are_escaped(self):
        self.store.schemas["plasmid"]["fields"].append({"key": "shout", "label": "Shout", "type": "computed", "expr": "concat(name, '<b>!</b>')"})
        pid = self.create("alice", "plasmid", name="<script>alert(1)</script>")
        page = self.c.get(f"/r/{pid}").text
        self.assertNotIn("<script>alert(1)", page)
        self.assertNotIn("<b>!</b>", page)

    def test_search_does_not_index_computed_values(self):
        self.assertEqual([d["id"] for d in self.app.state.index.search("calculated-only-term")], [])


if __name__ == "__main__":
    unittest.main()

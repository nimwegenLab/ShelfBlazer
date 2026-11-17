"""Live filtering while typing: the markup the script relies on, and that the server answers the fetched URLs with exactly the results."""
import os, re, unittest

from test_app import Base

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shelfblazer_app", "static")


def js(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


class TestLive(Base):
    def setUp(self):
        super().setUp()
        for n in ("alpha one", "alpha two", "beta"):
            self.create("alice", name=n)
        self.c = self.client("alice")

    def region(self, html):
        m = re.search(r'<div id="results">(.*?)</div>\n<script', html, re.S) or re.search(r'<div id="results">(.*)', html, re.S)
        self.assertTrue(m, "no #results region")
        return m.group(1)

    def test_list_page_has_the_live_form_results_area_total_and_scripts(self):
        page = self.c.get("/t/strain").text
        self.assertIn('class="tools" data-live', page)
        self.assertEqual(page.count('id="results"'), 1)
        self.assertIn('id="total"', page)
        self.assertIn('<script src="/static/live.js" defer>', page)
        self.assertIn('<script src="/static/bulk.js" defer>', page)
        self.assertNotIn("<script>", page)                                       # still no inline script (CSP)
        self.assertGreater(page.index('id="results"'), page.index('data-live'))      # the form itself is never replaced

    def test_the_results_area_holds_the_table_and_the_bulk_form_only(self):
        page = self.c.get("/t/strain").text
        results = page[page.index('id="results"'):]
        self.assertIn('id="bulk"', results)
        self.assertIn("<table", results)
        self.assertNotIn('type="search"', results)

    def test_the_fetched_url_returns_only_matching_rows_and_the_count(self):
        page = self.c.get("/t/strain?q=alpha&sort=id&dir=asc&view=table&owner=").text
        self.assertIn("alpha one", page)
        self.assertIn("alpha two", page)
        self.assertNotIn(">beta<", page)
        self.assertRegex(page, r'id="total"[^>]*>2<')
        self.assertIn('name="q" value="alpha"', page)                              # the bulk form carries the live filter
        none = self.c.get("/t/strain?q=zzzz").text
        self.assertIn("Nothing matches these filters", none)
        self.assertRegex(none, r'id="total"[^>]*>0<')

    def test_cards_and_empty_states_are_inside_the_results_area_too(self):
        page = self.c.get("/t/strain?view=cards&q=beta").text
        self.assertIn("rcards", page[page.index('id="results"'):])
        self.assertIn("beta", page[page.index('id="results"'):])

    def test_home_search_is_live_and_the_exact_id_still_jumps_on_enter(self):
        home = self.c.get("/").text
        self.assertIn('class="search" data-live', home)
        self.assertIn('id="results"', home)
        self.assertIn('<script src="/static/live.js" defer>', home)
        r = self.c.get("/?q=alpha")
        self.assertEqual(r.status_code, 200)
        self.assertIn("alpha one", r.text[r.text.index('id="results"'):])
        self.assertEqual(self.c.get("/?q=ST-1").status_code, 303)                  # the script ignores redirects; Enter follows them

    def test_hidden_records_never_appear_in_live_results(self):
        self.store.save_schema("plasmid", {**self.store.schemas["plasmid"], "permissions": {"read": "admin"}}, self.users.get("root"))
        self.create("root", "plasmid", name="alpha secret plasmid")
        for who, expect in (("alice", False), ("root", True)):
            self.assertEqual("alpha secret plasmid" in self.client(who).get("/?q=alpha").text, expect, who)

    def test_script_behaviour_is_in_the_file(self):
        s = js("live.js")
        for needle in ('form[data-live]', 'getElementById("results")', '"input"', "setTimeout", "r.redirected", "mine !== seq", "replaceState",
                       'new CustomEvent("results-updated")', "URLSearchParams"):
            self.assertIn(needle, s)
        self.assertEqual(re.findall(r"innerHTML\s*=\s*([^;]+);", s), ["fresh.innerHTML"])      # only the parsed server fragment is inserted
        self.assertNotIn("eval(", s)
        b = js("bulk.js")
        self.assertIn('addEventListener("results-updated", initList)', b)             # new rows get their checkboxes wired up

    def test_the_fuzzy_switch_is_inside_the_search_field_and_on_by_default(self):
        page = self.c.get("/t/strain").text
        self.assertRegex(page, r'<span class="searchbox(?: wide)?"><input type="search" name="q"[^>]*><input type="hidden" name="fuzzy" value="0">(?:<span class="fzs">)?<label class="fz"[^>]*><input type="checkbox" name="fuzzy" value="1" checked> fuzzy</label>(?:</span>)?</span>')
        off = self.c.get("/t/strain?fuzzy=0").text
        self.assertNotRegex(off, r'name="fuzzy" value="1" checked')
        self.assertIn("fuzzy=0", off)                                              # sort and page links keep the choice
        self.assertIn('name="fuzzy" value="0"><label class="fz"', self.c.get("/").text)

    def test_typos_are_found_only_while_fuzzy_is_on(self):
        self.create("alice", name="Ampicillin stock")
        self.assertIn("Ampicillin stock", self.c.get("/t/strain?q=Ampicilin").text)               # one letter missing
        self.assertNotIn("Ampicillin stock", self.c.get("/t/strain?q=Ampicilin&fuzzy=0").text)
        self.assertIn("Ampicillin stock", self.c.get("/t/strain?q=Ampicillin&fuzzy=0").text)
        self.assertNotIn("Ampicillin stock", self.c.get("/?q=Ampicilin&fuzzy=0").text)
        self.assertIn("Ampicillin stock", self.c.get("/?q=Ampicilin").text)

    def test_an_id_is_never_matched_fuzzily(self):
        for n in range(1, 8):
            self.create("alice", name=f"strain {n}")
        for q in ("ST-1", "st-1", "ST-8", "ST-12"):
            page = self.c.get("/t/strain", params={"q": q}).text
            ids = set(re.findall(r'data-href="/r/(ST-\d+)"', page))
            expect = {f"ST-{n}" for n in range(1, 11) if f"st-{n}".startswith(q.lower()) or q.lower() == f"st-{n}"}
            self.assertEqual(ids, expect, q)
        self.assertEqual(set(re.findall(r'data-href="/r/(ST-\d+)"', self.c.get("/t/strain", params={"q": "ST-99"}).text)), set())

    def test_live_search_of_a_full_id_shows_that_record_instead_of_jumping(self):
        self.assertEqual(self.c.get("/?q=ST-1").status_code, 303)                           # Enter: go to the record
        r = self.c.get("/?q=ST-1", headers={"X-Live": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertIn('<a class="rid" href="/r/ST-1">ST-1</a>', r.text[r.text.index('id="results"'):])
        self.assertIn('"X-Live"', js("live.js"))

    def test_files_exist_only_where_a_type_has_a_file_field(self):
        sid = self.create("alice", name="a")
        page = self.c.get(f"/r/{sid}").text
        self.assertNotIn("Files</h2>", page)                                           # no generic Files section
        self.assertNotIn("No files attached yet", page)
        self.assertNotIn('type="file"', self.c.get("/t/strain/new").text)
        self.assertNotIn("can be attached on the record page", self.c.get("/t/strain/new").text)
        pid = self.create("alice", "plasmid", name="p")
        self.assertIn('<h2 id="files-attachments">Files</h2>', self.c.get(f"/r/{pid}").text)           # the imported Files field of plasmids
        self.assertIn('type="file" name="f_attachments"', self.c.get("/t/plasmid/new").text)

    def test_small_text_in_forms_matches_the_compact_scale(self):
        css = js("style.css")
        self.assertNotRegex(css, r"font-size:[\d.]+rem")
        self.assertRegex(css, r"form\.edit label\.chk[^{]*\{[^}]*font-size:13px")

    def test_static_file_is_served(self):
        self.assertEqual(self.c.get("/static/live.js").status_code, 200)


if __name__ == "__main__":
    unittest.main()

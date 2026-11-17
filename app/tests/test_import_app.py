"""Import from CSV/TSV through the admin pages: upload, columns, check, apply, undo."""
import re, unittest

from test_app import Base


class TestImportApp(Base):
    def setUp(self):
        super().setUp()
        self.root, self.alice = self.client("root"), self.client("alice")
        self.s1 = self.create("alice", name="old one", genotype="g")

    def upload(self, c, content, name="strains.tsv", **extra):
        data = {"csrf_token": self.token(c), **extra}
        return c.post("/admin/types/strain/import", data=data, files={"file": (name, content, "text/plain")})

    def step(self, c, path, text, mapping, **extra):
        data = {"text": text, **{f"map_{i}": m for i, m in enumerate(mapping)}, **extra}
        return self.post(c, f"/admin/types/strain/import/{path}", data)

    @staticmethod
    def hidden_text(page):
        import html
        return html.unescape(re.search(r'<textarea name="text" hidden>(.*?)</textarea>', page, re.S).group(1))

    def test_administrators_only(self):
        self.assertEqual(self.alice.get("/admin/types/strain/import").status_code, 403)
        self.assertEqual(self.upload(self.alice, b"name\nx\n").status_code, 403)
        self.assertEqual(self.step(self.alice, "preview", "name\nx\n", ["name"]).status_code, 403)
        self.assertEqual(self.step(self.alice, "apply", "name\nx\n", ["name"]).status_code, 403)
        self.assertIn(self.client().get("/admin/types/strain/import").status_code, (303, 401, 403))
        self.assertEqual(self.root.get("/admin/types/strain/import").status_code, 200)
        self.assertEqual(self.root.get("/admin/types/nope/import").status_code, 404)

    def test_the_type_page_links_to_the_import(self):
        self.assertIn('href="/admin/types/strain/import"', self.root.get("/admin/types/strain").text)

    def test_posts_need_the_csrf_token(self):
        r = self.root.post("/admin/types/strain/import/apply", data={"text": "name\nx\n", "map_0": "name"})
        self.assertEqual(r.status_code, 403)

    def test_a_file_leads_to_the_column_page_with_titles_matched(self):
        r = self.upload(self.root, "ID\tName\tGenotype\tOwner\n\tnew\tg\tbob\n".encode())
        self.assertEqual(r.status_code, 200)
        self.assertIn("<strong>1</strong> data row", r.text)
        self.assertIn("tab-separated", r.text)
        self.assertRegex(r.text, r'<select name="map_0"[^>]*>.*?<option value="id" selected>')
        self.assertRegex(r.text, r'<select name="map_2"[^>]*>.*?<option value="genotype" selected>')
        self.assertNotRegex(r.text, r'<select name="map_3"[^>]*>.*?<option value="[a-z_]+" selected>')      # Owner is not importable: ignore
        self.assertEqual(self.hidden_text(r.text), "ID\tName\tGenotype\tOwner\n\tnew\tg\tbob\n")

    def test_bad_files_are_explained(self):
        for content, word in ((b"", "empty"), (b"name\n", "no data rows"), (b"\x00\x01\x02", "text file")):
            r = self.upload(self.root, content)
            self.assertEqual(r.status_code, 422)
            self.assertIn(word, r.text)
        self.assertEqual(self.root.post("/admin/types/strain/import", data={"csrf_token": self.token(self.root)}).status_code, 422)

    def test_the_check_shows_every_row_and_blocks_on_a_problem(self):
        text = f"ID\tName\tStatus\n\tfine\tok\n{self.s1}\t\tnope\n"
        r = self.step(self.root, "preview", text, ["id", "name", "status"])
        self.assertEqual(r.status_code, 200)
        self.assertIn("<strong>1</strong> new", r.text)
        self.assertIn("<strong>1</strong> with problems", r.text)
        self.assertIn("not one of the allowed options (ok, bad)", r.text)
        self.assertIn("Nothing was imported", r.text)
        self.assertNotIn("/import/apply\">Import", r.text)                                       # no way to import with a problem
        self.assertIn("Change the columns", r.text)

    def test_the_check_offers_the_import_when_all_rows_are_fine(self):
        r = self.step(self.root, "preview", "Name\tGenotype\nnew\tg1\n", ["name", "genotype"])
        self.assertRegex(r.text, r'formaction="/admin/types/strain/import/apply">Import 1 record</button>')
        self.assertIn("Genotype:</span> g1", r.text)

    def test_apply_creates_and_updates_in_one_commit_and_can_be_undone(self):
        text = f"ID\tName\tGenotype\n\tnew one\tgn\n{self.s1}\t\tupdated\n"
        before = len(self.store.records)
        r = self.step(self.root, "apply", text, ["id", "name", "genotype"])
        self.assertEqual(r.status_code, 303)                                                       # redirected: a refresh cannot import again
        self.assertRegex(r.headers["location"], r"^/admin/types/strain/import/done/[0-9a-f]{12}$")
        r = self.root.get(r.headers["location"])
        self.assertEqual(r.status_code, 200)
        self.assertIn("Import done", r.text)
        self.assertIn("<strong>1</strong> new · <strong>1</strong> updated", r.text)
        self.assertEqual(len(self.store.records), before + 1)
        self.assertEqual(self.store.records[self.s1]["fields"]["genotype"], "updated")
        self.assertEqual(self.store.records[self.s1]["name"], "old one")                           # blank name cell: kept
        new = [rid for rid, rec in self.store.records.items() if rec["name"] == "new one"][0]
        self.assertEqual(self.store.records[new]["created_by"], self.uid("root"))
        sha = re.search(r'name="sha" value="([0-9a-f]+)"', r.text).group(1)
        self.assertIn(f'href="/r/{new}"', r.text)
        r = self.post(self.root, "/bulk/undo", {"sha": sha, "back": "/t/strain"})
        self.assertEqual(r.status_code, 303)
        self.assertNotIn(new, self.store.records)
        self.assertEqual(self.store.records[self.s1]["fields"]["genotype"], "g")

    def test_the_done_page_is_admin_only_and_only_for_imports_of_that_type(self):
        r = self.step(self.root, "apply", "Name\nx\n", ["name"])
        url = r.headers["location"]
        self.assertEqual(self.alice.get(url).status_code, 403)
        self.assertEqual(self.root.get(url.replace("/strain/", "/plasmid/")).status_code, 404)
        self.assertEqual(self.root.get("/admin/types/strain/import/done/zzzzzzz").status_code, 404)
        self.assertEqual(self.root.get("/admin/types/strain/import/done/" + self.store.git("rev-list", "--max-parents=0", "HEAD").strip()[:12]).status_code, 404)  # not an import commit

    def test_apply_with_a_problem_changes_nothing(self):
        before = dict(self.store.records)
        r = self.step(self.root, "apply", "Name\tStatus\nfine\tok\nbad\tnope\n", ["name", "status"])
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.store.records.keys(), before.keys())

    def test_nothing_to_change_is_said_so(self):
        r = self.step(self.root, "apply", f"ID\tGenotype\n{self.s1}\tg\n", ["id", "genotype"])
        self.assertEqual(r.status_code, 200)
        self.assertIn("Nothing to import", r.text)

    def test_a_wrong_column_choice_goes_back_to_the_columns(self):
        r = self.step(self.root, "preview", "a\tb\nx\ty\n", ["genotype", "genotype"])
        self.assertEqual(r.status_code, 422)
        self.assertIn("two columns are assigned to the same target", r.text)
        self.assertIn('name="map_0"', r.text)

    def test_changing_the_columns_keeps_the_choice(self):
        r = self.step(self.root, "../import", "ID\tName\tGenotype\n\tx\ty\n", ["", "name", ""], remapped="1")
        self.assertEqual(r.status_code, 200)
        self.assertNotRegex(r.text, r'<select name="map_0"[^>]*>.*?<option value="id" selected>')         # not matched again by title
        self.assertRegex(r.text, r'<select name="map_1"[^>]*>.*?<option value="name" selected>')

    def test_comma_and_semicolon_files_and_windows_text(self):
        for text in ("Name,Genotype\nA,g\n", "Name;Genotype\nA;g\n"):
            r = self.upload(self.root, text.encode())
            self.assertRegex(r.text, r'<select name="map_1"[^>]*>.*?<option value="genotype" selected>')
        r = self.upload(self.root, "Name\tGenotype\nMüller\tg\n".encode("cp1252"))
        self.assertIn("Müller", self.hidden_text(r.text))


if __name__ == "__main__":
    unittest.main()

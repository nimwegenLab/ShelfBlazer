"""Import from CSV/TSV: upload, columns, check, apply, undo; for administrators and for everyone who may add records of the type."""
import re, unittest

from test_app import Base, STRAIN


class TestImportApp(Base):
    def setUp(self):
        super().setUp()
        self.root, self.alice = self.client("root"), self.client("alice")
        self.s1 = self.create("alice", name="old one", genotype="g")

    def upload(self, c, content, name="strains.tsv", **extra):
        data = {"csrf_token": self.token(c), **extra}
        return c.post("/t/strain/import", data=data, files={"file": (name, content, "text/plain")})

    def step(self, c, path, text, mapping, **extra):
        data = {"text": text, **{f"map_{i}": m for i, m in enumerate(mapping)}, **extra}
        return self.post(c, f"/t/strain/import/{path}", data)

    @staticmethod
    def hidden_text(page):
        import html
        return html.unescape(re.search(r'<textarea name="text" hidden>(.*?)</textarea>', page, re.S).group(1))

    def test_the_pages_need_a_login_and_a_known_type(self):
        self.assertIn(self.client().get("/t/strain/import").status_code, (303, 401, 403))
        self.assertEqual(self.root.get("/t/strain/import").status_code, 200)
        self.assertEqual(self.root.get("/t/nope/import").status_code, 404)

    def test_an_empty_template_with_the_column_titles_of_the_type(self):
        page = self.alice.get("/t/strain/import").text
        self.assertIn('href="/t/strain/import/template"', page)
        r = self.alice.get("/t/strain/import/template")
        self.assertEqual(r.status_code, 200)
        self.assertIn('filename="strain-import-template.tsv"', r.headers["content-disposition"])
        self.assertTrue(r.text.startswith("\ufeffName\t") and r.text.endswith("\tNotes\n") and r.text.count("\n") == 1)
        from shelfblazer import tableimport as ti
        header, rows, delimiter = ti.parse(ti.decode(r.content + b"my strain\n"))               # the import reads it back, with a row filled in
        self.assertEqual((delimiter, [cells[0] for _, cells in rows]), ("\t", ["my strain"]))
        self.assertEqual(header, ti.template_header(self.store, "strain"))
        self.assertEqual(ti.auto_map(header, ti.targets(self.store, "strain"))[0], "name")      # every title is matched for the person
        self.assertNotIn("ID", header)
        self.assertEqual(self.client().get("/t/strain/import/template").status_code in (303, 401, 403), True)
        self.assertEqual(self.alice.get("/t/nope/import/template").status_code, 404)

    def test_the_old_admin_address_moves(self):
        r = self.root.get("/admin/types/strain/import", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (308, "/t/strain/import"))

    def test_the_type_page_and_the_list_link_to_the_import(self):
        self.assertIn('href="/t/strain/import"', self.root.get("/admin/types/strain").text)
        self.assertIn('href="/t/strain/import"', self.alice.get("/t/strain").text)
        self.assertIn('href="/t/strain/import"', self.alice.get("/t/strain/new").text)

    def test_posts_need_the_csrf_token(self):
        r = self.root.post("/t/strain/import/apply", data={"text": "name\nx\n", "map_0": "name"})
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
        self.assertEqual(self.root.post("/t/strain/import", data={"csrf_token": self.token(self.root)}).status_code, 422)

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
        self.assertRegex(r.text, r'formaction="/t/strain/import/apply">Import 1 record</button>')
        self.assertIn("Genotype:</span> g1", r.text)

    def test_apply_creates_and_updates_in_one_commit_and_can_be_undone(self):
        text = f"ID\tName\tGenotype\n\tnew one\tgn\n{self.s1}\t\tupdated\n"
        before = len(self.store.records)
        r = self.step(self.root, "apply", text, ["id", "name", "genotype"])
        self.assertEqual(r.status_code, 303)                                                       # redirected: a refresh cannot import again
        self.assertRegex(r.headers["location"], r"^/t/strain/import/done/[0-9a-f]{12}$")
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

    def test_the_done_page_is_for_the_person_who_ran_it_and_administrators_and_for_that_type(self):
        r = self.step(self.alice, "apply", "Name\nx\n", ["name"])
        url = r.headers["location"]
        self.assertEqual(self.alice.get(url).status_code, 200)
        self.assertEqual(self.root.get(url).status_code, 200)
        self.assertEqual(self.client("bob").get(url).status_code, 404)
        self.assertEqual(self.root.get(url.replace("/strain/", "/plasmid/")).status_code, 404)
        self.assertEqual(self.root.get("/t/strain/import/done/zzzzzzz").status_code, 404)
        self.assertEqual(self.root.get("/t/strain/import/done/" + self.store.git("rev-list", "--max-parents=0", "HEAD").strip()[:12]).status_code, 404)  # not an import commit

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


class TestImportByEveryone(Base):
    """A person who is not an administrator: new rows into the lab collection (where the type lets everybody add) and updates of what they may edit."""
    def setUp(self):
        super().setUp()
        self.alice, self.bob, self.root = self.client("alice"), self.client("bob"), self.client("root")
        self.mine = self.create("alice", name="alice's strain", genotype="g")
        self.bobs = self.create("bob", name="bob's strain", genotype="g")

    def step(self, c, path, text, mapping, **extra):
        return self.post(c, f"/t/strain/import/{path}", {"text": text, **{f"map_{i}": m for i, m in enumerate(mapping)}, **extra})

    def test_new_rows_go_into_the_lab_collection_owned_by_the_importer(self):
        r = self.step(self.alice, "apply", "Name\nfresh\n", ["name"])
        self.assertEqual(r.status_code, 303)
        rid = [i for i, rec in self.store.records.items() if rec["name"] == "fresh"][0]
        self.assertEqual(self.store.records[rid]["created_by"], self.uid("alice"))
        self.assertEqual(self.alice.get(r.headers["location"]).status_code, 200)

    def test_an_update_needs_the_right_to_edit_that_record(self):
        text = f"ID\tGenotype\n{self.mine}\tnew\n{self.bobs}\tnew\n"
        r = self.step(self.alice, "preview", text, ["id", "genotype"])
        self.assertIn("you may not change " + self.bobs, r.text)
        self.assertNotIn("/import/apply\">Import", r.text)                                    # one problem stops the whole file
        r = self.step(self.alice, "apply", text, ["id", "genotype"])
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.store.records[self.mine]["fields"]["genotype"], "g")
        r = self.step(self.alice, "apply", f"ID\tGenotype\n{self.mine}\tnew\n", ["id", "genotype"])
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.store.records[self.mine]["fields"]["genotype"], "new")
        self.assertEqual(self.step(self.root, "apply", f"ID\tGenotype\n{self.bobs}\tby admin\n", ["id", "genotype"]).status_code, 303)

    def test_a_record_the_person_cannot_see_looks_missing(self):
        self.store.set_archived(self.bobs, self.users.get("bob"), True)
        r = self.step(self.alice, "preview", f"ID\tGenotype\n{self.bobs}\tx\n", ["id", "genotype"])
        self.assertIn(f"{self.bobs} does not exist", r.text)
        r = self.step(self.alice, "preview", "Name\nbob's strain\n", ["name"])               # nor is its name a duplicate she is told about
        self.assertNotIn(self.bobs, r.text)

    def test_a_type_only_administrators_may_add_to_refuses_everyone_else(self):
        self.store.save_schema("strain", dict(STRAIN, permissions={"create": "admin"}), self.users.get("root"))
        self.assertEqual(self.alice.get("/t/strain/import").status_code, 403)
        self.assertEqual(self.step(self.alice, "apply", "Name\nx\n", ["name"]).status_code, 403)
        self.assertEqual(self.root.get("/t/strain/import").status_code, 200)
        self.assertNotIn("/t/strain/import", self.alice.get("/t/strain").text)                 # no button either

    def test_a_hidden_type_is_not_found(self):
        self.store.save_schema("strain", dict(STRAIN, permissions={"read": "admin"}), self.users.get("root"))
        self.assertEqual(self.alice.get("/t/strain/import").status_code, 404)
        self.assertEqual(self.step(self.alice, "apply", "Name\nx\n", ["name"]).status_code, 404)

    def test_the_posts_need_the_csrf_token(self):
        self.assertEqual(self.alice.post("/t/strain/import/apply", data={"text": "name\nx\n", "map_0": "name"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()

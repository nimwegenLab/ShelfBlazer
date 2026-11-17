"""Export through the web pages: the button, the column page, the download."""
import csv, io, os, unittest

from test_app import Base

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shelfblazer_app", "static")


class TestExportApp(Base):
    def setUp(self):
        super().setUp()
        self.a = [self.create("alice", name=f"a{i}", genotype="g") for i in range(3)]
        self.b = self.create("bob", name="b0", genotype="h")
        self.alice, self.bob = self.client("alice"), self.client("bob")

    def test_the_list_has_an_export_button_in_the_select_bar(self):
        page = self.alice.get("/t/strain").text
        self.assertIn('id="bulk-export" class="btn" formaction="/t/strain/export"', page)
        with open(os.path.join(STATIC, "bulk.js"), encoding="utf-8") as fh:
            self.assertIn("e.submitter === exp", fh.read())            # exporting does not use up the selection

    def test_the_column_page_lists_the_columns_and_keeps_the_ids(self):
        r = self.post(self.alice, "/t/strain/export", {"ids": self.a[:2]})
        self.assertEqual(r.status_code, 200)
        self.assertIn("<strong>2</strong> selected", r.text)
        for label in ("ID", "Name", "Genotype", "Owner", "Created", "Archived"):
            self.assertIn(f"> {label}</label>", r.text)
        self.assertNotIn("Legacy owner", r.text)
        self.assertEqual(r.text.count('name="col"'), 9)
        self.assertEqual(r.text.count('name="ids"'), 2)

    def test_download_gives_a_tsv_file_with_the_chosen_columns(self):
        r = self.post(self.alice, "/t/strain/export/download", {"ids": self.a, "col": ["id", "name", "created_by"]})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/tab-separated-values"))
        self.assertRegex(r.headers["content-disposition"], r'^attachment; filename="strain-\d{4}-\d\d-\d\d\.tsv"$')
        rows = list(csv.reader(io.StringIO(r.text), delimiter="\t"))
        self.assertEqual(rows[0], ["ID", "Name", "Owner"])
        self.assertEqual(rows[1:], [[i, f"a{n}", "Alice A"] for n, i in enumerate(self.a)])

    def test_nothing_selected_or_no_column_goes_back_with_a_message(self):
        r = self.post(self.alice, "/t/strain/export", {})
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/t/strain"))
        r = self.post(self.alice, "/t/strain/export/download", {"ids": self.a})
        self.assertEqual(r.status_code, 422)
        self.assertIn("Choose at least one column.", r.text)
        self.assertEqual(r.text.count('name="ids"'), 3)

    def test_only_records_you_may_read_and_of_this_type_are_exported(self):
        self.post(self.alice, f"/r/{self.a[0]}/archive")
        r = self.post(self.bob, "/t/strain/export/download", {"ids": [self.a[0], self.a[1], self.b, "PL-1", "../x"], "col": ["id"]})
        self.assertEqual([x[0] for x in csv.reader(io.StringIO(r.text), delimiter="\t")][1:], [self.a[1], self.b])

    def test_a_hidden_type_cannot_be_exported_and_login_is_needed(self):
        self.assertEqual(self.post(self.alice, "/t/nope/export", {"ids": ["x"]}).status_code, 404)
        anon = self.client()
        self.assertIn(anon.post("/t/strain/export/download", data={"ids": self.a, "col": ["id"]}).status_code, (303, 401, 403))

    def test_posts_need_the_csrf_token(self):
        r = self.post(self.alice, "/t/strain/export/download", {"ids": self.a, "col": ["id"]}, token=False)
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()

"""Storage places: a dropdown with "New place…" at the bottom; a place nobody uses any more is no longer offered."""
import json, os, re, unittest

from test_app import Base, git

SHELF = {"name": "Reagent", "prefix": "RG", "list_columns": ["id", "name", "storage_place"], "fields": [
    {"key": "name", "label": "Name", "type": "text", "required": True},
    {"key": "storage_place", "label": "Storage place", "type": "select", "source": "storage_places"}]}


class Places(Base):
    def setUp(self):
        super().setUp()
        self.store.save_schema("reagent", json.loads(json.dumps(SHELF)), self.users.get("root"), create=True)
        self.fridge = self.store.add_place("Refrigerator", self.users.get("alice"))
        self.freezer = self.store.add_place("-20 Freezer", self.users.get("alice"))
        self.c = self.client("alice")

    def options(self, html):
        sel = re.search(r'<select name="f_storage_place">(.*?)</select>', html, re.S).group(1)
        return re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', sel)

    def used(self):
        return [p["id"] for p in self.store.places_in_use()]

    def new(self, name, **extra):
        return self.post(self.c, "/t/reagent/new", {"name": name, **extra})


class TestDropdown(Places):
    def test_only_places_in_use_are_offered_with_new_place_last(self):
        self.assertEqual(self.options(self.c.get("/t/reagent/new").text), [("", ""), ("__new__", "New place…")])    # nothing uses a place yet
        self.new("a", f_storage_place=self.fridge)
        opts = self.options(self.c.get("/t/reagent/new").text)
        self.assertEqual(opts, [("", ""), (self.fridge, "Refrigerator"), ("__new__", "New place…")])
        self.assertIn('class="newplace"', self.c.get("/t/reagent/new").text)

    def test_a_place_disappears_when_the_last_record_stops_using_it(self):
        rid = self.create("alice", "reagent", name="a", storage_place=self.fridge)
        self.assertEqual(self.used(), [self.fridge])
        v = self.version(rid)
        self.post(self.c, f"/r/{rid}/edit", {"name": "a", "f_storage_place": "", "version": v})
        self.assertEqual(self.used(), [])
        self.assertNotIn(self.fridge, dict(self.options(self.c.get("/t/reagent/new").text)))
        self.assertTrue(any(p["id"] == self.fridge for p in self.store.places))          # still in the file: it comes back when used again
        self.assertEqual(self.store.add_place("refrigerator", self.users.get("alice")), self.fridge)   # same name reuses the ID

    def test_purging_the_last_record_also_frees_the_place(self):
        rid = self.create("alice", "reagent", name="a", storage_place=self.freezer)
        self.purge(rid)
        self.assertEqual(self.used(), [])

    def test_the_edit_form_keeps_the_current_place_selected(self):
        rid = self.create("alice", "reagent", name="a", storage_place=self.fridge)
        page = self.c.get(f"/r/{rid}/edit").text
        self.assertRegex(page, rf'<option value="{self.fridge}" selected>Refrigerator</option>')
        self.assertIn("__new__", page)

    def test_bulk_edit_offers_places_without_the_new_entry(self):
        rid = self.create("alice", "reagent", name="a", storage_place=self.fridge)
        page = self.post(self.c, "/t/reagent/bulk", {"ids": [rid]}).text
        self.assertNotIn("__new__", page)
        self.assertNotIn("newplace", page)


class TestNewPlace(Places):
    def test_choosing_new_with_a_name_creates_the_place_and_uses_it(self):
        r = self.new("a", f_storage_place="__new__", f_storage_place_new="  Acids   cabinet 2 ")
        self.assertEqual(r.status_code, 303)
        rec = self.store.records[r.headers["location"].split("/")[-1]]
        pid = rec["fields"]["storage_place"]
        self.assertEqual(next(p["name"] for p in self.store.places if p["id"] == pid), "Acids cabinet 2")
        self.assertEqual(pid, "acids-cabinet-2")
        self.assertIn(pid, self.used())
        self.assertIn(f"{self.uid('alice')}@shelfblazer.invalid", git(self.repo, "log", "--format=%ae", "-3"))
        with open(os.path.join(self.repo, "config", "storage_places.yaml"), encoding="utf-8") as fh:
            self.assertIn("Acids cabinet 2", fh.read())
        self.assertIn("storage place: add Acids cabinet 2", git(self.repo, "log", "-1", "--format=%b"))   # in the record's own commit

    def test_a_name_matching_an_existing_place_reuses_it(self):
        n = len(self.store.places)
        r = self.new("a", f_storage_place="__new__", f_storage_place_new="REFRIGERATOR")
        self.assertEqual(self.store.records[r.headers["location"].split("/")[-1]]["fields"]["storage_place"], self.fridge)
        self.assertEqual(len(self.store.places), n)

    def test_new_without_a_name_is_an_error_and_creates_nothing(self):
        n = len(self.store.places)
        r = self.new("a", f_storage_place="__new__", f_storage_place_new="  ")
        self.assertEqual(r.status_code, 422)
        self.assertIn("type the name of the new place", r.text)
        self.assertEqual(len(self.store.places), n)
        self.assertEqual([x for x in self.store.records.values() if x["type"] == "reagent"], [])

    def test_a_too_long_name_is_refused(self):
        r = self.new("a", f_storage_place="__new__", f_storage_place_new="x" * 61)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(len(self.store.places), 2)

    def test_the_name_is_only_text_never_markup(self):
        r = self.new("a", f_storage_place="__new__", f_storage_place_new="<script>alert(1)</script>")
        rid = r.headers["location"].split("/")[-1]
        for page in (self.c.get(f"/r/{rid}").text, self.c.get("/t/reagent").text, self.c.get(f"/r/{rid}/edit").text):
            self.assertNotIn("<script>alert", page)

    def test_a_failed_save_adds_no_place_but_keeps_what_was_typed(self):
        n, commits = len(self.store.places), self.store.git("rev-list", "--count", "HEAD")
        r = self.new("", f_storage_place="__new__", f_storage_place_new="Cold room")        # the title is missing
        self.assertEqual(r.status_code, 422)
        self.assertRegex(r.text, r'<option value="__new__" selected>New place…</option>')
        self.assertIn('name="f_storage_place_new" value="Cold room"', r.text)
        self.assertEqual((len(self.store.places), self.store.git("rev-list", "--count", "HEAD")), (n, commits))
        r = self.new("ok", f_storage_place="__new__", f_storage_place_new="Cold room")      # fixed: place and record in one commit
        self.assertEqual(r.status_code, 303)
        self.assertEqual(int(self.store.git("rev-list", "--count", "HEAD")), int(commits) + 1)
        self.assertIn("storage place: add Cold room", self.store.git("log", "-1", "--format=%b"))

    def test_edit_can_also_create_a_place(self):
        rid = self.create("alice", "reagent", name="a")
        self.post(self.c, f"/r/{rid}/edit", {"name": "a", "f_storage_place": "__new__", "f_storage_place_new": "Shelf B", "version": self.version(rid)})
        self.assertEqual(self.store.records[rid]["fields"]["storage_place"], "shelf-b")

    def test_core_rules(self):
        with self.assertRaises(Exception):
            self.store.add_place("x", None)
        self.assertEqual(self.store.add_place("Shelf", self.users.get("bob")), "shelf")
        self.assertEqual(self.store.add_place("Shelf!", self.users.get("bob")), "shelf-2")     # same slug, different name: unique ID


class TestScript(unittest.TestCase):
    def test_the_dropdown_script_toggles_the_text_field(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", "shelfblazer_app", "static", "picker.js"), encoding="utf-8") as fh:
            js = fh.read()
        for needle in ("input.newplace", '"__new__"', "text.hidden", "text.required"):
            self.assertIn(needle, js)


if __name__ == "__main__":
    unittest.main()

"""How lists and records show data: clickable rows, remembered sort and view, storage locations (short, outdated, full),
and the record form's save buttons."""
import os, unittest

from test_app import STATIC, Base, read, tags

FULL = "Room 6035 / Freezer 1 -80 / Freezer_1_rack12_Strain_Collec / box 'Strains collec (box 2)' / G3"


class TestRowsPrefsStorageAndForm(Base):
    def test_whole_rows_are_clickable(self):
        self.create("alice", name="s1")
        c = self.client("alice")
        self.assertIn('<tr data-href="/r/ST-1">', c.get("/t/strain").text)
        self.assertIn('<tr data-href="/r/ST-1">', c.get("/?q=s1").text)
        self.assertIn('class="stretch" href="/r/ST-1"', c.get("/t/strain?view=cards").text)   # cards: the whole card is the link
        js = read("app.js")
        for needle in ('closest("tr[data-href]")', 'closest("a, button, input', "getSelection", "metaKey", "window.open"):
            self.assertIn(needle, js)
        self.assertIn("tr[data-href]{cursor:pointer}", read("style.css"))

    def test_sort_and_view_are_remembered_per_type(self):
        for n in ("beta", "alpha", "gamma"):
            self.create("alice", name=n)
        self.create("alice", "plasmid", name="pX")
        c = self.client("alice")
        r = c.get("/t/strain")
        self.assertNotIn("listprefs", r.headers.get("set-cookie", ""))              # nothing chosen, nothing stored
        r = c.get("/t/strain?sort=name&dir=desc")
        self.assertIn("listprefs=", r.headers["set-cookie"])
        page = c.get("/t/strain").text                                              # no parameters: last choice comes back
        self.assertIn('name="sort" value="name"', page)
        self.assertIn('name="dir" value="desc"', page)
        self.assertLess(page.index("gamma"), page.index("beta"))
        self.assertLess(page.index("beta"), page.index("alpha"))
        other = c.get("/t/plasmid").text                                            # other types are unaffected
        self.assertIn('name="sort" value="id"', other)
        c.get("/t/strain?view=cards")
        self.assertIn("rcards", c.get("/t/strain").text)                            # the view is remembered too
        self.assertIn("rcards", c.get("/t/strain?sort=name&dir=desc&q=").text)      # ...and survives explicit sort links
        self.assertIn("<table", c.get("/t/strain?view=table").text)
        self.assertIn("<table", c.get("/t/strain").text)
        c.get("/t/strain?sort=id&dir=asc")
        self.assertIn('name="sort" value="id"', c.get("/t/strain").text)            # a new choice replaces the old one
        self.assertIn("<table", self.client("alice").get("/t/strain").text)         # another browser starts with the defaults

    def test_remembered_values_are_validated_and_the_cookie_is_safe(self):
        self.create("alice", name="s1")
        c = self.client("alice")
        r = c.get("/t/strain?sort=name&dir=desc&view=cards")
        cookie = r.headers["set-cookie"]
        for flag in ("HttpOnly", "SameSite=lax", "Max-Age=31536000"):
            self.assertIn(flag, cookie)
        self.assertNotRegex(cookie.split(";")[0], r'[ ",]')                          # cookie-safe encoding
        c.get("/t/strain?sort=%3Cscript%3E&dir=sideways&view=3d")                    # invalid values change nothing
        self.assertNotIn("set-cookie", c.get("/t/strain?sort=nope").headers)
        page = c.get("/t/strain").text
        self.assertIn('name="sort" value="name"', page)
        self.assertIn("rcards", page)
        from urllib.parse import quote
        for junk in ("not json", quote('{"strain":{"sort":"<x>","dir":"up","view":"x"},"ghost":{"sort":"id"}}'), quote("[1,2]"), quote('{"strain":5}')):
            d = self.client("alice")
            d.cookies.set("listprefs", junk)
            r = d.get("/t/strain")
            self.assertEqual(r.status_code, 200, junk)
            self.assertIn('name="sort" value="id"', r.text)
            self.assertIn("<table", r.text)

    def test_storage_is_shown_short_with_the_full_path_available(self):
        rid = self.create("alice", name="s1", storage=FULL)
        c = self.client("alice")
        lst = c.get("/t/strain").text
        self.assertIn(">Box 2 Well G3<", lst)
        self.assertIn(f'title="{FULL}"'.replace("'", "&#39;"), lst)                  # full path as tooltip
        self.assertNotIn(f">{FULL.replace(chr(39), '&#39;')}<", lst)
        self.assertIn('class="clamp storage"', lst)                                      # one line on cards; pieces that wrap in tables
        self.assertRegex(read("style.css"), r"\.clamp\.storage\{[^}]*white-space:nowrap")
        self.assertRegex(read("style.css"), r"td \.clamp\.storage\{[^}]*white-space:normal;max-width:")
        self.assertRegex(read("style.css"), r"\.storage \.u\{white-space:nowrap\}")
        self.assertIn(">Box 2 Well G3<", c.get("/t/strain?view=cards").text)
        two = "Room 6035 / Freezer 1 -80 / r / box 'Strains collec (box 2)' / C5; also: Room 6035 / Freezer 2 -80 / r / box 'Freezer2 (box 2)' / A7"
        self.create("alice", name="s2", storage=two)
        self.assertIn('<span class="u">Box 2 Well C5</span> <span class="u">(Freezer 1 -80);</span> '
                      '<span class="u">also Box 2 Well A7</span> <span class="u">(Freezer 2 -80)</span>', c.get("/t/strain").text)
        rec = c.get(f"/r/{rid}").text
        self.assertIn("Box 2 Well G3", rec)
        self.assertIn('class="muted small fullpath"', rec)
        self.assertIn("Freezer_1_rack12_Strain_Collec", rec)                         # full path stays on the record page
        self.assertIn("Freezer_1_rack12_Strain_Collec", c.get(f"/r/{rid}/edit").text)  # the form edits the stored text
        self.assertIn(rid, c.get("/?q=Freezer_1_rack12").text)                       # and it is still searchable
        self.assertEqual(self.store.records[rid]["fields"]["storage"], FULL)         # data untouched
        self.create("alice", name="s3", storage="Box 5 Well A1")                     # short text typed by a person is shown as is
        self.assertIn(">Box 5 Well A1<", c.get("/t/strain").text)

    def test_storage_sorts_naturally(self):
        for n, box in ((1, 10), (2, 2), (3, 1)):
            self.create("alice", name=f"s{n}", storage=f"Room 1 / F / r / box 'Strains collec (box {box})' / A1")
        page = self.client("alice").get("/t/strain?sort=storage&dir=asc").text
        self.assertLess(page.index("Box 1 Well"), page.index("Box 2 Well"))
        self.assertLess(page.index("Box 2 Well"), page.index("Box 10 Well"))

    def test_create_form_has_save_and_save_and_add_another(self):
        page = self.client("alice").get("/t/strain/new").text
        buttons = tags(page, "button", name="action")
        self.assertEqual([(b["value"], b["_text"].strip()) for b in buttons], [("save", "Save"), ("another", "Save and add another")])
        self.assertIn("primary", buttons[0]["class"].split())                           # Save is the main button
        self.create("alice", name="s1")
        edit = self.client("alice").get("/r/ST-1/edit").text
        self.assertIn(">Save</button>", edit)
        self.assertNotIn("add another", edit)                                         # editing has a single Save

    def test_save_and_add_another_flow(self):
        c = self.client("alice")
        r = self.post(c, "/t/strain/new", {"name": "first", "action": "another"})
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/t/strain/new"))
        page = c.get("/t/strain/new").text
        self.assertRegex(page, r'Created <code class="rid">ST-1</code>')              # the new ID is confirmed on the fresh form
        self.assertEqual(self.store.records["ST-1"]["name"], "first")
        r = self.post(c, "/t/strain/new", {"name": "second", "action": "save"})
        self.assertEqual(r.headers["location"], "/r/ST-2")
        r = self.post(c, "/t/strain/new", {"name": "third"})                           # Enter key / no button value: plain save
        self.assertEqual(r.headers["location"], "/r/ST-3")
        bad = self.post(c, "/t/strain/new", {"name": "", "action": "another"})
        self.assertEqual(bad.status_code, 422)                                         # nothing is created; both buttons come back
        self.assertIn("Save and add another", bad.text)
        self.assertEqual(len(self.store.records), 3)


class TestStorageDisplayModes(Base):
    SHORT = "Box 2 Well G3"

    def set_mode(self, mode):
        for f in self.store.schemas["strain"]["fields"]:
            if f["key"] == "storage":
                if mode is None:
                    f.pop("display", None)
                else:
                    f["display"] = mode

    def setUp(self):
        super().setUp()
        self.rid = self.create("alice", name="s1", storage=FULL)
        self.c = self.client("alice")

    def test_an_old_storage_field_without_a_setting_is_short(self):
        self.set_mode(None)
        self.store.reload()                                                   # definitions from before "display": storage was short
        self.assertIn(f">{self.SHORT}<", self.c.get("/t/strain").text)
        self.assertNotIn("[outdated]", self.c.get("/t/strain").text)

    def test_outdated_shows_the_original_text_with_a_marker(self):
        self.set_mode("outdated")
        full = FULL.replace("'", "&#39;")
        for path in ("/t/strain", "/t/strain?view=cards"):
            page = self.c.get(path).text
            self.assertIn(f">{full}</span>", page, path)                      # the stored text, unabbreviated
            self.assertIn('class="outdated"', page, path)
            self.assertIn("[outdated]", page, path)
            self.assertNotIn(f">{self.SHORT}<", page, path)
        rec = self.c.get(f"/r/{self.rid}").text
        self.assertIn(full, rec)
        self.assertIn("[outdated]", rec)
        self.assertNotIn(f"{self.SHORT}", rec)
        self.assertNotIn("fullpath", rec)                                      # the original is already what is shown
        self.assertEqual(self.store.records[self.rid]["fields"]["storage"], FULL)   # data untouched
        self.assertIn(self.rid, self.c.get("/?q=Freezer_1_rack12").text)       # still searchable

    def test_full_shows_the_original_without_a_marker(self):
        self.set_mode("full")
        page = self.c.get("/t/strain").text
        self.assertIn(FULL.replace("'", "&#39;"), page)
        self.assertNotIn("[outdated]", page)

    def test_unknown_display_shows_the_text_as_stored(self):
        for bad in ("outdated ", "Short", "", 3):
            self.set_mode(bad)
            page = self.c.get("/t/strain").text
            self.assertNotIn(f">{self.SHORT}<", page, repr(bad))
            self.assertNotIn("[outdated]", page, repr(bad))

    def test_any_text_field_can_be_a_location(self):
        f = next(f for f in self.store.schemas["strain"]["fields"] if f["key"] == "genotype")
        f["display"] = "short"
        rid = self.create("alice", name="s2", genotype=FULL)
        page = self.c.get(f"/r/{rid}").text
        self.assertIn(self.SHORT, page)
        self.assertIn('class="muted small fullpath"', page)                  # the full text underneath

    def test_marker_only_where_there_is_storage(self):
        self.set_mode("outdated")
        self.create("alice", name="no storage here")
        page = self.c.get("/t/strain").text
        self.assertEqual(page.count("[outdated]"), 1)                          # one record has storage, one does not

    def test_marker_and_css_exist(self):
        css = read("style.css")
        self.assertRegex(css, r"\.outdated\{[^}]*color:var\(--warnfg\)")
        with open(os.path.join(STATIC, "..", "templates", "list.html"), encoding="utf-8") as fh:
            self.assertIn('title="This location comes from the old database', fh.read())


if __name__ == "__main__":
    unittest.main()

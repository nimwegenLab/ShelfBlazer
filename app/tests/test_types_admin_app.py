"""Type administration pages: admin only, create types, edit fields, options, colour, columns, hide, remove, formula preview."""
import json, re, unittest

from test_app import Base


class Admin(Base):
    def setUp(self):
        super().setUp()
        self.a = self.client("root")

    def p(self, path, data=None, c=None):
        return self.post(c or self.a, path, data)

    def flashes(self, r):
        page = self.a.get(r.headers["location"]).text
        return re.findall(r'class="flash[^"]*"[^>]*>(.*?)</', page, re.S)

    def disk(self, slug):
        return json.loads(self.read(f"{self.repo}/config/types/{slug}.yaml"))

    def field(self, slug, key):
        return next(f for f in self.store.schemas[slug]["fields"] if f["key"] == key)


class TestAccess(Admin):
    def test_only_admins_see_or_use_the_pages(self):
        for who in (None, "alice"):
            c = self.client(who)
            for path in ("/admin/types", "/admin/types/strain", "/admin/types/strain/preview?expr=1"):
                self.assertIn(c.get(path).status_code, (303, 403), (who, path))
            for path in ("/admin/types/new", "/admin/types/strain/settings", "/admin/types/strain/fields/add", "/admin/types/strain/delete"):
                r = c.post(path, data={"name": "Zed", "prefix": "ZZ"}) if who is None else self.p(path, {"name": "Zed", "prefix": "ZZ"}, c)
                self.assertIn(r.status_code, (303, 403), (who, path))
        self.assertNotIn("zed", self.store.schemas)

    def test_navigation_link_only_for_admins(self):
        self.assertIn('class="admin" href="/admin/users"', self.a.get("/").text)             # the menu has one Admin entry
        self.assertIn('href="/admin/types"', self.a.get("/admin/users").text)                 # Types is reached from the Admin pages
        self.assertNotIn('class="admin"', self.client("alice").get("/").text)
        self.assertNotIn('href="/admin/types"', self.client("alice").get("/me").text)

    def test_pages_render_and_mark_the_menu_entry(self):
        r = self.a.get("/admin/types")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Strain", r.text)
        self.assertEqual(self.a.get("/admin/types/strain").status_code, 200)
        self.assertEqual(self.a.get("/admin/types/nope").status_code, 404)

    def test_forged_csrf_is_refused(self):
        r = self.a.post("/admin/types/strain/settings", data={"name": "X", "csrf_token": "bad"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.store.schemas["strain"]["name"], "Strain")


class TestCreate(Admin):
    def test_create_a_type_then_use_it(self):
        r = self.p("/admin/types/new", {"name": "Lipid", "prefix": "lp", "color": "teal"})
        self.assertEqual(r.headers["location"], "/admin/types/lipid")
        self.assertEqual(self.disk("lipid")["prefix"], "LP")
        self.assertEqual(self.disk("lipid")["color"], "teal")
        rid = self.create("alice", "lipid", name="DOPC")
        self.assertEqual(rid, "LP-1")
        self.assertIn("Lipid", self.a.get("/").text)

    def test_commit_is_authored_by_the_admin(self):
        self.p("/admin/types/new", {"name": "Lipid", "prefix": "LP"})
        from test_app import git
        self.assertIn(f"{self.uid('root')}@shelfblazer.invalid", git(self.repo, "log", "-1", "--format=%ae"))

    def test_bad_input_is_refused_with_a_message(self):
        for data in ({"name": "Dup", "prefix": "ST"}, {"name": "x", "prefix": "A"}, {"name": "", "prefix": "ZZ"}, {"name": "Strain", "prefix": "ZZ"}):
            r = self.p("/admin/types/new", data)
            self.assertEqual(r.headers["location"], "/admin/types")
            self.assertTrue(self.flashes(r), data)
        self.assertEqual(set(self.store.schemas), {"strain", "plasmid"})

    def test_remove_an_empty_type_only(self):
        self.p("/admin/types/new", {"name": "Lipid", "prefix": "LP"})
        self.create("alice", "lipid", name="a")
        r = self.p("/admin/types/lipid/delete")
        self.assertIn("lipid", self.store.schemas)
        self.assertTrue(self.flashes(r))
        self.p("/admin/types/new", {"name": "Empty", "prefix": "EM"})
        self.p("/admin/types/empty/delete")
        self.assertNotIn("empty", self.store.schemas)
        self.p("/admin/types/plasmid/delete")                      # strain links to it
        self.assertIn("plasmid", self.store.schemas)


class TestFields(Admin):
    def test_add_each_kind_of_field(self):
        base = "/admin/types/plasmid/fields/add"
        self.p(base, {"label": "Size (bp)", "type": "number"})
        self.p(base, {"label": "Kind", "type": "select", "options": "a\nb\n\na"})
        self.p(base, {"label": "Lab", "type": "ref", "to": "strain"})
        self.p(base, {"label": "Used", "type": "computed", "expr": "count(referenced_by('strain'))"})
        self.p(base, {"label": "Checked", "type": "bool", "key": "checked"})
        keys = [f["key"] for f in self.store.schemas["plasmid"]["fields"]]
        self.assertEqual(keys[-5:], ["size_bp", "kind", "lab", "used", "checked"])
        self.assertEqual(self.field("plasmid", "kind")["options"], ["a", "b"])
        self.assertEqual(self.field("plasmid", "lab")["to"], "strain")
        self.assertEqual(self.disk("plasmid")["fields"][-1]["key"], "checked")

    def test_add_field_errors(self):
        base = "/admin/types/plasmid/fields/add"
        for data in ({"label": "", "type": "text"}, {"label": "X", "type": "bogus"}, {"label": "Name", "type": "text"},
                     {"label": "Q", "type": "select", "options": ""}, {"label": "F", "type": "computed", "expr": "nope(1)"},
                     {"label": "Owner", "type": "text"}):
            r = self.p(base, data)
            self.assertTrue(self.flashes(r), data)
        self.assertEqual(len(self.store.schemas["plasmid"]["fields"]), 4)             # unchanged: name, resistance, sequence, Files

    def test_existing_records_show_the_new_field_empty(self):
        rid = self.create("alice", "plasmid", name="p")
        self.p("/admin/types/plasmid/fields/add", {"label": "Size", "type": "number"})
        self.assertEqual(self.a.get(f"/r/{rid}").status_code, 200)
        self.assertIn('name="f_size"', self.client("alice").get(f"/r/{rid}/edit").text)

    def test_rename_and_required(self):
        self.p("/admin/types/strain/fields/genotype/update", {"label": "Genotype / notes", "required_present": "1", "required": "on"})
        self.assertEqual(self.field("strain", "genotype")["label"], "Genotype / notes")
        self.assertTrue(self.field("strain", "genotype").get("required"))
        self.p("/admin/types/strain/fields/genotype/update", {"label": "Genotype", "required_present": "1"})
        self.assertFalse(self.field("strain", "genotype").get("required"))

    def test_options_add_remove_and_protection_of_used_options(self):
        rid = self.create("alice", "plasmid", name="p", resistance="Kan")
        self.p("/admin/types/plasmid/fields/resistance/update", {"label": "Resistance", "add_options": "Cm\nTet"})
        self.assertEqual(self.field("plasmid", "resistance")["options"], ["Kan", "Amp", "Cm", "Tet"])
        self.p("/admin/types/plasmid/fields/resistance/update", {"label": "Resistance", "remove_options": ["Amp", "Cm"]})
        self.assertEqual(self.field("plasmid", "resistance")["options"], ["Kan", "Tet"])
        r = self.p("/admin/types/plasmid/fields/resistance/update", {"label": "Resistance", "remove_options": ["Kan"]})
        self.assertIn("Kan", self.field("plasmid", "resistance")["options"])
        self.assertTrue(self.flashes(r))
        self.assertIn("(in use)", self.a.get("/admin/types/plasmid").text)

    def test_text_to_longtext_only(self):
        self.p("/admin/types/strain/fields/genotype/update", {"label": "Genotype", "type": "longtext"})
        self.assertEqual(self.field("strain", "genotype")["type"], "longtext")
        r = self.p("/admin/types/strain/fields/genotype/update", {"label": "Genotype", "type": "number"})
        self.assertEqual(self.field("strain", "genotype")["type"], "longtext")
        self.assertTrue(self.flashes(r))

    def test_hide_and_show_keep_the_data(self):
        rid = self.create("alice", name="s", genotype="wt")
        self.p("/admin/types/strain/fields/genotype/hidden")
        self.assertTrue(self.field("strain", "genotype")["hidden"])
        self.assertEqual(self.store.records[rid]["fields"]["genotype"], "wt")
        self.assertNotIn("f_genotype", self.client("alice").get("/t/strain/new").text)
        self.assertNotIn("wt<", self.client("alice").get(f"/r/{rid}").text.replace("Genotype", ""))
        self.p("/admin/types/strain/fields/genotype/hidden")
        self.assertFalse(self.field("strain", "genotype").get("hidden"))
        self.assertIn("wt", self.client("alice").get(f"/r/{rid}").text)

    def test_the_title_and_system_fields_are_protected(self):
        self.p("/admin/types/strain/fields/name/hidden")
        self.assertFalse(self.field("strain", "name").get("hidden"))
        self.p("/admin/types/strain/fields/legacy_owner/hidden")
        self.assertFalse(self.field("strain", "legacy_owner").get("hidden"))
        self.p("/admin/types/strain/fields/legacy_owner/update", {"label": "Changed"})
        self.assertEqual(self.field("strain", "legacy_owner")["label"], "Legacy owner")
        self.p("/admin/types/strain/fields/name/move", {"direction": "down"})
        self.assertEqual(self.store.schemas["strain"]["fields"][0]["key"], "name")

    def test_move(self):
        keys = lambda: [f["key"] for f in self.store.schemas["strain"]["fields"]]
        self.p("/admin/types/strain/fields/storage/move", {"direction": "up"})
        self.assertEqual(keys()[:3], ["name", "storage", "genotype"])
        self.p("/admin/types/strain/fields/storage/move", {"direction": "down"})
        self.assertEqual(keys()[:3], ["name", "genotype", "storage"])

    def test_every_change_is_one_commit(self):
        from test_app import git
        n = lambda: int(git(self.repo, "rev-list", "--count", "HEAD"))
        before = n()
        self.p("/admin/types/strain/fields/add", {"label": "Size", "type": "number"})
        self.assertEqual(n(), before + 1)
        self.assertIn("type strain", git(self.repo, "log", "-1", "--format=%s"))


class TestSettings(Admin):
    def test_name_colour_and_columns(self):
        self.p("/admin/types/strain/settings", {"name": "Bacterial strain", "color": "red", "columns": ["genotype", "created_at"]})
        s = self.store.schemas["strain"]
        self.assertEqual((s["name"], s["color"]), ("Bacterial strain", "red"))
        self.assertEqual(s["list_columns"], ["id", "name", "genotype", "created_at"])
        self.assertEqual(self.client("alice").get("/t/strain").status_code, 200)
        self.p("/admin/types/strain/settings", {"name": "Bacterial strain", "color": "", "columns": ["storage"]})
        self.assertNotIn("color", self.store.schemas["strain"])

    def test_bad_colour_or_empty_name_is_refused(self):
        r = self.p("/admin/types/strain/settings", {"name": "S", "color": "plaid", "columns": ["storage"]})
        self.assertTrue(self.flashes(r))
        self.assertNotEqual(self.store.schemas["strain"].get("color"), "plaid")
        r = self.p("/admin/types/strain/settings", {"name": " ", "color": "", "columns": ["storage"]})
        self.assertEqual(self.store.schemas["strain"]["name"], "Strain")

    def test_record_picture_is_chosen_among_the_file_fields(self):
        url = "/admin/types/strain/settings"
        base = {"name": "Strain", "color": "", "columns": ["storage"]}
        self.assertNotIn('name="thumbnail"', self.a.get("/admin/types/strain").text)            # no File field, no choice
        self.p("/admin/types/strain/fields/add", {"label": "Photo", "type": "file"})
        self.p("/admin/types/strain/fields/add", {"label": "Gel", "type": "file"})
        page = self.a.get("/admin/types/strain").text
        self.assertIn('name="thumbnail"', page)
        self.p(url, dict(base, thumbnail="photo"))
        self.assertEqual(self.store.schemas["strain"]["thumbnail"], "photo")
        self.p(url, base)                                                                         # not sent: left as it is
        self.assertEqual(self.store.schemas["strain"]["thumbnail"], "photo")
        self.assertTrue(self.flashes(self.p(url, dict(base, thumbnail="name"))))                  # not a File field
        self.assertEqual(self.store.schemas["strain"]["thumbnail"], "photo")
        self.p("/admin/types/strain/fields/photo/hidden", {})                                     # hiding it drops the setting
        self.assertNotIn("thumbnail", self.store.schemas["strain"])
        self.p("/admin/types/strain/fields/photo/hidden", {})
        self.p(url, dict(base, thumbnail="gel"))
        self.p(url, dict(base, thumbnail=""))
        self.assertNotIn("thumbnail", self.store.schemas["strain"])

    def test_a_file_field_can_be_limited_to_pictures(self):
        self.p("/admin/types/strain/fields/add", {"label": "Gel", "type": "file", "pictures_only": "on"})
        self.p("/admin/types/strain/fields/add", {"label": "Papers", "type": "file"})
        fields = {f["key"]: f for f in self.store.schemas["strain"]["fields"]}
        self.assertTrue(fields["gel"].get("pictures_only"))
        self.assertNotIn("pictures_only", fields["papers"])
        rid = self.store.create("strain", self.users.get("alice"), "S1", {})
        alice = self.users.get("alice")
        with self.assertRaises(Exception) as cm:
            self.store.add_attachment(rid, alice, "notes.pdf", b"%PDF", field="gel")
        self.assertIn("pictures only", str(cm.exception))
        self.store.add_attachment(rid, alice, "gel1.PNG", b"\x89PNG", field="gel")
        self.store.add_attachment(rid, alice, "notes.pdf", b"%PDF", field="papers")             # other fields are not limited
        r = self.client("alice").get(f"/r/{rid}/edit")
        self.assertIn('accept="image/*,.heic,.heif"', r.text)
        self.p("/admin/types/strain/fields/gel/update", {"label": "Gel", "pictures_present": "1"})   # box cleared
        self.assertNotIn("pictures_only", {f["key"]: f for f in self.store.schemas["strain"]["fields"]}["gel"])
        self.p("/admin/types/strain/fields/gel/update", {"label": "Gel"})                          # box not on the form: unchanged
        self.p("/admin/types/strain/fields/gel/update", {"label": "Gel", "pictures_present": "1", "pictures_only": "on"})
        self.assertTrue({f["key"]: f for f in self.store.schemas["strain"]["fields"]}["gel"].get("pictures_only"))
        self.p("/admin/types/strain/fields/gel/update", {"label": "Gel"})
        self.assertTrue({f["key"]: f for f in self.store.schemas["strain"]["fields"]}["gel"].get("pictures_only"))

    def test_hiding_a_field_removes_it_from_the_list_columns(self):
        self.assertIn("storage", self.store.schemas["strain"]["list_columns"])
        self.p("/admin/types/strain/fields/storage/hidden")
        self.assertNotIn("storage", self.store.schemas["strain"]["list_columns"])
        self.assertEqual(self.client("alice").get("/t/strain").status_code, 200)


class TestOrder(Admin):
    def menu(self, c):
        import re
        nav = re.search(r"<nav.*?</nav>", c.get("/").text, re.S).group(0)
        return re.findall(r'href="/t/([a-z-]+)"', nav)

    def home(self, c):
        import re
        return re.findall(r'<a class="l" href="/t/([a-z-]+)"', c.get("/").text)

    def test_types_not_in_the_saved_order_follow_alphabetically(self):
        self.p("/admin/types/new", {"name": "Zinc", "prefix": "ZN"})
        self.p("/admin/types/new", {"name": "Acid", "prefix": "AC"})
        self.assertEqual(self.menu(self.a), ["strain", "plasmid", "acid", "zinc"])      # the saved order, then the rest
        self.assertEqual(self.store.type_order, ["strain", "plasmid"])
        self.store.type_order = []                                                     # no saved order: alphabetical, no type favoured
        self.assertEqual(self.menu(self.a), ["acid", "plasmid", "strain", "zinc"])

    def test_move_up_and_down_changes_menu_home_and_admin_page_for_everybody(self):
        self.p("/admin/types/plasmid/order", {"direction": "up"})
        for c in (self.a, self.client("alice")):
            self.assertEqual(self.menu(c), ["plasmid", "strain"])
            self.assertEqual(self.home(c), ["plasmid", "strain"])
        page = self.a.get("/admin/types").text
        self.assertLess(page.index('/admin/types/plasmid"'), page.index('/admin/types/strain"'))
        self.p("/admin/types/plasmid/order", {"direction": "down"})
        self.assertEqual(self.menu(self.a), ["strain", "plasmid"])
        self.assertEqual(json.loads(self.read(f"{self.repo}/config/type_order.yaml")), ["strain", "plasmid"])

    def test_the_first_cannot_move_up_nor_the_last_down(self):
        self.p("/admin/types/strain/order", {"direction": "up"})
        self.p("/admin/types/plasmid/order", {"direction": "down"})
        self.assertEqual(self.menu(self.a), ["strain", "plasmid"])
        page = self.a.get("/admin/types").text
        self.assertIn('title="Move up" disabled', page)
        self.assertIn('title="Move down" disabled', page)

    def test_a_new_type_goes_to_the_end_and_a_removed_one_leaves_no_hole(self):
        self.p("/admin/types/plasmid/order", {"direction": "up"})
        self.p("/admin/types/new", {"name": "Lipid", "prefix": "LP"})
        self.assertEqual(self.menu(self.a), ["plasmid", "strain", "lipid"])
        self.p("/admin/types/lipid/delete")
        self.assertEqual(self.menu(self.a), ["plasmid", "strain"])

    def test_only_admins_and_valid_input(self):
        for who in (None, "alice"):
            c = self.client(who)
            r = c.post("/admin/types/plasmid/order", data={"direction": "up"}) if who is None else self.p("/admin/types/plasmid/order", {"direction": "up"}, c)
            self.assertIn(r.status_code, (303, 403))
        self.assertEqual(self.store.type_order, ["strain", "plasmid"])
        r = self.p("/admin/types/plasmid/order", {"direction": "sideways"})
        self.assertTrue(self.flashes(r))
        self.assertEqual(self.p("/admin/types/nope/order", {"direction": "up"}).status_code, 404)

    def test_one_commit_per_move_authored_by_the_admin(self):
        from test_app import git
        before = int(git(self.repo, "rev-list", "--count", "HEAD"))
        self.p("/admin/types/plasmid/order", {"direction": "up"})
        self.assertEqual(int(git(self.repo, "rev-list", "--count", "HEAD")), before + 1)
        self.assertIn("types: move plasmid up", git(self.repo, "log", "-1", "--format=%s"))
        self.assertIn(f"{self.uid('root')}@shelfblazer.invalid", git(self.repo, "log", "-1", "--format=%ae"))

    def test_hidden_types_stay_hidden_in_the_ordered_menu_and_colours_do_not_move(self):
        self.p("/admin/types/plasmid/order", {"direction": "up"})
        self.store.save_schema("plasmid", {**self.store.schemas["plasmid"], "permissions": {"read": "admin"}}, self.users.get("root"))
        self.assertEqual(self.menu(self.client("alice")), ["strain"])
        import re
        self.assertIn('href="/t/strain" data-hue="1"', self.a.get("/").text)               # the colour belongs to the type, not to its place


class TestPreview(Admin):
    def test_try_a_formula_on_the_newest_or_a_chosen_record(self):
        pl = self.create("alice", "plasmid", name="p")
        self.create("alice", name="s", plasmid=pl)
        r = self.a.get("/admin/types/plasmid/preview", params={"expr": "count(referenced_by('strain'))"}).json()
        self.assertEqual((r["error"], r["value"], r["record"]), (None, "1", pl))
        r = self.a.get("/admin/types/plasmid/preview", params={"expr": "upper(name)", "id": pl}).json()
        self.assertEqual(r["value"], "P")
        r = self.a.get("/admin/types/plasmid/preview", params={"expr": "import_os(1)"}).json()
        self.assertTrue(r["error"])

    def test_no_record_yet(self):
        r = self.a.get("/admin/types/plasmid/preview", params={"expr": "1+1"}).json()
        self.assertTrue(r["error"])


class TestPages(Admin):
    def test_the_page_script_is_served_and_has_no_inline_script(self):
        page = self.a.get("/admin/types/strain").text
        self.assertIn('src="/static/types.js"', page)
        self.assertNotRegex(page, r"<script(?![^>]*src=)")
        self.assertEqual(self.a.get("/static/types.js").status_code, 200)

    def test_a_computed_field_shows_its_help_and_examples(self):
        self.assertIn("count(referenced_by('strain'))", self.a.get("/admin/types/plasmid").text)


if __name__ == "__main__":
    unittest.main()

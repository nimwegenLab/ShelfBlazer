"""Choice fields with "addable": a new option typed in the record form joins the list; text fields with "validate": "dna" reject non-sequences."""
import json, unittest

from test_app import Base

PLASMID = {"name": "Vector", "prefix": "VC", "list_columns": ["id", "name", "resistance"], "fields": [
    {"key": "name", "label": "Name", "type": "text", "required": True},
    {"key": "resistance", "label": "Resistance", "type": "multiselect", "options": ["Kanamycin", "Ampicillin"], "addable": True},
    {"key": "kind", "label": "Kind", "type": "select", "options": ["a"], "addable": True},
    {"key": "fixed", "label": "Fixed", "type": "multiselect", "options": ["x"]},
    {"key": "sequence", "label": "Sequence", "type": "longtext", "validate": "dna"},
    {"key": "references", "label": "Reference (DOI)", "type": "text", "validate": "doi"}]}


class Addable(Base):
    def setUp(self):
        super().setUp()
        self.store.save_schema("vector", json.loads(json.dumps(PLASMID)), self.users.get("root"), create=True)
        self.c = self.client("alice")

    def new(self, name, **extra):
        return self.post(self.c, "/t/vector/new", {"name": name, **extra})

    def options(self):
        return [f["options"] for f in self.store.schemas["vector"]["fields"] if f["key"] == "resistance"][0]

    def test_form_offers_a_new_option_box_only_on_addable_fields(self):
        page = self.c.get("/t/vector/new").text
        self.assertIn('name="f_resistance_new"', page)
        self.assertIn('name="f_kind_new"', page)
        self.assertIn("<span class=\"checks\" data-addable>", page)       # picker.js turns these into chips with a dropdown
        self.assertIn("New option…", page)
        self.assertNotIn('name="f_fixed_new"', page)

    def test_a_typed_option_is_added_and_ticked(self):
        r = self.new("p", f_resistance="Kanamycin", f_resistance_new="  Chloram   phenicol ")
        self.assertIn(r.status_code, (302, 303), r.text[:300])
        self.assertEqual(self.options(), ["Kanamycin", "Ampicillin", "Chloram phenicol"])
        rec = next(r for r in self.store.records.values() if r["name"] == "p")
        self.assertEqual(rec["fields"]["resistance"], ["Kanamycin", "Chloram phenicol"])
        self.assertIn("add option 'Chloram phenicol'", self.store.git("log", "-1", "--format=%b"))   # saved with the record, one commit

    def test_same_name_in_another_case_reuses_the_option(self):
        self.new("p", f_resistance_new="KANAMYCIN")
        self.assertEqual(self.options(), ["Kanamycin", "Ampicillin"])
        self.assertEqual(next(iter(self.store.records.values()))["fields"]["resistance"], ["Kanamycin"])

    def test_single_choice_new_option(self):
        self.new("p", f_kind="__new__", f_kind_new="b")
        self.assertEqual(next(iter(self.store.records.values()))["fields"]["kind"], "b")

    def test_a_field_that_is_not_addable_ignores_the_box(self):
        self.new("p", f_fixed="x", f_fixed_new="zzz")
        self.assertEqual([f for f in self.store.schemas["vector"]["fields"] if f["key"] == "fixed"][0]["options"], ["x"])

    def test_too_long_option_is_not_added(self):
        r = self.new("p", f_resistance_new="x" * 61)
        self.assertEqual(r.status_code, 422)
        self.assertIn("at most 60 characters", r.text)
        self.assertEqual(self.options(), ["Kanamycin", "Ampicillin"])

    def test_a_save_that_fails_adds_no_option(self):
        n = self.store.git("rev-list", "--count", "HEAD")
        r = self.new("", f_resistance_new="Zeocin", f_kind="__new__", f_kind_new="c")       # no title
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.options(), ["Kanamycin", "Ampicillin"])
        self.assertEqual(self.store.git("rev-list", "--count", "HEAD"), n)
        self.assertIn('name="f_resistance_new" value="Zeocin"', r.text)


class DnaValidation(Base):
    def setUp(self):
        super().setUp()
        self.store.save_schema("vector", json.loads(json.dumps(PLASMID)), self.users.get("root"), create=True)
        self.c = self.client("alice")

    def save(self, seq):
        return self.post(self.c, "/t/vector/new", {"name": "p", "f_sequence": seq})

    def test_accepts_dna_whitespace_iupac_and_fasta_header(self):
        for ok in ("ACGT", "acgtn rykm\r\nswbd hvu", ">pOSIP (CamR, 6223 bps)\r\ngaattc\r\nggatcc", ""):
            self.assertIn(self.save(ok).status_code, (302, 303), ok)
        self.assertEqual(sum(r["type"] == "vector" for r in self.store.records.values()), 4)

    def test_rejects_other_text_with_a_message(self):
        for bad in ("https://benchling.com/s/seq-abc", "ACGT 123", "ACGT\n>head\nACGX"):
            r = self.save(bad)
            self.assertEqual(r.status_code, 422, bad)
            self.assertIn("not a DNA sequence", r.text)
        self.assertFalse(any(r["type"] == "vector" for r in self.store.records.values()))      # nothing was saved


class DoiValidation(Base):
    def setUp(self):
        super().setUp()
        self.store.save_schema("vector", json.loads(json.dumps(PLASMID)), self.users.get("root"), create=True)
        self.c = self.client("alice")

    def save(self, ref):
        return self.post(self.c, "/t/vector/new", {"name": "p", "f_references": ref})

    def stored(self):
        return next(r for r in self.store.records.values() if r["type"] == "vector")["fields"].get("references")

    def test_bare_prefixed_and_linked_dois_are_stored_bare(self):
        for typed, want in (("10.1038/nmeth895", "10.1038/nmeth895"), (" doi: 10.1038/nmeth895. ", "10.1038/nmeth895"),
                            ("https://doi.org/10.7554/eLife.05856", "10.7554/eLife.05856"),
                            ("10.1021/sb400021j,  https://dx.doi.org/10.1038/nmeth895 10.1021/SB400021J", "10.1021/sb400021j, 10.1038/nmeth895")):
            self.assertIn(self.save(typed).status_code, (302, 303), typed)
        self.assertEqual(self.stored(), "10.1038/nmeth895")       # first record found; the normal forms are checked below
        from shelfblazer.store import normalize_dois
        self.assertEqual(normalize_dois("10.1021/sb400021j,  https://dx.doi.org/10.1038/nmeth895 10.1021/SB400021J"), "10.1021/sb400021j, 10.1038/nmeth895")
        self.assertEqual(normalize_dois(" DOI:10.7554/eLife.05856. "), "10.7554/eLife.05856")

    def test_anything_else_is_refused(self):
        for bad in ("Zaslaver 2006 Nature Methods", "17.6 ng/ul", "http://pubs.acs.org/doi/abs/10.1021/sb400021j", "10.1038"):
            r = self.save(bad)
            self.assertEqual(r.status_code, 422, bad)
            self.assertIn("not a DOI", r.text)

    def test_record_page_links_the_doi(self):
        self.save("10.1038/nmeth895")
        rid = next(r["id"] for r in self.store.records.values() if r["type"] == "vector")
        page = self.c.get(f"/r/{rid}").text
        self.assertIn('href="https://doi.org/10.1038/nmeth895"', page)
        self.assertNotIn("<h2>References</h2>", page)


class TestRefusedSavesAddNothing(Base):
    """A save the person is not allowed to make must not add options or places either (they used to be added while the form was read)."""
    def setUp(self):
        super().setUp()
        schema = json.loads(json.dumps(PLASMID))
        schema["fields"].append({"key": "storage_place", "label": "Storage place", "type": "select", "source": "storage_places"})
        self.store.save_schema("vector", schema, self.users.get("root"), create=True)

    def state(self):
        return (self.store.git("rev-list", "--count", "HEAD").strip(), json.dumps(self.store.schemas["vector"]), json.dumps(self.store.places))

    def extra(self):
        return {"f_resistance_new": "Sneaky", "f_kind": "__new__", "f_kind_new": "sneaky", "f_storage_place": "__new__", "f_storage_place_new": "Sneaky shelf"}

    def test_editing_someone_elses_record(self):
        rid = self.create("alice", "vector", name="mine")
        before = self.state()
        r = self.post(self.client("bob"), f"/r/{rid}/edit", {"name": "x", "version": self.version(rid), **self.extra()})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.state(), before)

    def test_creating_where_only_admins_may(self):
        schema = dict(self.store.schemas["vector"], permissions={"create": "admin"})
        self.store.save_schema("vector", schema, self.users.get("root"))
        before = self.state()
        r = self.post(self.client("alice"), "/t/vector/new", {"name": "x", **self.extra()})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.state(), before)

    def test_the_owner_still_adds_them(self):
        rid = self.create("alice", "vector", name="mine")
        r = self.post(self.client("alice"), f"/r/{rid}/edit", {"name": "mine", "version": self.version(rid), **self.extra()})
        self.assertEqual(r.status_code, 303, r.text[:300])
        self.assertIn("Sneaky shelf", [p["name"] for p in self.store.places])
        self.assertEqual(self.store.records[rid]["fields"]["kind"], "sneaky")


class TestPlaceLimit(Base):
    def test_places_stop_at_the_limit(self):
        from shelfblazer import store as st
        from shelfblazer.store import ValidationError
        alice = self.users.get("alice")
        old, st.MAX_PLACES = st.MAX_PLACES, 2
        try:
            self.store.add_place("one", alice)
            self.store.add_place("two", alice)
            self.assertEqual(self.store.add_place("ONE", alice), "one")          # an existing name is still found
            with self.assertRaises(ValidationError):
                self.store.add_place("three", alice)
        finally:
            st.MAX_PLACES = old


class LegacyField(Base):
    """"legacy": true (the free-text storage): shown on the record when it has a value, offered in a form only where it already has one."""
    def setUp(self):
        super().setUp()
        sch = {"name": "Mold", "prefix": "ML", "list_columns": ["id", "name"], "fields": [
            {"key": "name", "label": "Name", "type": "text", "required": True},
            {"key": "old_label", "label": "Old label", "type": "text", "legacy": True}]}
        self.store.save_schema("mold", sch, self.users.get("root"), create=True)
        self.c = self.client("alice")

    def make(self, name, **fields):
        self.post(self.c, "/t/mold/new", {"name": name, **fields})
        return next(x["id"] for x in self.store.records.values() if x["name"] == name)

    def test_not_on_the_new_entry_form_and_not_stored_from_it(self):
        self.assertNotIn('name="f_old_label"', self.c.get("/t/mold/new").text)
        rid = self.make("fresh")                                           # saved from the form without it
        self.assertNotIn("old_label", self.store.records[rid]["fields"])

    def test_shown_on_the_record_and_editable_when_it_has_a_value(self):
        rid = self.make("old")
        self.store.update(rid, self.users.get("alice"), "old", {"old_label": "87"}, "", self.version(rid))
        self.assertIn("Old label", self.c.get(f"/r/{rid}").text)
        self.assertIn('name="f_old_label"', self.c.get(f"/r/{rid}/edit").text)
        self.post(self.c, f"/r/{rid}/edit", {"name": "renamed", "version": self.version(rid)})      # field not in the form: kept
        self.assertEqual(self.store.records[rid]["fields"]["old_label"], "87")

    def test_not_offered_when_empty(self):
        rid = self.make("plain")
        self.assertNotIn('name="f_old_label"', self.c.get(f"/r/{rid}/edit").text)
        self.assertNotIn("Old label", self.c.get(f"/r/{rid}").text)

    def test_only_true_is_accepted_in_the_definition(self):
        sch = {"name": "Mold", "prefix": "ML", "list_columns": ["id", "name"], "fields": [
            {"key": "name", "label": "Name", "type": "text", "required": True},
            {"key": "old_label", "label": "Old label", "type": "text", "legacy": "yes"}]}
        with self.assertRaises(Exception):
            self.store.save_schema("mold2", sch, self.users.get("root"), create=True)


class HelpText(Base):
    """"help" on a field (or on the title field) is shown next to its label on the record form."""
    def setUp(self):
        super().setUp()
        self.root = self.users.get("root")
        self.c = self.client("alice")

    def schema(self, **extra):
        return {"name": "Mold", "prefix": "ML", "list_columns": ["id", "name"], "fields": [
            {"key": "name", "label": "Name", "type": "text", "required": True, "help": "Gene, then F or R"},
            {"key": "conc", "label": "Conc", "type": "text", **extra}]}

    def test_shown_on_the_form(self):
        self.store.save_schema("mold", self.schema(help="Free text, e.g. 100uM"), self.root, create=True)
        page = self.c.get("/t/mold/new").text
        self.assertIn("Free text, e.g. 100uM", page)
        self.assertIn("Gene, then F or R", page)

    def test_must_be_short_text(self):
        for bad in (5, "x" * 201):
            with self.assertRaises(Exception):
                self.store.save_schema("mold2", self.schema(help=bad), self.root, create=True)


if __name__ == "__main__":
    unittest.main()

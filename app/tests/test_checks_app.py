"""The "Check before saving" panel on the record form: shown once, the person decides, nothing is blocked."""
import html, re, unittest
from unittest import mock

from shelfblazer import checks
from test_app import Base, STRAIN


class Panel(Base):
    def setUp(self):
        super().setUp()
        self.alice = self.client("alice")
        self.first = self.create("alice", name="MG1655 test", genotype="g")

    def new(self, c=None, name="MG1655 test", **extra):
        return self.post(c or self.alice, "/t/strain/new", {"name": name, **extra})

    @staticmethod
    def confirm_of(page):
        return html.unescape(re.search(r'name="confirm" value="([0-9a-f]+)"', page).group(1))

    def count(self):
        return len(self.store.records)

    # ---- new records
    def test_a_possible_duplicate_is_shown_and_nothing_is_saved(self):
        n = self.count()
        r = self.new(name="mg1655  TEST")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Check before saving", r.text)
        self.assertIn(f'href="/r/{self.first}"', r.text)
        self.assertIn("MG1655 test", r.text)                                    # the existing record, by name
        self.assertIn("has the same name", r.text)
        self.assertIn('value="mg1655  TEST"', r.text)                           # what was typed is still in the form
        self.assertEqual(self.count(), n)

    def test_save_anyway_saves_it(self):
        page = self.new().text
        r = self.new(confirm=self.confirm_of(page))
        self.assertEqual(r.status_code, 303)
        self.assertEqual(sum(1 for rec in self.store.records.values() if rec["name"] == "MG1655 test"), 2)

    def test_the_panel_asks_again_when_the_findings_change(self):
        self.create("alice", name="Other")
        code = self.confirm_of(self.new().text)
        r = self.new(name="Other", confirm=code)                                # confirmed for another record
        self.assertEqual(r.status_code, 200)
        self.assertIn("Check before saving", r.text)
        self.assertNotEqual(self.confirm_of(r.text), code)

    def test_a_name_without_findings_saves_at_once(self):
        self.assertEqual(self.new(name="something else").status_code, 303)

    def test_a_form_with_errors_shows_the_errors_not_the_panel(self):
        r = self.new(f_status="nope")
        self.assertEqual(r.status_code, 422)
        self.assertNotIn("Check before saving", r.text)

    def test_the_chosen_button_is_kept(self):
        r = self.new(action="another")
        self.assertRegex(r.text, r'<button class="primary" name="action" value="another">Save anyway</button>')
        r = self.new(confirm=self.confirm_of(r.text), action="another")
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/t/strain/new"))

    def test_the_panel_is_only_in_the_page_that_needs_it(self):
        self.assertNotIn("checks.js", self.alice.get("/t/strain/new").text)
        self.assertIn("checks.js", self.new().text)

    # ---- who sees what
    def test_a_record_the_person_may_not_see_is_not_mentioned(self):
        bob = self.client("bob")
        hidden = self.create("bob", name="Bobs secret")
        self.store.set_archived(hidden, self.users.get("bob"), True)
        r = self.new(name="Bobs secret")
        self.assertEqual(r.status_code, 303)                                    # alice sees no archived record of bob's
        self.assertEqual(self.new(c=bob, name="Bobs secret").status_code, 200)  # bob does

    def test_an_archived_duplicate_is_marked_and_explained(self):
        self.store.set_archived(self.first, self.users.get("alice"), True)
        r = self.new()
        self.assertIn("archived", r.text)
        self.assertIn("restore the old record", r.text)

    def test_a_personal_item_is_compared_with_the_lab_collection(self):
        root = self.users.get("root")
        self.store.save_schema("strain", dict(STRAIN, permissions={"personal": "all"}), root)
        r = self.new(collection="mine")
        self.assertEqual(r.status_code, 200)
        self.assertIn(f'href="/r/{self.first}"', r.text)

    # ---- editing
    def edit(self, rid, c=None, **data):
        rec = self.store.records[rid]
        return self.post(c or self.alice, f"/r/{rid}/edit", {"name": rec["name"], "version": rec["_version"], **data})

    def test_an_edit_that_makes_a_duplicate_is_shown(self):
        other = self.create("alice", name="Another")
        r = self.edit(other, name="MG1655 test")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Check before saving", r.text)
        self.assertEqual(self.store.records[other]["name"], "Another")
        r = self.edit(other, name="MG1655 test", confirm=self.confirm_of(r.text), version=self.store.records[other]["_version"])
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.store.records[other]["name"], "MG1655 test")

    def test_an_edit_does_not_repeat_what_the_record_already_had(self):
        twin = self.store.create("strain", self.users.get("alice"), "MG1655 test", {})          # the form would have asked
        self.assertEqual(self.store.records[twin]["name"], self.store.records[self.first]["name"])    # already a duplicate
        r = self.edit(twin, f_genotype="new genotype")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.store.records[twin]["fields"]["genotype"], "new genotype")

    def test_a_record_is_not_its_own_duplicate_on_edit(self):
        self.assertEqual(self.edit(self.first, f_genotype="changed").status_code, 303)

    # ---- fixes (the rules that make them come with A3: here a stand-in)
    def test_a_fix_gets_a_button_that_names_its_field(self):
        fix = checks.Finding("fix", "write it as DH5alpha", field="genotype", suggestion="DH5alpha")
        with mock.patch.object(checks, "review", return_value=[fix]):
            r = self.new(name="something else")
        self.assertEqual(r.status_code, 200)
        self.assertIn("<strong>Genotype:</strong> write it as DH5alpha", r.text)
        self.assertRegex(r.text, r'<button type="button" class="apply" data-field="genotype" data-value="DH5alpha">')

    def test_a_warning_is_shown_without_a_button(self):
        warn = checks.Finding("warning", "the plasmid is named but not linked")
        with mock.patch.object(checks, "review", return_value=[warn]):
            r = self.new(name="something else")
        self.assertIn("the plasmid is named but not linked", r.text)
        self.assertNotIn('class="apply"', r.text)

    def test_real_rules_give_a_fix_button_and_a_warning(self):
        import json, os
        os.makedirs(os.path.join(self.repo, "config", "checks"), exist_ok=True)
        with open(os.path.join(self.repo, "config", "checks", "strain.yaml"), "w") as fh:
            json.dump({"tables": {"spelling": [["(?i)\\bdh5alfa\\b", "DH5alpha"]]},
                       "fields": {"name": {"fix": ["spelling"]}, "genotype": {"max_length": 5}}}, fh)
        self.store.reload()
        r = self.new(name="Dh5alfa", f_genotype="much too long")
        self.assertEqual(r.status_code, 200)
        self.assertRegex(r.text, r'<button type="button" class="apply" data-field="name" data-value="DH5alpha">Use “DH5alpha”</button>')
        self.assertIn("Genotype is long", r.text)
        r = self.new(name="DH5alpha", f_genotype="ok")                    # corrected: saved at once
        self.assertEqual(r.status_code, 303)

    def test_the_apply_script_and_style_exist(self):
        r = self.alice.get("/static/checks.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("data-field", r.text.replace("dataset.field", "data-field"))
        self.assertIn(".checkpanel", self.alice.get("/static/style.css").text)


if __name__ == "__main__":
    unittest.main()

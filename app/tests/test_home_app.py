"""Home page (title, search, recent activity), greyed-out archived records, storage on edit forms, folded fields."""
import re, unittest

from test_app import Base


class TestHome(Base):
    def test_title_search_and_section_headings(self):
        for n in ("a", "b"):
            self.create("alice", name=n)
        self.create("alice", "plasmid", name="p")
        page = self.client("alice").get("/").text
        self.assertRegex(page, r'<h1>Home <span class="muted count">2 types \u00b7 3 records</span></h1>')
        self.assertIn('placeholder="Search all records, or type an ID such as ST-2"', page)
        self.assertIn("<h2>Record types</h2>", page)
        self.assertNotIn("Stocks", page)

    def test_counts_skip_archived_and_hidden_types(self):
        rid = self.create("alice", name="a")
        self.create("alice", "plasmid", name="p")
        self.post(self.client("alice"), f"/r/{rid}/archive")
        self.assertIn("2 types \u00b7 1 record<", self.client("alice").get("/").text)
        self.store.save_schema("plasmid", {**self.store.schemas["plasmid"], "permissions": {"read": "admin"}}, self.users.get("root"))
        self.assertIn("1 type \u00b7 0 records<", self.client("alice").get("/").text)

    def test_thousands_separator(self):
        for i in range(3):
            self.store.records[f"ST-{i + 900}"] = dict(id=f"ST-{i + 900}", type="strain", name="x", created_by="alice", created_at="", updated_at="", archived=False,
                                                         legacy_id=i + 900, fields={}, notes="", _version="v")
        for i in range(1000):
            self.store.records[f"PL-{i + 1}"] = dict(id=f"PL-{i + 1}", type="plasmid", name="x", created_by="alice", created_at="", updated_at="", archived=False,
                                                       legacy_id=i + 1, fields={}, notes="", _version="v")
        self.assertIn("1,003 records", self.client("alice").get("/").text)


class TestActivity(Base):
    def test_recent_activity_shows_verb_id_and_how_long_ago_newest_first(self):
        rid = self.create("alice", name="first strain")
        self.post(self.client("bob"), "/t/plasmid/new", {"name": "bobs plasmid"})
        c = self.client("alice")
        self.post(c, f"/r/{rid}/edit", {"name": "renamed strain", "version": self.version(rid)})
        self.post(c, f"/r/{rid}/archive")
        page = c.get("/").text
        self.assertIn("<h2>Recent activity</h2>", page)
        items = re.findall(r'<li data-hue="\d"[^>]*>(.*?)</li>', page, re.S)
        text = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", i)).strip() for i in items]
        self.assertEqual(text, ["archived ST-1 just now", "edited ST-1 just now", "created PL-1 just now", "created ST-1 just now"])
        self.assertIn('<a class="rid" href="/r/ST-1">ST-1</a>', page)
        self.assertIn('title="Alice A \u00b7 ', page)                               # who and when on hover
        self.assertNotIn('class="avatar"', page.split("Recent activity")[1])           # the design shows no names in the list

    def test_time_ago_wording(self):
        import datetime
        now = datetime.datetime.now().astimezone()
        ago = lambda **kw: self.ago((now - datetime.timedelta(**kw)).isoformat(timespec="seconds"))
        self.assertEqual([ago(seconds=5), ago(minutes=12), ago(hours=1, minutes=5), ago(days=1), ago(days=3)], ["just now", "12 min ago", "1 h ago", "yesterday", "3 days ago"])
        self.assertRegex(ago(days=30), r"^\d\d \w{3} \d{4}$")

    @property
    def ago(self):
        return self.app.state.time_ago

    def test_repeated_saves_collapse_into_one_line(self):
        rid = self.create("alice", name="a")
        c = self.client("alice")
        for n in range(3):
            self.post(c, f"/r/{rid}/edit", {"name": f"a{n}", "version": self.version(rid)})
        items = re.findall(r'<li data-hue="\d"', c.get("/").text)
        self.assertEqual(len(items), 2)                                         # created + one "updated"

    def test_deleted_records_have_no_link_and_hidden_types_are_not_shown(self):
        rid = self.create("alice", name="gone")
        self.purge(rid, f"{rid}: delete by {self.uid('alice')}")                  # the "delete" commits of older versions are still in the history
        page = self.client("alice").get("/").text
        self.assertIn('<code class="rid">ST-1</code>', page)
        self.assertNotIn('href="/r/ST-1"', page)
        self.store.save_schema("strain", {**self.store.schemas["strain"], "permissions": {"read": "admin"}}, self.users.get("root"))
        self.assertNotIn("ST-1", self.client("alice").get("/").text.split("Recent activity")[-1] if "Recent activity" in self.client("alice").get("/").text else "")
        self.assertIn("ST-1", self.client("root").get("/").text.split("Recent activity")[-1])

    def test_bulk_edits_show_as_one_line(self):
        ids = [self.create("alice", name=f"s{i}") for i in range(2)]
        self.post(self.client("alice"), "/t/strain/bulk/apply", {"ids": ids, "action": "archive"})
        page = self.client("alice").get("/").text
        self.assertRegex(page, r"bulk edit</span> <span class=\"muted\">archive \(2 strain records\)")

    def test_no_activity_section_when_search_is_used_or_nothing_happened(self):
        self.assertNotIn("Recent activity", self.client("alice").get("/").text)
        self.create("alice", name="s")
        self.assertNotIn("Recent activity", self.client("alice").get("/", params={"q": "s"}).text)

    def test_names_are_escaped(self):
        self.create("alice", name="<b>x</b>")
        page = self.client("alice").get("/").text
        self.assertNotIn("<b>x</b>", page)


class TestArchivedGreyedOut(Base):
    def test_archived_rows_and_cards_are_marked(self):
        a, b = self.create("alice", name="live"), self.create("alice", name="old")
        c = self.client("alice")
        self.post(c, f"/r/{b}/archive")
        table = c.get("/t/strain?archived=1").text
        self.assertIn(f'<tr data-href="/r/{a}"><td', table)                                  # plain row
        self.assertIn(f'<tr data-href="/r/{b}" class="archived">', table)
        cards = c.get("/t/strain?archived=1&view=cards").text
        self.assertIn('class="rcard archived"', cards)
        self.assertEqual(cards.count('class="rcard"'), 1)
        self.assertIn(f'<tr data-href="/r/{b}" class="archived">', c.get("/", params={"q": "old", "archived": "1"}).text)

    def test_the_css_greys_them_out_flatly(self):
        import os
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", "shelfblazer_app", "static", "style.css"), encoding="utf-8") as fh:
            css = fh.read()
        self.assertRegex(css, r"tr\.archived td[^{]*,\.rcard\.archived[^{]*\{opacity:\.\d+\}")


class TestStorageOnEdit(Base):
    def test_storage_is_offered_only_where_there_is_already_a_value(self):
        c = self.client("alice")
        new = self.create("alice", name="new strain")
        old = self.create("alice", name="old strain", storage="Box 5 Well A1")
        self.assertNotIn('name="f_storage"', c.get("/t/strain/new").text)
        self.assertNotIn('name="f_storage"', c.get(f"/r/{new}/edit").text)                  # nothing to correct: not offered
        self.assertIn('name="f_storage"', c.get(f"/r/{old}/edit").text)                     # old data stays editable

    def test_saving_a_form_without_the_field_keeps_the_stored_value(self):
        old = self.create("alice", name="old", storage="Box 5 Well A1")
        c = self.client("alice")
        self.post(c, f"/r/{old}/edit", {"name": "renamed", "version": self.version(old)})            # field not in the form
        self.assertEqual(self.store.records[old]["fields"]["storage"], "Box 5 Well A1")
        self.post(c, f"/r/{old}/edit", {"name": "renamed", "f_storage": "Box 6", "version": self.version(old)})
        self.assertEqual(self.store.records[old]["fields"]["storage"], "Box 6")
        self.post(c, f"/r/{old}/edit", {"name": "renamed", "f_storage": "", "version": self.version(old)})   # clearing it is still possible
        self.assertNotIn("storage", self.store.records[old]["fields"])

    def test_an_error_page_keeps_the_field_for_a_record_that_has_one(self):
        old = self.create("alice", name="old", storage="Box 5")
        r = self.post(self.client("alice"), f"/r/{old}/edit", {"name": "", "f_storage": "Box 7", "version": self.version(old)})
        self.assertEqual(r.status_code, 422)
        self.assertIn('name="f_storage"', r.text)

    def test_bulk_edit_can_still_set_storage(self):
        ids = [self.create("alice", name=f"s{i}") for i in range(2)]
        page = self.post(self.client("alice"), "/t/strain/bulk", {"ids": ids}).text
        self.assertIn("value_storage", page)


class TestFoldedFields(Base):
    def setUp(self):
        super().setUp()
        s = self.store.schemas["strain"]
        s["fields"].append({"key": "related", "label": "Related", "type": "ref", "to": "*", "multiple": True, "fold": True})

    def test_a_folded_field_sits_in_a_closed_details_after_the_notes(self):
        rid = self.create("alice", name="a")
        page = self.client("alice").get(f"/r/{rid}/edit").text
        self.assertRegex(page, r'(?s)<details class="fold"><summary>Related</summary>.*name="f_related"')
        self.assertGreater(page.index("details"), page.index('name="notes"'))
        self.assertNotRegex(page[:page.index("<details")], r'name="f_related"')

    def test_it_opens_when_it_has_a_value_or_an_error(self):
        pl = self.create("alice", "plasmid", name="p")
        rid = self.create("alice", name="a", related=pl)
        self.assertIn('<details class="fold" open>', self.client("alice").get(f"/r/{rid}/edit").text)
        r = self.post(self.client("alice"), "/t/strain/new", {"name": "x", "f_related": "PL-999"})
        self.assertEqual(r.status_code, 422)
        self.assertIn('<details class="fold" open>', r.text)

    def test_other_fields_are_not_folded_and_the_value_still_saves(self):
        pl = self.create("alice", "plasmid", name="p")
        rid = self.create("alice", name="a", related=pl)
        self.assertEqual(self.store.records[rid]["fields"]["related"], [pl])
        page = self.client("alice").get("/t/strain/new").text
        self.assertRegex(page, r'name="f_genotype"(?s:.*)<details')


if __name__ == "__main__":
    unittest.main()

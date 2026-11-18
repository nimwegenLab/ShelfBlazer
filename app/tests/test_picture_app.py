"""A type's "thumbnail" setting: the first image in that file field shows on the record page and on the cards (not in the table); the browser shrinks the upload."""
import unittest

from shelfblazer.schema_edit import validate_schema
from test_app import Base


def schema(**extra):
    return {"name": "Consumable", "prefix": "CS", "list_columns": ["id", "name"], "thumbnail": "photo", "fields": [
        {"key": "name", "label": "Name", "type": "text", "required": True},
        {"key": "photo", "label": "Photo", "type": "file"},
        {"key": "other", "label": "Other files", "type": "file", "multiple": True}], **extra}


class Picture(Base):
    def setUp(self):
        super().setUp()
        self.root, self.alice = self.users.get("root"), self.users.get("alice")
        self.store.save_schema("consumable", schema(), self.root, create=True)
        self.c = self.client("alice")
        self.rid = self.store.create("consumable", self.alice, "Tips", {})
        self.bare = self.store.create("consumable", self.alice, "No picture yet", {})

    def test_the_setting_must_name_a_file_field(self):
        self.assertEqual(validate_schema("consumable", schema(), self.store.schemas, old=self.store.schemas["consumable"]), [])
        for bad in ("name", "nope", ["photo"]):
            self.assertTrue(any("thumbnail" in p for p in validate_schema("consumable", schema(thumbnail=bad), self.store.schemas,
                                                                          old=self.store.schemas["consumable"])), bad)

    def test_record_page_shows_the_first_image(self):
        self.store.add_attachment(self.rid, self.alice, "notes.pdf", b"%PDF", field="photo")
        self.assertNotIn('class="pic"', self.c.get(f"/r/{self.rid}").text)              # a document is not a picture
        self.store.add_attachment(self.rid, self.alice, "tips.JPG", b"\xff\xd8\xff", field="photo")
        page = self.c.get(f"/r/{self.rid}").text
        self.assertIn(f'<img class="pic" src="/files/{self.rid}/tips.JPG" alt="Tips">', page)
        self.assertNotIn('class="pic"', self.c.get(f"/r/{self.bare}").text)

    def test_other_file_fields_do_not_count(self):
        self.store.add_attachment(self.rid, self.alice, "gel.png", b"\x89PNG", field="other")
        self.assertNotIn('class="pic"', self.c.get(f"/r/{self.rid}").text)

    def test_cards_show_it_and_the_table_does_not(self):
        self.store.add_attachment(self.rid, self.alice, "tips.png", b"\x89PNG", field="photo")
        cards = self.c.get("/t/consumable?view=cards").text
        self.assertEqual(cards.count('class="rpic"'), 1)
        self.assertIn(f'src="/files/{self.rid}/tips.png"', cards)
        self.assertNotIn("rpic", self.c.get("/t/consumable?view=table").text)

    def test_the_picture_can_be_fetched_by_a_reader_and_not_by_anonymous_visitors(self):
        self.store.add_attachment(self.rid, self.alice, "tips.png", b"\x89PNG", field="photo")
        self.assertEqual(self.c.get(f"/files/{self.rid}/tips.png").status_code, 200)
        self.assertIn(self.client().get(f"/files/{self.rid}/tips.png", follow_redirects=False).status_code, (303, 401))

    def test_the_form_takes_one_picture_and_marks_it_for_shrinking(self):
        form = self.c.get("/t/consumable/new").text
        self.assertRegex(form, r'<input type="file" name="f_photo" data-photo accept="image/\*">')
        self.assertRegex(form, r'<input type="file" name="f_other" multiple>')
        self.assertIn('name="f_photo" data-photo accept="image/*"', self.c.get(f"/r/{self.rid}/edit").text)    # pictures are added on the edit page
        self.assertIn("/static/photo.js", self.c.get("/").text)
        self.assertEqual(self.c.get("/static/photo.js").status_code, 200)

    def test_types_without_the_setting_are_unchanged(self):
        self.assertNotIn("data-photo", self.c.get("/t/strain/new").text)


if __name__ == "__main__":
    unittest.main()

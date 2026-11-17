"""File fields: added on the type page, chosen in the record form, attached on save, listed on the record page."""
import json, os, re, unittest

from shelfblazer import schema_edit as se
from test_app import Base, git

FIELD = {"key": "gel_image", "label": "Gel image", "type": "file"}


class FileBase(Base):
    def setUp(self):
        super().setUp()
        self.store.schemas["strain"]["fields"].append({"key": "attachments", "label": "Files", "type": "file", "multiple": True})
        self.store.schemas["strain"]["fields"].append(dict(FIELD))
        self.c = self.client("alice")

    def make(self, name="s", files=None, c=None, **extra):
        c = c or self.c
        data = {"csrf_token": self.token(c), "name": name, **extra}
        return c.post("/t/strain/new", data=data, files=files or {})

    def edit(self, rid, files=None, c=None, **extra):
        c = c or self.c
        data = {"csrf_token": self.token(c), "name": "s", "version": self.version(rid), **extra}
        return c.post(f"/r/{rid}/edit", data=data, files=files or {})

    def on_disk(self, rid, name):
        return os.path.isfile(os.path.join(self.repo, "data", "files", rid, name))


class TestTypePage(Base):
    def setUp(self):
        super().setUp()
        self.a = self.client("root")

    def test_a_file_field_can_be_added_renamed_hidden_and_never_required(self):
        r = self.post(self.a, "/admin/types/strain/fields/add", {"label": "Gel image", "type": "file", "required": "on"})
        self.assertEqual(r.status_code, 303)
        f = next(f for f in self.store.schemas["strain"]["fields"] if f["key"] == "gel_image")
        self.assertEqual(f, {"key": "gel_image", "label": "Gel image", "type": "file"})                  # required is ignored for files
        self.assertIn("File (upload)", self.a.get("/admin/types/strain").text)
        page = self.a.get("/admin/types/strain").text
        self.assertRegex(page, r'fields/gel_image/update')                                             # an editable card, not "managed by the system"
        self.post(self.a, "/admin/types/strain/fields/gel_image/update", {"label": "Gel photo"})
        self.assertEqual(next(f for f in self.store.schemas["strain"]["fields"] if f["key"] == "gel_image")["label"], "Gel photo")
        self.post(self.a, "/admin/types/strain/fields/gel_image/hidden")
        self.assertTrue(next(f for f in self.store.schemas["strain"]["fields"] if f["key"] == "gel_image")["hidden"])
        self.assertNotIn("gel_image", self.store.schemas["strain"]["list_columns"])

    def test_the_imported_files_field_is_an_ordinary_file_field(self):
        self.assertFalse(se.is_system({"key": "attachments", "type": "file"}))
        self.assertFalse(se.is_system({"key": "gel_image", "type": "file"}))
        self.assertTrue(se.is_system({"key": "legacy_owner", "type": "text"}))
        s = se.update_field({"fields": [{"key": "attachments", "label": "Files", "type": "file"}]}, "attachments", label="Documents")
        self.assertEqual(s["fields"][0]["label"], "Documents")
        page = self.a.get("/admin/types/plasmid").text
        self.assertIn("fields/attachments/update", page)                                          # an editable card, not "managed by the system"

    def test_file_fields_are_not_offered_as_list_columns_or_bulk_fields(self):
        self.store.schemas["strain"]["fields"].append(dict(FIELD))
        page = self.a.get("/admin/types/strain").text
        self.assertNotRegex(page, r'name="columns" value="gel_image"')
        self.assertNotIn("gel_image", [f["key"] for f in self.store.bulk_fields("strain")])


class TestForm(FileBase):
    def test_the_form_has_a_file_input_and_is_multipart(self):
        page = self.c.get("/t/strain/new").text
        self.assertRegex(page, r'<form method="post" class="edit" enctype="multipart/form-data">')
        self.assertRegex(page, r'Gel image\s*<input type="file" name="f_gel_image" multiple>')
        plain = self.client("bob")
        self.assertIn('type="file" name="f_attachments"', plain.get("/t/plasmid/new").text)      # the imported Files field of plasmids is a normal file field
        self.store.save_schema("lipid", se.new_type("Lipid", "LP"), self.users.get("root"), create=True)
        self.assertNotIn("multipart", plain.get("/t/lipid/new").text)                            # a type without file fields stays a plain form

    def test_files_chosen_on_a_new_record_are_attached_after_it_is_saved(self):
        r = self.make("a", files={"f_gel_image": ("gel.png", b"\x89PNG data", "image/png")})
        self.assertEqual(r.status_code, 303)
        rid = r.headers["location"].split("/")[-1]
        self.assertEqual(self.store.records[rid]["fields"]["gel_image"], [f"{rid}/gel.png"])
        self.assertTrue(self.on_disk(rid, "gel.png"))
        log = git(self.repo, "log", "--format=%s", "-3")
        self.assertIn(f"{rid}: attach gel.png by {self.uid('alice')}", log)
        self.assertIn(f"{rid}: create by {self.uid('alice')}", log)
        page = self.c.get(f"/r/{rid}").text
        self.assertIn('<h2 id="files-gel_image">Gel image</h2>', page)
        self.assertIn(f'<a href="/files/{rid}/gel.png">gel.png</a>', page)
        self.assertEqual(self.c.get(f"/files/{rid}/gel.png").content, b"\x89PNG data")

    def test_several_files_and_a_second_save_add_to_the_list_never_overwrite(self):
        r = self.make("a", files=[("f_gel_image", ("g.png", b"1", "image/png")), ("f_gel_image", ("g.png", b"2", "image/png"))])
        rid = r.headers["location"].split("/")[-1]
        self.assertEqual(self.store.records[rid]["fields"]["gel_image"], [f"{rid}/g.png", f"{rid}/g-2.png"])
        self.edit(rid, files={"f_gel_image": ("g.png", b"3", "image/png")})
        self.assertEqual(len(self.store.records[rid]["fields"]["gel_image"]), 3)
        self.assertEqual(self.c.get(f"/files/{rid}/g.png").content, b"1")

    def test_the_edit_form_lists_what_is_attached_and_saving_without_a_file_keeps_it(self):
        rid = self.make("a", files={"f_gel_image": ("gel.png", b"x", "image/png")}).headers["location"].split("/")[-1]
        self.assertIn("Attached: gel.png", self.c.get(f"/r/{rid}/edit").text)
        self.edit(rid)
        self.assertEqual(self.store.records[rid]["fields"]["gel_image"], [f"{rid}/gel.png"])
        r = self.c.post(f"/r/{rid}/edit", data={"csrf_token": self.token(self.c), "name": "", "version": self.version(rid)})
        self.assertEqual(r.status_code, 422)
        self.assertIn("Attached: gel.png", r.text)
        self.assertIn("Files are not kept: choose them again.", r.text)

    def test_a_refused_file_does_not_lose_the_record(self):
        r = self.make("a", files={"f_gel_image": ("run.exe", b"MZ", "application/octet-stream")})
        rid = r.headers["location"].split("/")[-1]
        self.assertIn(rid, self.store.records)
        self.assertNotIn("gel_image", self.store.records[rid]["fields"])
        self.assertFalse(self.on_disk(rid, "run.exe"))
        self.assertRegex(self.c.get(f"/r/{rid}").text, r"Not added: run\.exe")

    def test_a_validation_error_creates_nothing_and_attaches_nothing(self):
        r = self.make("", files={"f_gel_image": ("gel.png", b"x", "image/png")})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.store.records, {})
        self.assertFalse(os.path.exists(os.path.join(self.repo, "data", "files")))

    def test_too_much_data_is_refused_before_it_is_read(self):
        r = self.c.post("/t/strain/new", data={"csrf_token": self.token(self.c), "name": "a"}, files={"f_gel_image": ("big.dat", b"x", "application/octet-stream")},
                        headers={"content-length": str(10 ** 9)})
        self.assertIn(r.status_code, (413, 400, 422))
        self.assertEqual(self.store.records, {})

    def test_at_most_five_files_are_taken_at_once(self):
        files = [("f_gel_image", (f"g{i}.png", b"x", "image/png")) for i in range(7)]
        rid = self.make("a", files=files).headers["location"].split("/")[-1]
        self.assertEqual(len(self.store.records[rid]["fields"]["gel_image"]), 5)


class TestRecordPageAndRules(FileBase):
    def setUp(self):
        super().setUp()
        self.rid = self.make("a", files={"f_gel_image": ("gel.png", b"x", "image/png")}).headers["location"].split("/")[-1]

    def test_removing_a_file_touches_only_its_own_field(self):
        self.c.post(f"/r/{self.rid}/files", data={"csrf_token": self.token(self.c), "field": "attachments"}, files={"files": ("notes.txt", b"hi")})
        self.assertEqual(self.store.records[self.rid]["fields"]["attachments"], [f"{self.rid}/notes.txt"])
        page = self.c.get(f"/r/{self.rid}").text
        self.assertIn('<h2 id="files-attachments">Files</h2>', page)
        self.assertIn('<h2 id="files-gel_image">Gel image</h2>', page)
        self.assertIn('name="field" value="gel_image"', page)
        self.post(self.c, f"/r/{self.rid}/files/delete", {"name": "gel.png", "field": "gel_image"})
        self.assertNotIn("gel_image", self.store.records[self.rid]["fields"])
        self.assertFalse(self.on_disk(self.rid, "gel.png"))
        self.assertEqual(self.store.records[self.rid]["fields"]["attachments"], [f"{self.rid}/notes.txt"])

    def test_upload_on_the_record_page_goes_to_the_chosen_field_and_unknown_fields_are_refused(self):
        self.c.post(f"/r/{self.rid}/files", data={"csrf_token": self.token(self.c), "field": "gel_image"}, files={"files": ("two.png", b"2")})
        self.assertEqual(len(self.store.records[self.rid]["fields"]["gel_image"]), 2)
        self.c.post(f"/r/{self.rid}/files", data={"csrf_token": self.token(self.c), "field": "sequence"}, files={"files": ("x.txt", b"2")})
        self.assertNotIn("x.txt", json.dumps(self.store.records[self.rid]["fields"]))
        self.assertFalse(self.on_disk(self.rid, "x.txt"))

    def test_only_people_who_may_edit_see_the_upload_form_and_can_use_it(self):
        bob = self.client("bob")
        page = bob.get(f"/r/{self.rid}").text
        self.assertIn(f'<a href="/files/{self.rid}/gel.png">gel.png</a>', page)
        self.assertNotIn(f'action="/r/{self.rid}/files"', page)
        r = bob.post(f"/r/{self.rid}/files", data={"csrf_token": self.token(bob), "field": "gel_image"}, files={"files": ("b.png", b"b")})
        self.assertEqual(self.store.records[self.rid]["fields"]["gel_image"], [f"{self.rid}/gel.png"])

    def test_a_hidden_file_field_disappears_from_forms_and_page_but_keeps_its_data(self):
        next(f for f in self.store.schemas["strain"]["fields"] if f["key"] == "gel_image")["hidden"] = True
        self.assertNotIn("gel_image", self.c.get("/t/strain/new").text)
        self.assertNotIn("Gel image", self.c.get(f"/r/{self.rid}").text)
        self.assertEqual(self.store.records[self.rid]["fields"]["gel_image"], [f"{self.rid}/gel.png"])
        self.assertTrue(self.on_disk(self.rid, "gel.png"))

    def test_a_type_where_only_admins_edit_cannot_attach_at_creation_by_a_user(self):
        self.store.schemas["strain"]["permissions"] = {"edit": "admin"}
        r = self.make("b", files={"f_gel_image": ("n.png", b"x", "image/png")})
        rid = r.headers["location"].split("/")[-1]
        self.assertIn(rid, self.store.records)                                                           # saved, but the file is refused
        self.assertNotIn("gel_image", self.store.records[rid]["fields"])
        self.assertRegex(self.c.get(f"/r/{rid}").text, r"Not added: you may not change this record")

    def test_downloads_follow_the_read_rule_of_the_type(self):
        self.store.schemas["strain"]["permissions"] = {"read": "admin"}
        self.assertEqual(self.client("bob").get(f"/files/{self.rid}/gel.png").status_code, 404)
        self.assertEqual(self.client("root").get(f"/files/{self.rid}/gel.png").status_code, 200)


if __name__ == "__main__":
    unittest.main()

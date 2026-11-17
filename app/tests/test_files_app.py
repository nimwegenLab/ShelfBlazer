"""File upload, download and removal through the web pages."""
import os, re, unittest

from test_app import Base


class FilesBase(Base):
    def setUp(self):
        super().setUp()
        self.rid = self.create("alice", "plasmid", name="with files")      # plasmids have a Files field, like in the imported data
        self.alice, self.bob, self.root = self.client("alice"), self.client("bob"), self.client("root")

    def upload(self, c, rid, *files, token=True, **extra):
        data = {"csrf_token": self.token(c)} if token else {}
        data.update(extra)
        return c.post(f"/r/{rid}/files", data=data, files=[("files", f) for f in files])

    def stored(self, name):
        return os.path.join(self.store.repo, "data", "files", self.rid, name)


class TestUpload(FilesBase):
    def test_owner_sees_the_form_others_do_not(self):
        page = self.alice.get(f"/r/{self.rid}").text
        self.assertIn(f'action="/r/{self.rid}/files" enctype="multipart/form-data"', page)
        self.assertIn("Up to 25 MB per file", page)
        self.assertIn("No files attached yet", page)
        self.assertNotIn("/files", self.bob.get(f"/r/{self.rid}").text.replace("/static/", ""))      # no upload form for someone else
        self.assertIn('enctype="multipart/form-data"', self.root.get(f"/r/{self.rid}").text)

    def test_upload_download_roundtrip(self):
        r = self.upload(self.alice, self.rid, ("map.gb", b"LOCUS  pUA66", "application/octet-stream"))
        self.assertEqual((r.status_code, r.headers["location"]), (303, f"/r/{self.rid}"))
        self.assertTrue(os.path.isfile(self.stored("map.gb")))
        self.assertEqual(self.store.records[self.rid]["fields"]["attachments"], [f"{self.rid}/map.gb"])
        page = self.alice.get(f"/r/{self.rid}").text
        self.assertIn(f'<a href="/files/{self.rid}/map.gb">map.gb</a>', page)
        self.assertIn("Added map.gb", page)
        self.assertEqual(self.store.git("status", "--porcelain").strip(), "")
        self.assertEqual(self.store.git("log", "-1", "--format=%an").strip(), self.uid("alice"))
        d = self.bob.get(f"/files/{self.rid}/map.gb")                                          # anyone logged in can read it
        self.assertEqual((d.status_code, d.content), (200, b"LOCUS  pUA66"))
        self.assertTrue(d.headers["content-disposition"].startswith("attachment"))              # never shown inline
        self.assertEqual(d.headers["x-content-type-options"], "nosniff")
        self.assertEqual(self.client().get(f"/files/{self.rid}/map.gb").status_code, 303)       # but not without signing in

    def test_programs_scripts_and_empty_files_are_refused_with_a_message(self):
        r = self.upload(self.alice, self.rid, ("setup.exe", b"MZ", "application/x-msdownload"), ("page.html", b"<script>", "text/html"), ("empty.txt", b"", "text/plain"))
        self.assertEqual(r.status_code, 303)
        page = self.alice.get(f"/r/{self.rid}").text
        self.assertIn("programs and scripts cannot be uploaded (.exe)", page)
        self.assertIn("(.html)", page)
        self.assertIn("the file is empty", page)
        self.assertNotIn("attachments", self.store.records[self.rid]["fields"])
        self.assertFalse(os.path.exists(os.path.join(self.store.repo, "data", "files", self.rid)))

    def test_a_good_and_a_bad_file_in_one_request(self):
        self.upload(self.alice, self.rid, ("ok.csv", b"a,b", "text/csv"), ("bad.sh", b"#!/bin/sh", "text/x-sh"))
        self.assertEqual(self.store.records[self.rid]["fields"]["attachments"], [f"{self.rid}/ok.csv"])
        page = self.alice.get(f"/r/{self.rid}").text
        self.assertIn("Added ok.csv", page)
        self.assertIn("Not added", page)

    def test_too_large_file_is_refused_and_the_size_guard_stops_huge_requests(self):
        big = b"x" * (25 * 1024 * 1024 + 1)
        self.upload(self.alice, self.rid, ("big.dat", big, "application/octet-stream"))
        self.assertIn("larger than 25 MB", self.alice.get(f"/r/{self.rid}").text)
        self.assertNotIn("attachments", self.store.records[self.rid]["fields"])
        r = self.alice.post(f"/r/{self.rid}/files", content=b"x", headers={"content-length": str(900 * 1024 * 1024), "content-type": "multipart/form-data; boundary=x"})
        self.assertEqual(r.status_code, 413)                                                    # rejected before the body is read

    def test_same_name_twice_and_hostile_names(self):
        self.upload(self.alice, self.rid, ("a.xlsx", b"1", "application/octet-stream"))
        self.upload(self.alice, self.rid, ("a.xlsx", b"2", "application/octet-stream"), ("../../config/types/strain.yaml", b"{}", "text/plain"))
        names = [os.path.basename(x) for x in self.store.records[self.rid]["fields"]["attachments"]]
        self.assertEqual(names, ["a.xlsx", "a-2.xlsx", "strain.yaml"])                          # the traversal part is dropped
        self.assertTrue(os.path.isfile(self.stored("strain.yaml")))
        with open(os.path.join(self.store.repo, "config", "types", "strain.yaml")) as fh:
            self.assertIn('"name": "Strain"', fh.read())                                        # real config untouched

    def test_permissions_csrf_and_login(self):
        self.assertEqual(self.upload(self.bob, self.rid, ("a.gb", b"x", "text/plain")).status_code, 403)
        self.assertEqual(self.upload(self.alice, self.rid, ("a.gb", b"x", "text/plain"), token=False).status_code, 403)
        r = self.client().post(f"/r/{self.rid}/files", files=[("files", ("a.gb", b"x", "text/plain"))])
        self.assertEqual((r.status_code, r.headers["location"].split("?")[0]), (303, "/login"))
        self.assertNotIn("attachments", self.store.records[self.rid]["fields"])
        self.assertEqual(self.upload(self.root, self.rid, ("a.gb", b"x", "text/plain")).status_code, 303)    # admins may
        self.assertEqual(self.upload(self.alice, "ST-999", ("a.gb", b"x", "text/plain")).status_code, 404)

    def test_no_file_chosen_is_a_polite_message(self):
        r = self.alice.post(f"/r/{self.rid}/files", data={"csrf_token": self.token(self.alice)}, files=[("files", ("", b"", "application/octet-stream"))])
        self.assertEqual(r.status_code, 303)
        self.assertIn("Choose at least one file", self.alice.get(f"/r/{self.rid}").text)


class TestRemove(FilesBase):
    def setUp(self):
        super().setUp()
        self.upload(self.alice, self.rid, ("a.gb", b"x", "text/plain"), ("b.gb", b"y", "text/plain"))

    def test_owner_can_remove_and_it_is_one_commit_with_history_kept(self):
        before = int(self.store.git("rev-list", "--count", "HEAD"))
        r = self.post(self.alice, f"/r/{self.rid}/files/delete", {"name": "a.gb"})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(int(self.store.git("rev-list", "--count", "HEAD")), before + 1)
        self.assertFalse(os.path.exists(self.stored("a.gb")))
        self.assertTrue(os.path.exists(self.stored("b.gb")))
        self.assertEqual(self.store.records[self.rid]["fields"]["attachments"], [f"{self.rid}/b.gb"])
        self.assertIn("Removed a.gb", self.alice.get(f"/r/{self.rid}").text)
        self.assertEqual(self.alice.get(f"/files/{self.rid}/a.gb").status_code, 404)

    def test_others_cannot_and_unknown_names_are_reported(self):
        self.assertEqual(self.post(self.bob, f"/r/{self.rid}/files/delete", {"name": "a.gb"}).status_code, 403)
        self.assertTrue(os.path.exists(self.stored("a.gb")))
        self.post(self.alice, f"/r/{self.rid}/files/delete", {"name": "../../config/types/strain.yaml"})
        self.assertIn("no such attachment", self.alice.get(f"/r/{self.rid}").text)
        self.assertTrue(os.path.exists(os.path.join(self.store.repo, "config", "types", "strain.yaml")))
        self.assertEqual(self.alice.post(f"/r/{self.rid}/files/delete", data={"name": "a.gb"}).status_code, 403)   # no token

    def test_remove_button_asks_first(self):
        page = self.alice.get(f"/r/{self.rid}").text
        self.assertRegex(page, r'class="inline" data-confirm="Remove a.gb from PL-\d+\?"')


class TestMissingFilesAreShownHonestly(FilesBase):
    def test_a_listed_file_that_is_not_on_disk_is_explained(self):
        self.upload(self.alice, self.rid, ("a.gb", b"x", "text/plain"))
        os.remove(self.stored("a.gb"))
        self.assertIn("file not in this copy of the data", self.alice.get(f"/r/{self.rid}").text)


if __name__ == "__main__":
    unittest.main()

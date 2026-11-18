"""File upload rules and the store's attach / remove."""
import json, os, subprocess, tempfile, unittest

from shelfblazer import uploads
from shelfblazer.store import Forbidden, NotFound, Store, ValidationError
from helpers import ALICE, BOB, ROOT

SCHEMA = {"name": "Document", "prefix": "DC", "list_columns": ["id", "name"], "fields": [
    {"key": "name", "label": "Title", "type": "text", "required": True},
    {"key": "file_missing", "label": "File missing", "type": "bool"},
    {"key": "attachments", "label": "Files", "type": "file", "multiple": True}]}


class TestRules(unittest.TestCase):
    def test_safe_names(self):
        cases = {"map.gb": "map.gb", "../../etc/passwd": "passwd", "C:\\Users\\x\\plasmid map.dna": "plasmid map.dna",
                 "a/b/c.xlsx": "c.xlsx", "weird<>|?*name.txt": "weird_____name.txt", "...": "file", "": "file", ".htaccess": "htaccess",
                 "tab\tnew\nline.csv": "tabnewline.csv", "caf\u00e9 r\u00e9sum\u00e9.pdf": "caf\u00e9 r\u00e9sum\u00e9.pdf", "  spaced   out .txt": "spaced out.txt"}
        for raw, want in cases.items():
            self.assertEqual(uploads.safe_name(raw), want, raw)
        self.assertLessEqual(len(uploads.safe_name("x" * 500 + ".gb")), 120)
        self.assertTrue(uploads.safe_name("x" * 500 + ".gb").endswith(".gb"))
        for hostile in ("\x00.txt", "a\u202eb.exe", "/", "\\", "con.txt", "\n"):
            n = uploads.safe_name(hostile)
            self.assertNotIn("/", n)
            self.assertNotIn("\\", n)
            self.assertTrue(n)

    def test_programs_and_scripts_are_refused_case_insensitively(self):
        for bad in ("setup.exe", "RUN.BAT", "x.Sh", "a.js", "page.html", "p.HTM", "logo.svg", "tool.py", "m.ps1", "app.jar", "x.dll", "evil.txt.exe", "x.php", "x.vbs"):
            with self.assertRaises(uploads.UploadError, msg=bad):
                uploads.check(uploads.safe_name(bad), 10)
        for good in ("map.gb", "plasmid.dna", "plates.xlsx", "notes.docx", "data.csv", "seq.fasta", "trace.ab1", "photo.jpg", "paper.pdf", "evil.exe.txt", "README", "x.zip", "a.xml"):
            uploads.check(good, 10)

    def test_size_limits(self):
        uploads.check("a.gb", uploads.MAX_UPLOAD_BYTES)
        with self.assertRaises(uploads.UploadError):
            uploads.check("a.gb", uploads.MAX_UPLOAD_BYTES + 1)
        with self.assertRaises(uploads.UploadError):
            uploads.check("a.gb", 0)
        self.assertEqual(uploads.MAX_UPLOAD_BYTES, 25 * 1024 * 1024)

    def test_unique_names_never_overwrite(self):
        self.assertEqual(uploads.unique_name("a.gb", []), "a.gb")
        self.assertEqual(uploads.unique_name("a.gb", ["a.gb"]), "a-2.gb")
        self.assertEqual(uploads.unique_name("a.gb", ["A.GB", "a-2.gb"]), "a-3.gb")
        self.assertEqual(uploads.unique_name("README", ["readme"]), "README-2")

    def test_human_size(self):
        self.assertEqual(uploads.human_size(10), "10 B")
        self.assertEqual(uploads.human_size(2048), "2.0 KB")
        self.assertEqual(uploads.human_size(5 * 1024 * 1024), "5.0 MB")


class TestStoreAttachments(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(self.repo, "config", "types"))
        os.makedirs(os.path.join(self.repo, "data"))
        with open(os.path.join(self.repo, "config", "types", "document.yaml"), "w") as fh:
            json.dump(SCHEMA, fh)
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True)
        subprocess.run(["git", "-C", self.repo, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"], check=True)
        self.store = Store(self.repo)
        self.rid = self.store.create("document", ALICE, "protocol", {"file_missing": True})

    def tearDown(self):
        self.tmp.cleanup()

    def commits(self):
        return int(self.store.git("rev-list", "--count", "HEAD"))

    def read_file(self, name):
        with open(os.path.join(self.repo, "data", "files", self.rid, name), "rb") as fh:
            return fh.read()

    def test_attach_stores_the_file_lists_it_and_makes_one_commit(self):
        before = self.commits()
        name = self.store.add_attachment(self.rid, ALICE, "../map.gb", b"LOCUS")
        self.assertEqual(name, "map.gb")
        self.assertEqual(self.commits(), before + 1)
        self.assertEqual(self.read_file("map.gb"), b"LOCUS")
        self.assertEqual(self.store.records[self.rid]["fields"], {"attachments": [f"{self.rid}/map.gb"]})   # file_missing cleared
        self.assertEqual(self.store.git("status", "--porcelain").strip(), "")
        self.assertIn(f"data/files/{self.rid}/map.gb", self.store.git("show", "--stat", "--format=", "HEAD"))
        self.assertEqual(self.store.git("log", "-1", "--format=%an <%ae>").strip(), "alice <alice@shelfblazer.invalid>")   # the user ID only, never the name
        self.assertEqual(Store(self.repo).records[self.rid]["fields"]["attachments"], [f"{self.rid}/map.gb"])

    def test_same_name_twice_keeps_both(self):
        self.store.add_attachment(self.rid, ALICE, "a.xlsx", b"1")
        second = self.store.add_attachment(self.rid, ALICE, "A.xlsx", b"2")
        self.assertEqual(second, "A-2.xlsx")
        self.assertEqual(len(self.store.records[self.rid]["fields"]["attachments"]), 2)
        self.assertEqual(self.read_file("a.xlsx"), b"1")

    def test_refusals_change_nothing(self):
        before = self.commits()
        for fname, data in (("tool.exe", b"MZ"), ("x.sh", b"#!"), ("empty.txt", b""), ("big.bin.txt", b"x" * (uploads.MAX_UPLOAD_BYTES + 1))):
            with self.assertRaises(ValidationError, msg=fname):
                self.store.add_attachment(self.rid, ALICE, fname, data)
        self.assertEqual(self.commits(), before)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "data", "files", self.rid)))
        self.assertEqual(self.store.git("status", "--porcelain").strip(), "")

    def test_only_the_owner_or_an_admin(self):
        with self.assertRaises(Forbidden):
            self.store.add_attachment(self.rid, BOB, "a.gb", b"x")
        self.store.add_attachment(self.rid, ROOT, "a.gb", b"x")
        with self.assertRaises(Forbidden):
            self.store.remove_attachment(self.rid, BOB, "a.gb")
        with self.assertRaises(NotFound):
            self.store.add_attachment("DC-999", ALICE, "a.gb", b"x")

    def test_remove_deletes_file_and_entry_in_one_commit(self):
        self.store.add_attachment(self.rid, ALICE, "a.gb", b"x")
        self.store.add_attachment(self.rid, ALICE, "b.gb", b"y")
        before = self.commits()
        self.store.remove_attachment(self.rid, ALICE, "a.gb")
        self.assertEqual(self.commits(), before + 1)
        self.assertEqual(self.store.records[self.rid]["fields"]["attachments"], [f"{self.rid}/b.gb"])
        self.assertFalse(os.path.exists(os.path.join(self.repo, "data", "files", self.rid, "a.gb")))
        self.assertEqual(self.store.git("show", f"HEAD~1:data/files/{self.rid}/a.gb"), "x")        # still in history
        self.store.remove_attachment(self.rid, ALICE, "b.gb")
        self.assertNotIn("attachments", self.store.records[self.rid]["fields"])
        with self.assertRaises(ValidationError):
            self.store.remove_attachment(self.rid, ALICE, "b.gb")
        with self.assertRaises(ValidationError):
            self.store.remove_attachment(self.rid, ALICE, "../../config/types/document.yaml")      # only listed attachments
        self.assertTrue(os.path.exists(os.path.join(self.repo, "config", "types", "document.yaml")))

    def test_file_count_is_capped(self):
        for i in range(uploads.MAX_FILES_PER_RECORD):
            self.store.add_attachment(self.rid, ALICE, f"f{i}.txt", b"x")
        with self.assertRaises(ValidationError):
            self.store.add_attachment(self.rid, ALICE, "one-too-many.txt", b"x")


if __name__ == "__main__":
    unittest.main()

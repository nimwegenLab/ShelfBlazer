"""Attached files in Git LFS: pointers in the history, contents on disk, setup of an existing repository, and a clear refusal without git-lfs."""
import json, os, shutil, subprocess, tempfile, unittest
from unittest import mock

from shelfblazer import store as store_mod
from shelfblazer.store import LFS_ATTRIBUTES, Store, StoreError
from helpers import ALICE

SCHEMA = {"name": "Document", "prefix": "DC", "list_columns": ["id", "name"], "fields": [
    {"key": "name", "label": "Title", "type": "text", "required": True},
    {"key": "attachments", "label": "Files", "type": "file", "multiple": True}]}
CONTENT = b"\x89PNG\r\n" + bytes(range(256)) * 40
POINTER = "version https://git-lfs.github.com/spec/v1"


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, check=True).stdout


@unittest.skipUnless(shutil.which("git-lfs"), "git-lfs is not installed")
class TestLfs(unittest.TestCase):
    def make_repo(self, lfs):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(repo, "config", "types"))
        os.makedirs(os.path.join(repo, "data"))
        with open(os.path.join(repo, "config", "types", "document.yaml"), "w") as fh:
            json.dump(SCHEMA, fh)
        if lfs:
            with open(os.path.join(repo, ".gitattributes"), "w") as fh:
                fh.write(LFS_ATTRIBUTES)
        git(repo, "init", "-q", "-b", "main")
        git(repo, "-c", "filter.lfs.process=", "-c", "filter.lfs.required=false", "-c", "filter.lfs.clean=cat", "-c", "filter.lfs.smudge=cat", "add", "-A")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
        return repo

    def attach(self, s):
        rid = s.create("document", ALICE, "doc", {})
        s.add_attachment(rid, ALICE, "gel.png", CONTENT)
        return rid

    def test_new_repository_stores_uploads_as_pointers(self):
        repo = self.make_repo(lfs=True)
        s = Store(repo)                                                             # switches the LFS filter on for this repository
        self.assertTrue(s.uses_lfs())
        self.assertIn("git-lfs", git(repo, "config", "--get", "filter.lfs.process"))
        rid = self.attach(s)
        self.assertTrue(git(repo, "show", f"HEAD:data/files/{rid}/gel.png").startswith(POINTER))
        with open(os.path.join(repo, "data", "files", rid, "gel.png"), "rb") as fh:
            self.assertEqual(fh.read(), CONTENT)                                    # served from disk unchanged
        self.assertFalse(git(repo, "show", f"HEAD:data/document/{rid}.md").startswith(POINTER))   # records stay plain text
        s.remove_attachment(rid, ALICE, "gel.png")
        self.assertFalse(os.path.exists(os.path.join(repo, "data", "files", rid, "gel.png")))
        self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_enable_on_existing_repository_converts_current_files(self):
        repo = self.make_repo(lfs=False)
        s = Store(repo)
        rid = self.attach(s)
        raw = subprocess.run(["git", "-C", repo, "show", f"HEAD:data/files/{rid}/gel.png"], capture_output=True, check=True).stdout
        self.assertEqual(raw, CONTENT)                                              # plain git until LFS is switched on
        s.enable_lfs()
        self.assertTrue(s.uses_lfs())
        self.assertTrue(git(repo, "show", f"HEAD:data/files/{rid}/gel.png").startswith(POINTER))
        self.assertIn("store attached files with Git LFS", git(repo, "log", "-1", "--format=%s"))
        old = subprocess.run(["git", "-C", repo, "show", f"HEAD~1:data/files/{rid}/gel.png"], capture_output=True, check=True).stdout
        self.assertEqual(old, CONTENT)                                              # history kept as it was
        with open(os.path.join(repo, "data", "files", rid, "gel.png"), "rb") as fh:
            self.assertEqual(fh.read(), CONTENT)
        s.add_attachment(rid, ALICE, "second.png", CONTENT)
        self.assertTrue(git(repo, "show", f"HEAD:data/files/{rid}/second.png").startswith(POINTER))

    def test_rewrite_history_converts_old_commits(self):
        repo = self.make_repo(lfs=False)
        s = Store(repo)
        rid = self.attach(s)
        s.enable_lfs(rewrite_history=True)
        for rev in git(repo, "log", "--format=%H", "--", f"data/files/{rid}/gel.png").split():
            self.assertTrue(git(repo, "show", f"{rev}:data/files/{rid}/gel.png").startswith(POINTER))
        with open(os.path.join(repo, "data", "files", rid, "gel.png"), "rb") as fh:
            self.assertEqual(fh.read(), CONTENT)
        self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_refuses_dirty_repository(self):
        repo = self.make_repo(lfs=False)
        s = Store(repo)
        with open(os.path.join(repo, "stray.txt"), "w") as fh:
            fh.write("x")
        with self.assertRaises(StoreError):
            s.enable_lfs()

    def test_lfs_repository_without_git_lfs_refuses_to_start(self):
        repo = self.make_repo(lfs=True)
        with mock.patch.object(store_mod.shutil, "which", return_value=None):
            with self.assertRaisesRegex(StoreError, "git-lfs is not installed"):
                Store(repo)
        Store(self.make_repo(lfs=False))                                            # a plain repository does not need it


if __name__ == "__main__":
    unittest.main()

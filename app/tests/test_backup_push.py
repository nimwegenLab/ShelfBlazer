"""deploy/backup-push.sh: push when ahead, stay quiet when not, keep within the LFS budget, report failures. Needs git; the LFS tests need git-lfs."""
import os, pathlib, shutil, subprocess, tempfile, unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "deploy" / "backup-push.sh"
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t", GIT_TERMINAL_PROMPT="0")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=ENV).stdout


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class BackupPushTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.home, self.remote = root / "home", root / "remote.git"
        self.data = self.home / "data"
        self.data.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.data)], check=True, env=ENV)
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True, env=ENV)
        if shutil.which("git-lfs"):
            (self.data / ".gitattributes").write_text("data/files/** filter=lfs diff=lfs merge=lfs -text\n")
            git(self.data, "lfs", "install", "--local")
        (self.data / "a.md").write_text("one\n")
        git(self.data, "add", "-A")
        git(self.data, "commit", "-q", "-m", "first")

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, **env):
        return subprocess.run(["bash", str(SCRIPT), str(self.data)], capture_output=True, text=True, env=dict(ENV, **env))

    def status(self):
        return (self.home / "backup-status").read_text().split(" ", 2)[1]

    def remote_commits(self):
        return int(git(self.remote, "rev-list", "--all", "--count").strip())

    def remote_lfs_objects(self):
        return sum(1 for p in self.remote.rglob("*") if p.is_file() and "lfs" in p.parts and "objects" in p.parts)

    def commit_attachment(self, name):
        folder = self.data / "data" / "files" / "IT-1"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(os.urandom(200_000))
        git(self.data, "add", "-A")
        git(self.data, "commit", "-q", "-m", f"attach {name}")

    def test_without_a_remote_it_reports_and_succeeds(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.status(), "NO_REMOTE")
        self.assertTrue((self.home / "backup-lastrun").exists())

    def test_pushes_when_ahead_and_is_quiet_when_not(self):
        git(self.data, "remote", "add", "origin", str(self.remote))
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual((self.status(), self.remote_commits()), ("OK", 1))
        before = (self.home / "backup-status").read_text()
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual((self.home / "backup-status").read_text(), before)               # nothing to push: no status written...
        self.assertTrue((self.home / "backup-lastrun").exists())                          # ...but the heartbeat shows the run happened
        (self.data / "a.md").write_text("two\n")
        git(self.data, "commit", "-q", "-am", "second")
        self.run_script()
        self.assertEqual(self.remote_commits(), 2)

    def test_failure_is_reported_and_the_next_run_recovers(self):
        git(self.data, "remote", "add", "origin", str(self.home / "nowhere.git"))
        r = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.status(), "FAILED")
        git(self.data, "remote", "set-url", "origin", str(self.remote))
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual((self.status(), self.remote_commits()), ("OK", 1))

    def test_a_running_copy_blocks_a_second_run(self):
        git(self.data, "remote", "add", "origin", str(self.remote))
        lock = self.home / "backup-push.lock"
        lock.mkdir()
        (lock / "pid").write_text(str(os.getpid()))                                       # a live process holds the lock
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(self.remote_commits(), 0)

    @unittest.skipUnless(shutil.which("git-lfs"), "git-lfs is not installed")
    def test_attachments_are_uploaded_within_the_budget(self):
        git(self.data, "remote", "add", "origin", str(self.remote))
        self.commit_attachment("a.bin")
        self.run_script()
        self.assertEqual((self.status(), self.remote_commits(), self.remote_lfs_objects()), ("OK", 2, 1))

    @unittest.skipUnless(shutil.which("git-lfs"), "git-lfs is not installed")
    def test_over_budget_pushes_commits_without_attachments_then_catches_up(self):
        git(self.data, "remote", "add", "origin", str(self.remote))
        self.commit_attachment("a.bin")
        self.assertEqual(self.run_script(LFS_BUDGET_MB="0").returncode, 0)
        self.assertEqual((self.status(), self.remote_commits(), self.remote_lfs_objects()), ("WARN", 2, 0))
        self.assertTrue((self.home / "backup-lfs-skipped").exists())
        self.run_script(LFS_BUDGET_MB="0")                                                # still over budget, nothing new: stays quiet
        self.assertEqual(self.remote_lfs_objects(), 0)
        self.assertEqual(self.run_script().returncode, 0)                                 # room again: the skipped attachment goes up
        self.assertEqual((self.status(), self.remote_lfs_objects()), ("OK", 1))
        self.assertFalse((self.home / "backup-lfs-skipped").exists())


if __name__ == "__main__":
    unittest.main()

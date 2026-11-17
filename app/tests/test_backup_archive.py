"""deploy/backup-archive.sh and restore-archive.sh: an encrypted archive that restores completely, and time-limited retention.
Needs git, tar and gpg; the attachment checks need git-lfs."""
import datetime, os, pathlib, shutil, subprocess, tempfile, unittest

DEPLOY = pathlib.Path(__file__).resolve().parents[2] / "deploy"
BACKUP, RESTORE = DEPLOY / "backup-archive.sh", DEPLOY / "restore-archive.sh"
MARKER = b"SECRET-MARKER-4711"
RECIPIENT = "backup-test@example.invalid"
GIT_ENV = dict(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t", GIT_TERMINAL_PROMPT="0")


def have(*tools):
    return all(shutil.which(t) for t in tools)


@unittest.skipUnless(have("git", "tar", "gpg"), "git, tar and gpg are needed")
class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.gnupg = tempfile.mkdtemp(prefix="g", dir="/tmp")          # short path: gpg's agent socket must fit in it
        os.chmod(self.gnupg, 0o700)
        self.env = dict(os.environ, GNUPGHOME=self.gnupg, **GIT_ENV)
        subprocess.run(["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "", "--quick-generate-key",
                        f"Backup Test <{RECIPIENT}>", "default", "default", "never"], check=True, capture_output=True, env=self.env)
        self.home = self.root / "home"
        data = self.data = self.home / "data"
        (self.home / "instance").mkdir(parents=True)
        (self.home / "instance" / "secret.key").write_bytes(MARKER)
        (self.home / "instance" / "users.json").write_text('{"users": {}}')
        data.mkdir()
        self.git(data, "init", "-q", "-b", "main")
        self.lfs = have("git-lfs")
        if self.lfs:
            (data / ".gitattributes").write_text("data/files/** filter=lfs diff=lfs merge=lfs -text\n")
            self.git(data, "lfs", "install", "--local")
        (data / "a.md").write_text("one\n")
        (data / "data" / "files" / "IT-1").mkdir(parents=True)
        self.attachment = os.urandom(150_000)
        (data / "data" / "files" / "IT-1" / "a.bin").write_bytes(self.attachment)
        self.git(data, "add", "-A")
        self.git(data, "commit", "-q", "-m", "first")
        (data / "a.md").write_text("two\n")
        self.git(data, "commit", "-q", "-am", "second")
        self.dest = self.root / "dest"

    def tearDown(self):
        subprocess.run(["gpgconf", "--kill", "all"], env=self.env, capture_output=True)
        shutil.rmtree(self.gnupg, ignore_errors=True)
        self.tmp.cleanup()

    def git(self, repo, *args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, env=self.env).stdout

    def backup(self, **extra):
        env = {**self.env, "GPG_RECIPIENT": RECIPIENT, **extra}
        return subprocess.run(["bash", str(BACKUP), str(self.home), str(self.dest)], capture_output=True, text=True, env=env)

    def archives(self):
        return sorted(p.name for p in self.dest.glob("shelfblazer-*.tar.gz.gpg"))

    def test_the_archive_is_encrypted_and_restores_everything(self):
        r = self.backup()
        self.assertEqual(r.returncode, 0, r.stderr)
        (archive,) = list(self.dest.glob("shelfblazer-*.tar.gz.gpg"))
        self.assertNotIn(MARKER, archive.read_bytes())                                    # the session key is not readable in it
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
        self.assertTrue((self.dest / "last-archive").exists())
        restored = self.root / "restored"
        r = subprocess.run(["bash", str(RESTORE), str(archive), str(restored)], capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((restored / "instance" / "secret.key").read_bytes(), MARKER)
        self.assertEqual(self.git(restored / "data", "log", "--format=%s").split(), self.git(self.data, "log", "--format=%s").split())
        self.assertEqual(self.git(restored / "data", "status", "--porcelain"), "")
        self.assertEqual((restored / "data" / "a.md").read_text(), "two\n")
        if self.lfs:                                                                      # the real file, not an LFS pointer
            self.assertEqual((restored / "data" / "data" / "files" / "IT-1" / "a.bin").read_bytes(), self.attachment)

    def test_retention_keeps_recent_and_first_of_month_archives(self):
        today = datetime.datetime.now(datetime.timezone.utc).date()
        def first_of_month(months_back):
            y, m = today.year, today.month - months_back
            while m < 1:
                y, m = y - 1, m + 12
            return datetime.date(y, m, 1)
        old_daily = today - datetime.timedelta(days=45)
        if old_daily.day == 1:
            old_daily -= datetime.timedelta(days=1)
        names = {"recent": today - datetime.timedelta(days=5), "old_daily": old_daily,
                 "old_first": first_of_month(3), "ancient_first": first_of_month(15)}
        self.dest.mkdir()
        for d in names.values():
            (self.dest / f"shelfblazer-{d}.tar.gz.gpg").write_bytes(b"x")
        self.assertEqual(self.backup().returncode, 0)
        left = set(self.archives())
        self.assertIn(f"shelfblazer-{names['recent']}.tar.gz.gpg", left)
        self.assertIn(f"shelfblazer-{names['old_first']}.tar.gz.gpg", left)
        self.assertNotIn(f"shelfblazer-{names['old_daily']}.tar.gz.gpg", left)
        self.assertNotIn(f"shelfblazer-{names['ancient_first']}.tar.gz.gpg", left)
        self.assertIn(f"shelfblazer-{today}.tar.gz.gpg", left)

    def test_refuses_without_a_usable_recipient(self):
        r = subprocess.run(["bash", str(BACKUP), str(self.home), str(self.dest)], capture_output=True, text=True, env=self.env)
        self.assertNotEqual(r.returncode, 0)
        r = self.backup(GPG_RECIPIENT="nobody@example.invalid")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.archives(), [])

    def test_restore_refuses_a_used_target(self):
        self.assertEqual(self.backup().returncode, 0)
        (archive,) = list(self.dest.glob("shelfblazer-*.tar.gz.gpg"))
        used = self.root / "used"
        used.mkdir()
        (used / "x").write_text("x")
        r = subprocess.run(["bash", str(RESTORE), str(archive), str(used)], capture_output=True, text=True, env=self.env)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()

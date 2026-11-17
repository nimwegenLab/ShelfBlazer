"""Command line path handling (--home, $SHELFBLAZER_HOME, explicit overrides). Run with the app tests."""
import contextlib, io, json, os, shutil, subprocess, tempfile, unittest
from unittest import mock

from shelfblazer_app import cli


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class TestCliPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        src = os.path.join(self.home, "import_out")
        write(os.path.join(src, "config", "types", "strain.yaml"), json.dumps({"name": "Strain", "prefix": "ST", "list_columns": ["id"], "fields": []}))
        write(os.path.join(src, "data", "strain", "ST-1.md"), "---\nid: \"ST-1\"\n---\n")
        write(os.path.join(src, "users_stubs.yaml"), json.dumps([{"username": "root", "name": "Root", "role": "admin"}, {"username": "alice", "name": "Alice"}]))

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv, env=None):
        out = io.StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=False), contextlib.redirect_stdout(out):
            cli.main(list(argv))
        return out.getvalue()

    def test_home_option_derives_every_folder(self):
        self.run_cli("--home", self.home, "init-data")
        self.run_cli("--home", self.home, "import-users")
        self.assertTrue(os.path.isfile(os.path.join(self.home, "data", "data", "strain", "ST-1.md")))
        self.assertTrue(os.path.isdir(os.path.join(self.home, "data", ".git")))
        self.assertTrue(os.path.isfile(os.path.join(self.home, "instance", "users.json")))
        log = subprocess.run(["git", "-C", os.path.join(self.home, "data"), "log", "--format=%an"], capture_output=True, text=True).stdout
        self.assertIn("ShelfBlazer import", log)

    def test_environment_variable_and_flag_precedence(self):
        self.run_cli("init-data", env={"SHELFBLAZER_HOME": self.home})
        self.assertTrue(os.path.isdir(os.path.join(self.home, "data", ".git")))
        other = os.path.join(self.tmp.name, "elsewhere")
        self.run_cli("--home", self.home, "--instance", other, "import-users")  # explicit flag beats --home
        self.assertTrue(os.path.isfile(os.path.join(other, "users.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.home, "instance", "users.json")))    # init-data imports the stubs into the home's instance

    def test_default_home_is_var(self):
        a = cli.resolve_paths(mock.Mock(home=None, instance=None, cmd="run", data=None))
        self.assertEqual((a.data, a.instance), (os.path.join("var", "data"), os.path.join("var", "instance")))

    def test_refuses_non_importer_folder_and_nonempty_target(self):
        with self.assertRaises(SystemExit):
            self.run_cli("--home", self.home, "init-data", "--from", self.tmp.name)
        self.run_cli("--home", self.home, "init-data")
        with self.assertRaises(SystemExit):
            self.run_cli("--home", self.home, "init-data")  # target exists and is not empty


class TestDropFieldCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        src = os.path.join(self.home, "import_out")
        write(os.path.join(src, "config", "types", "document.yaml"), json.dumps({"name": "Document", "prefix": "DC", "list_columns": ["id", "name", "category"], "fields": [
            {"key": "name", "label": "Title", "type": "text"}, {"key": "category", "label": "Category", "type": "text"}]}))
        for n in (1, 2):
            write(os.path.join(src, "data", "document", f"DC-{n}.md"), f'---\nid: "DC-{n}"\ntype: "document"\nname: "d{n}"\ncreated_by: "admin"\ncreated_at: null\nupdated_at: null\narchived: false\nlegacy_id: {n}\nfields:\n  category: "Documents"\n---\n\n')
        self.run_cli = lambda *argv: self._run(*argv)
        self._run("--home", self.home, "init-data")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(list(argv))
        return out.getvalue()

    def test_without_yes_nothing_changes(self):
        with self.assertRaises(SystemExit) as cm:
            self._run("--home", self.home, "drop-field", "document", "category")
        self.assertIn("--yes", str(cm.exception))
        with open(os.path.join(self.home, "data", "config", "types", "document.yaml")) as fh:
            self.assertIn("category", fh.read())

    def test_drops_the_field_everywhere_with_one_commit(self):
        before = subprocess.run(["git", "-C", os.path.join(self.home, "data"), "rev-list", "--count", "HEAD"], capture_output=True, text=True).stdout.strip()
        out = self._run("--home", self.home, "drop-field", "document", "category", "--yes")
        self.assertIn("2 record(s) updated", out)
        data = os.path.join(self.home, "data")
        with open(os.path.join(data, "config", "types", "document.yaml")) as fh:
            schema = json.load(fh)
        self.assertEqual([f["key"] for f in schema["fields"]], ["name"])
        self.assertEqual(schema["list_columns"], ["id", "name"])
        for n in (1, 2):
            with open(os.path.join(data, "data", "document", f"DC-{n}.md")) as fh:
                self.assertNotIn("category", fh.read())
        after = subprocess.run(["git", "-C", data, "rev-list", "--count", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(int(after), int(before) + 1)

    def test_unknown_type_or_field_is_reported(self):
        for argv in (("drop-field", "ghost", "x", "--yes"), ("drop-field", "document", "ghost", "--yes"), ("drop-field", "document", "name", "--yes")):
            with self.assertRaises(SystemExit):
                self._run("--home", self.home, *argv)


@unittest.skipUnless(shutil.which("git-lfs"), "git-lfs is not installed")
class TestLfsCommands(TestCliPaths):
    """init-data puts attached files in Git LFS from the first commit; lfs-setup converts an existing repository."""

    def setUp(self):
        super().setUp()
        write(os.path.join(self.home, "import_out", "data", "files", "ST-1", "map.gb"), "LOCUS pX 100 bp\n")
        write(os.path.join(self.home, "import_out", "legacy", "files", "old.xls"), "binary-ish")

    def head(self, path):
        return subprocess.run(["git", "-C", os.path.join(self.home, "data"), "show", f"HEAD:{path}"], capture_output=True, text=True).stdout

    def test_init_data_uses_lfs_for_files_only(self):
        out = self.run_cli("--home", self.home, "init-data")
        self.assertIn("Git LFS", out)
        self.assertTrue(self.head("data/files/ST-1/map.gb").startswith("version https://git-lfs"))
        self.assertFalse(os.path.exists(os.path.join(self.home, "data", "legacy")))       # the old tables stay in import_out and the LabCollector backup
        with open(os.path.join(self.home, "data", ".gitattributes")) as fh:
            self.assertNotIn("legacy", fh.read())
        self.assertTrue(self.head("data/strain/ST-1.md").startswith("---"))
        with open(os.path.join(self.home, "data", "data", "files", "ST-1", "map.gb")) as fh:
            self.assertEqual(fh.read(), "LOCUS pX 100 bp\n")

    def test_no_lfs_keeps_plain_git_and_lfs_setup_converts_later(self):
        self.run_cli("--home", self.home, "init-data", "--no-lfs")
        self.assertFalse(os.path.exists(os.path.join(self.home, "data", ".gitattributes")))
        self.assertEqual(self.head("data/files/ST-1/map.gb"), "LOCUS pX 100 bp\n")
        with self.assertRaises(SystemExit):
            self.run_cli("--home", self.home, "lfs-setup", "--rewrite-history")             # needs --yes
        self.assertIn("Git LFS", self.run_cli("--home", self.home, "lfs-setup"))
        self.assertTrue(self.head("data/files/ST-1/map.gb").startswith("version https://git-lfs"))
        with self.assertRaises(SystemExit):
            self.run_cli("--home", self.home, "lfs-setup")                                  # already done

    def test_init_data_without_git_lfs_stops_cleanly(self):
        from shelfblazer import store
        with mock.patch.object(store.shutil, "which", return_value=None), self.assertRaises(SystemExit) as cm:
            self.run_cli("--home", self.home, "init-data")
        self.assertIn("--no-lfs", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.home, "data")))


class TestStubDates(unittest.TestCase):
    """The creation date an importer stub carries (from LabCollector's history log) reaches the accounts."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.stubs = os.path.join(self.tmp.name, "users_stubs.yaml")
        write(self.stubs, json.dumps([
            {"username": "alice", "name": "Alice", "legacy_admin_id": 2, "created": "2012-03-27T15:50:18"},
            {"username": "bob", "name": "Bob", "legacy_admin_id": 3, "created": None}]))
        from shelfblazer.auth import Users
        self.users = Users(os.path.join(self.tmp.name, "users.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_accounts_get_the_date_from_the_stub(self):
        self.assertEqual(cli.import_stubs(self.users, self.stubs), (2, 0, 0))
        self.assertEqual(self.users.get("alice")["created"], "2012-03-27T15:50:18")
        self.assertRegex(self.users.get("bob")["created"], r"^\d{4}-\d\d-\d\dT")           # no date known: the time of the import

    def test_imported_accounts_that_exist_get_their_date_once_and_others_are_left_alone(self):
        self.users.create("alice", "Alice", legacy={"admin_id": 2, "contact_id": 1})
        self.users.create("bob", "Bob")                                                     # made in the app: no LabCollector ID
        write(self.stubs, json.dumps([{"username": "alice", "legacy_admin_id": 2, "created": "2012-03-27T15:50:18"},
                                      {"username": "bob", "legacy_admin_id": 3, "created": "2013-01-01T10:00:00"}]))
        before = self.users.get("bob")["created"]
        self.assertEqual(cli.import_stubs(self.users, self.stubs), (0, 2, 1))
        self.assertEqual(self.users.get("alice")["created"], "2012-03-27T15:50:18")
        self.assertEqual(self.users.get("bob")["created"], before)
        self.assertEqual(cli.import_stubs(self.users, self.stubs), (0, 2, 0))               # nothing left to change

    def test_dates_only_creates_nobody(self):
        self.users.create("alice", "Alice", legacy={"admin_id": 2, "contact_id": 1})
        self.assertEqual(cli.import_stubs(self.users, self.stubs, create=False), (0, 1, 1))
        self.assertIsNone(self.users.get("bob"))                                            # not created again (say, erased on purpose)

    def test_set_created_checks_its_input(self):
        self.users.create("alice", "Alice")
        with self.assertRaises(ValueError):
            self.users.set_created("alice", "last year")
        with self.assertRaises(KeyError):
            self.users.set_created("nobody", "2012-03-27T15:50:18")


class TestEmptyInstance(unittest.TestCase):
    """init-data --empty: a new instance with one starter type and an administrator, no records."""
    PW = "correct horse battery"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.data = os.path.join(self.home, "data")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(["--home", self.home, *argv])
        return out.getvalue()

    def test_creates_repository_starter_type_and_admin(self):
        out = self.run_cli("init-data", "--empty", "--no-lfs")
        self.assertIn("set-password admin", out)
        self.assertTrue(os.path.isdir(os.path.join(self.data, ".git")))
        self.assertFalse(os.path.exists(os.path.join(self.data, ".gitattributes")))        # --no-lfs
        with open(os.path.join(self.data, "config", "types", "item.yaml"), encoding="utf-8") as fh:
            schema = json.load(fh)
        from shelfblazer.schema_edit import validate_schema
        self.assertEqual(validate_schema("item", schema, {}), [])
        self.assertEqual(schema["prefix"], "IT")
        log = subprocess.run(["git", "-C", self.data, "log", "--format=%an|%s"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(log, "ShelfBlazer init|Initial empty data repository")
        from shelfblazer.auth import Users
        admin = Users(os.path.join(self.home, "instance", "users.json")).get("admin")
        self.assertEqual((admin["role"], admin["active"], admin["pw"]), ("admin", True, None))

    @unittest.skipUnless(shutil.which("git-lfs"), "git-lfs is not installed")
    def test_attached_files_use_lfs_by_default(self):
        self.run_cli("init-data", "--empty")
        with open(os.path.join(self.data, ".gitattributes"), encoding="utf-8") as fh:
            self.assertIn("filter=lfs", fh.read())

    def test_refuses_a_used_target_and_the_combination_with_from(self):
        self.run_cli("init-data", "--empty", "--no-lfs")
        with self.assertRaises(SystemExit):
            self.run_cli("init-data", "--empty", "--no-lfs")                               # target exists and is not empty
        with self.assertRaises(SystemExit):
            self.run_cli("init-data", "--empty", "--from", self.tmp.name)

    def test_admin_signs_in_and_creates_the_first_record(self):
        import re
        from starlette.testclient import TestClient
        from shelfblazer import auth
        from shelfblazer_app.web import create_app
        auth.SCRYPT.update(n=2**10)
        self.run_cli("init-data", "--empty", "--no-lfs")
        app = create_app(self.data, os.path.join(self.home, "instance"))
        app.state.users.set_password("admin", self.PW)
        c = TestClient(app, follow_redirects=False)

        def token(path):
            return re.search(r'name="csrf_token" value="([^"]+)"', c.get(path).text).group(1)
        r = c.post("/login", data={"username": "admin", "password": self.PW, "csrf_token": token("/login")})
        self.assertEqual(r.status_code, 303)
        r = c.post("/t/item/new", data={"name": "Pipette tips", "csrf_token": token("/me")})
        self.assertEqual(r.status_code, 303, r.text[:300])
        self.assertEqual(r.headers["location"], "/r/IT-1")
        self.assertEqual(app.state.store.records["IT-1"]["name"], "Pipette tips")
        self.assertIn("Item", c.get("/").text)


if __name__ == "__main__":
    unittest.main()

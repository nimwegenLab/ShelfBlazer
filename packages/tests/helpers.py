"""Shared by the core tests (not a test module): the test people, a small store with strains and a plasmid, and purge."""
import atexit, json, os, shutil, subprocess, tempfile, unittest

from shelfblazer.store import Store


def purge(store, rid, actor):
    """Stand-in for the planned purge command: the record file is gone from the repository and the store.
    The app itself never deletes records (archive only); tests use this when they need a record that no longer exists."""
    path = store.path_of(rid, store.records[rid]["type"])
    os.remove(path)
    store.commit([path], f"{rid}: purge", actor)
    del store.records[rid]
    store._notify(rid)


STRAIN = {"name": "Strain", "prefix": "ST", "list_columns": ["id", "name"], "fields": [
    {"key": "name", "label": "Name", "type": "text", "required": True},
    {"key": "genotype", "label": "Genotype", "type": "text"},
    {"key": "tags", "label": "Tags", "type": "multiselect", "options": ["a", "b", "c"]},
    {"key": "ok", "label": "OK", "type": "bool"},
    {"key": "plasmid", "label": "Plasmid", "type": "ref", "to": "plasmid", "multiple": True},
    {"key": "hiddenf", "label": "Hidden", "type": "text", "hidden": True},
    {"key": "calc", "label": "Calc", "type": "computed", "expr": "1"}]}
PLASMID = {"name": "Plasmid", "prefix": "PL", "list_columns": ["id", "name"], "fields": [{"key": "name", "label": "Name", "type": "text", "required": True}]}
ALICE = {"username": "alice", "uid": "alice", "name": "Alice A", "role": "user", "active": True}
BOB = {"username": "bob", "uid": "bob", "name": "Bob B", "role": "user", "active": True}
ROOT = {"username": "root", "uid": "root", "name": "Root", "role": "admin", "active": True}


_READY = {}


def _ready_repository():
    """A finished test repository (the two types and the sample records), built once per test process and copied for every test.
    Each test still gets its own private copy and changes nothing the others see; only the git work of building it is not repeated."""
    if not _READY:
        root = tempfile.mkdtemp(prefix="sb-ready-")
        atexit.register(shutil.rmtree, root, ignore_errors=True)
        repo = os.path.join(root, "repo")
        os.makedirs(os.path.join(repo, "config", "types"))
        os.makedirs(os.path.join(repo, "data"))
        for slug, sch in (("strain", STRAIN), ("plasmid", PLASMID)):
            with open(os.path.join(repo, "config", "types", slug + ".yaml"), "w") as fh:
                json.dump(sch, fh)
        subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
        subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
        subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"], check=True)
        store = Store(repo, users_lookup=lambda n: {"alice": ALICE, "bob": BOB, "root": ROOT}.get(n))
        _READY.update(repo=repo, a=[store.create("strain", ALICE, f"a{i}", {"genotype": "g"}) for i in range(3)],
                      b=[store.create("strain", BOB, f"b{i}", {"genotype": "g"}) for i in range(2)], p=store.create("plasmid", ALICE, "pX", {}))
    return _READY


class Base(unittest.TestCase):
    def setUp(self):
        ready = _ready_repository()
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        shutil.copytree(ready["repo"], self.repo, symlinks=True)
        self.store = Store(self.repo, users_lookup=lambda n: {"alice": ALICE, "bob": BOB, "root": ROOT}.get(n))
        self.a, self.b, self.p = list(ready["a"]), list(ready["b"]), ready["p"]

    def tearDown(self):
        self.tmp.cleanup()

    def commits(self):
        return int(self.store.git("rev-list", "--count", "HEAD"))

    def head_message(self):
        return self.store.git("log", "-1", "--format=%s").strip()

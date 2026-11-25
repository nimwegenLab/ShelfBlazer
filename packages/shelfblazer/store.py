"""Record storage: one Markdown file per record in a git repository, plus schemas and counters.

Every write goes through ``Store`` under a cross-process file lock, is permission-checked, validated,
written, and committed to git with the acting user as author. The in-memory ``records`` dict mirrors the files.
"""
import contextlib, datetime, fcntl, glob, json, logging, os, re, shutil, subprocess, threading

from . import builtin, fields as fieldrules, formula, history, ids, perms, schema_edit, uploads
from . import ids as ids_mod                                                  # bulk_apply has a parameter called ids
from .auth import UID_RE, Users                                            # a user ID; Users.collection_visible
from .errors import Conflict, Forbidden, NotFound, StoreError, ValidationError
from .fields import FIELD_DISPLAYS, FIELD_VALIDATORS, check_dna, normalize_dois
from .recordfile import parse_record, read_json, serialize_record, unix_newlines, version_of, write_text

# Optional "color" in a type definition (config/types/<slug>.yaml): one of these names. Names, not hex values, so every
# colour is a vetted palette entry that keeps text readable in day and night mode. Order = palette slot 1..8.
TYPE_COLORS = ("blue", "green", "orange", "pink", "purple", "teal", "red", "gold")
# Optional "icon" in a type definition: one of these glyphs (Font Awesome Free, in the app's static/icons.svg). Default: box.
TYPE_ICONS = ("box", "bacterium", "dna", "bullseye", "flask", "table-cells", "layer-group", "file-lines")
# Bulk edit: actions, and the fields they may touch.
BULK_ACTIONS = ("set", "add", "remove", "owner", "archive", "restore")
BULK_ADMIN_ACTIONS = ("owner",)
log = logging.getLogger("shelfblazer")
# Attached files are stored with Git LFS: the history keeps small pointers,
# the file contents live in .git/lfs and go to the backup remote's LFS storage. Records, schemas and counters stay plain text.
MAX_PLACES = 2000                       # storage places that can be added from record forms
LFS_PATTERNS = ("data/files/**",)
LFS_ATTRIBUTES = "".join(f"{p} filter=lfs diff=lfs merge=lfs -text\n" for p in LFS_PATTERNS)


# Pseudo-account for maintenance commands (drop-field, lfs-setup): not a person.
MAINTENANCE = {"username": "maintenance", "uid": "maintenance", "name": "ShelfBlazer maintenance", "role": "admin", "active": True}


def author_of(actor):
    """Git author for a person: their user ID only (name and username stay out of git, see docs/design.md 16.2)."""
    uid = actor["uid"]
    return uid, f"{uid}@shelfblazer.invalid"


def lfs_available():
    if not shutil.which("git-lfs"):
        raise StoreError("this data repository stores attached files with Git LFS, but git-lfs is not installed "
                         "(macOS: brew install git-lfs; Debian/Ubuntu: apt install git-lfs)")


# An owner change on a personal record is refused: its links go to its owner's own personal records (design 17.4).
OWNER_KEPT = "a personal record keeps its owner (its links are to the owner's own records): move it to the lab collection first"


def now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


# ----------------------------------------------------------------------------- store
class Store:
    def __init__(self, repo, users_lookup=None):
        self.repo = os.path.abspath(repo)
        self.users_lookup = users_lookup or (lambda key: None)  # user ID or username -> user dict (with "uid") or None
        self._lock_path = os.path.join(self.repo, ".git", "shelfblazer.lock")
        self._tlock = threading.RLock()
        self._lock_depth = 0
        self.on_change = []  # callbacks(record_id or None)
        self.ensure_lfs()
        self.reload()

    # ---- changes made outside this process (maintenance commands, a restore, a manual commit)
    def _head_signature(self):
        """Cheap fingerprint of the current commit: HEAD and the file of the branch it points to (git replaces that file on every
        commit, so its inode and time change). No subprocess: this runs on every request."""
        g = os.path.join(self.repo, ".git")
        try:
            with open(os.path.join(g, "HEAD"), encoding="utf-8") as fh:
                head = fh.read().strip()
        except OSError:
            return None
        sig = [head]
        if head.startswith("ref: "):
            for p in (os.path.join(g, head[5:]), os.path.join(g, "packed-refs")):
                try:
                    st = os.stat(p)
                    sig.append((st.st_ino, st.st_mtime_ns, st.st_size))
                except OSError:
                    sig.append(None)
        return tuple(sig)

    def refresh(self):
        """Reload everything when the repository has a commit this process did not make (returns True then). Called before
        every write (under the lock) and before every page, so a running server never writes an outdated record back."""
        sig = self._head_signature()
        if sig == self._seen_head:
            return False
        with self._tlock:
            if self._head_signature() == self._seen_head:
                return False
            log.info("data repository changed outside this process: reloading")
            self.reload()
        self._notify(None)
        return True

    # ---- loading
    def reload(self):
        self._seen_head = self._head_signature()
        self.schemas = {}
        for p in sorted(glob.glob(os.path.join(self.repo, "config", "types", "*.yaml"))):
            self.schemas[os.path.basename(p)[:-5]] = fieldrules.upgrade_schema(read_json(p))
        self._index_prefixes()
        self._check_schemas()
        op = os.path.join(self.repo, "config", "type_order.yaml")
        self.type_order = [s for s in (read_json(op) if os.path.exists(op) else []) if isinstance(s, str)]
        pp = os.path.join(self.repo, "config", "storage_places.yaml")
        self.places = read_json(pp) if os.path.exists(pp) else []
        self.records = {}
        self._backrefs = self._formers = None
        for p in glob.glob(os.path.join(self.repo, "data", "*", "*.md")):
            self._load_file(p)

    def _index_prefixes(self):
        """prefix_type: ID prefix -> type; former_prefix: a prefix a type used to have ("former_prefixes" in its definition) -> type."""
        self.prefix_type = {s["prefix"]: slug for slug, s in self.schemas.items()}
        self.former_prefix = {p: slug for slug, s in self.schemas.items() for p in s.get("former_prefixes", [])}

    def canonical_id(self, rid):
        """The current ID for an ID written with a former prefix of its type (ST-12 after ST became BS: BS-12), or for the personal
        key a record had before it moved to the lab collection (ST-P17~U7F3K2QA, kept in its "former_ids"); any other text unchanged."""
        p = ids.parse(rid)
        if not p:
            return rid
        if p.prefix in self.former_prefix:
            rid = ids.make(self.schemas[self.former_prefix[p.prefix]]["prefix"], p.number, p.personal, p.owner)
        return self._former_index().get(ids.make(*ids.parse(rid)), rid)

    def resolve(self, text, owner=None):
        """The key of the record an ID names, as written by or for the person ``owner`` (a user ID): a short personal ID (``ST-P3``)
        means that person's own; otherwise as ``canonical_id``. Text that is not an ID is returned as it is."""
        p = ids.parse(text)
        if not p:
            return text
        return self.canonical_id(ids.make(p.prefix, p.number, p.personal, p.owner or (owner if p.personal else None)))

    def _former_index(self):
        """Former ID -> current ID, from the records' "former_ids" (rebuilt after every change, like the reverse links)."""
        if getattr(self, "_formers", None) is None:
            self._formers = {old: r["id"] for r in self.records.values() for old in r.get("former_ids", [])}
        return self._formers

    def _rel_paths(self, rid, type_slug):
        """The repository paths a record's history lives under: its file now, and under each former prefix of its type."""
        p = ids.parse(rid)
        rels = [os.path.relpath(self.path_of(rid, type_slug), self.repo)]
        for old in self.schemas[type_slug].get("former_prefixes", []):
            if p:
                rels.append(os.path.relpath(self.path_of(ids.make(old, p.number, p.personal, p.owner), type_slug), self.repo))
        rec = self.records.get(rid) or {}
        rels += [os.path.relpath(self.path_of(old, type_slug), self.repo) for old in rec.get("former_ids", [])]       # before a move
        return rels

    def _load_file(self, path):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        rec = parse_record(text, path)
        rec["_version"] = version_of(text)
        self.records[rec["id"]] = rec
        return rec

    def _check_schemas(self):
        """Collect warnings about type definitions (unknown colour, bad formula, ...). Pages keep working; the bad setting is ignored."""
        self.schema_warnings = []
        for slug, sch in self.schemas.items():
            keys = [f["key"] for f in sch.get("fields", [])]
            for f in sch.get("fields", []):
                if f.get("type") == "computed":
                    problem = formula.validate(f.get("expr"), keys)
                    if problem:
                        msg = f"type '{slug}', field '{f['key']}': {problem}"
                        self.schema_warnings.append(msg)
                        log.warning(msg)
                if "validate" in f and f["validate"] not in FIELD_VALIDATORS:
                    msg = f"type '{slug}', field '{f['key']}': unknown validate {f['validate']!r} ignored (use one of: {', '.join(FIELD_VALIDATORS)})"
                    self.schema_warnings.append(msg)
                    log.warning(msg)
                if "display" in f and f["display"] not in FIELD_DISPLAYS:
                    msg = f"type '{slug}', field '{f['key']}': unknown display {f['display']!r} ignored (use one of: {', '.join(FIELD_DISPLAYS)})"
                    self.schema_warnings.append(msg)
                    log.warning(msg)
            if "icon" in sch and sch["icon"] not in TYPE_ICONS:
                msg = f"type '{slug}': unknown icon {sch['icon']!r} ignored (use one of: {', '.join(TYPE_ICONS)})"
                self.schema_warnings.append(msg)
                log.warning(msg)
            if "color" in sch and sch["color"] not in TYPE_COLORS:
                msg = f"type '{slug}': unknown color {sch['color']!r} ignored (use one of: {', '.join(TYPE_COLORS)})"
                self.schema_warnings.append(msg)
                log.warning(msg)

    def type_icon(self, slug):
        """The icon of a type: its "icon" setting, else the first instance's default (builtin.py), else a box."""
        icon = (self.schemas.get(slug) or {}).get("icon")
        return icon if icon in TYPE_ICONS else builtin.ICONS.get(slug, "box")

    def type_color(self, slug):
        """The palette colour name of a type ("color" setting, else builtin.py), or None for a colour derived from its name."""
        color = (self.schemas.get(slug) or {}).get("color")
        return color if color in TYPE_COLORS else builtin.COLORS.get(slug)

    def order_of(self, type_slug):
        return [f["key"] for f in self.schemas[type_slug]["fields"]]

    def path_of(self, rid, type_slug):
        return os.path.join(self.repo, "data", type_slug, rid + ".md")

    # ---- locking + git
    @contextlib.contextmanager
    def _write_lock(self):
        """The cross-process write lock. On entry, catch up with commits made elsewhere (so a write never starts from an outdated
        copy); on exit, remember the commit this process made. Re-entrant within one process."""
        with self._tlock:
            if self._lock_depth:
                self._lock_depth += 1
                try:
                    yield
                finally:
                    self._lock_depth -= 1
                return
            os.makedirs(os.path.dirname(self._lock_path), exist_ok=True)
            with open(self._lock_path, "w") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                self._lock_depth = 1
                try:
                    if self._head_signature() != self._seen_head:
                        log.info("data repository changed outside this process: reloading before the write")
                        self.reload()
                        self._notify(None)
                    yield
                finally:
                    self._seen_head = self._head_signature()
                    self._lock_depth = 0
                    fcntl.flock(fh, fcntl.LOCK_UN)

    def run_git(self, *args, author=None):
        """Run git in the data repository: the CompletedProcess (author: (name, email) for commits; the committer is the app)."""
        name, email = author or ("ShelfBlazer", "shelfblazer@localhost")
        env = dict(os.environ, GIT_COMMITTER_NAME="ShelfBlazer", GIT_COMMITTER_EMAIL="shelfblazer@localhost",
                   GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email)
        return subprocess.run(["git", "-C", self.repo, *args], capture_output=True, text=True, env=env)

    def git(self, *args, author=None, check=True):
        """git's output; a failure raises StoreError unless check=False."""
        r = self.run_git(*args, author=author)
        if check and r.returncode != 0:
            raise StoreError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
        return r.stdout

    def commit(self, paths, message, actor, body=None):
        self.git("add", "--", *paths)
        if not self.git("status", "--porcelain", "--", *paths).strip():
            return
        args = ["commit", "-q", "-m", message] + (["-m", body] if body else [])
        self.git(*args, author=author_of(actor))

    # ---- Git LFS
    def uses_lfs(self):
        p = os.path.join(self.repo, ".gitattributes")
        if not os.path.exists(p):
            return False
        with open(p, encoding="utf-8") as fh:
            return "filter=lfs" in fh.read()

    def ensure_lfs(self):
        """A repository that stores files with LFS must have git-lfs, or files would be committed whole (or saves would fail):
        refuse to start without it, and switch the LFS filter on for this repository if it is not on yet."""
        if not self.uses_lfs():
            return
        lfs_available()
        if not self.git("config", "--get", "filter.lfs.process", check=False).strip():
            self.git("lfs", "install", "--local")

    def enable_lfs(self, rewrite_history=False):
        from . import maintenance
        return maintenance.enable_lfs(self, rewrite_history)

    def recover(self):
        """Startup check: commit anything left uncommitted by a crash, clear a stale index lock."""
        with self._write_lock():
            lock = os.path.join(self.repo, ".git", "index.lock")
            if os.path.exists(lock):
                os.remove(lock)
            if self.git("status", "--porcelain").strip():
                self.git("add", "-A")
                self.git("commit", "-q", "-m", "recover: uncommitted changes found at startup")

    # ---- counters
    def _counters_path(self):
        return os.path.join(self.repo, "config", "counters.yaml")

    def _read_counters(self):
        p = self._counters_path()
        return read_json(p) if os.path.exists(p) else {}

    @staticmethod
    def _counter_key(type_slug, personal, owner):
        return type_slug + (f".P.{owner}" if personal else "")

    def peek_numbers(self, type_slug, count=1, personal=False, owner=None):
        """The numbers the next ``count`` records of a type would get: in the lab collection, or in the personal collection of
        ``owner`` (each person counts their own, from 1); nothing is written."""
        if personal and not owner:
            raise ValueError("a personal collection belongs to someone")
        counters = self._read_counters()
        used = [r["legacy_id"] for r in self.records.values() if r["type"] == type_slug and ids.is_personal(r["id"]) == personal
                and (not personal or r["created_by"] == owner)]
        n = max(counters.get(self._counter_key(type_slug, personal, owner), 1), (max(used) + 1) if used else 1)
        return list(range(n, n + count))

    def _next_number(self, type_slug, personal=False, owner=None):
        """The next number of a type's lab collection, or of one person's personal collection (counter "<type>.P.<user ID>")."""
        counters = self._read_counters()
        key = self._counter_key(type_slug, personal, owner)
        n = self.peek_numbers(type_slug, 1, personal, owner)[0]
        counters[key] = n + 1
        with open(self._counters_path(), "w") as fh:
            json.dump(counters, fh, indent=2)
            fh.write("\n")
        return n

    # ---- validation
    def clean_fields(self, type_slug, raw, existing=None, viewer=None, subject=None):
        """raw: dict key -> str | list[str] | bool from a form. Returns (fields, errors).

        With ``viewer``, a link may only point to records that person may see (links already stored are kept as they are).
        ``subject`` is the record being saved, as ``subject_of`` gives it (None: a lab record): it decides which personal records
        it may link to."""
        errors, out = {}, dict(existing or {})
        for f in self.schemas[type_slug]["fields"]:
            k, ty = f["key"], f["type"]
            if not fieldrules.is_value(f):
                continue
            if k not in raw:
                continue
            v = raw[k]
            try:
                val = self._coerce(f, v, type_slug, viewer, (existing or {}).get(k), subject)
            except ValueError as e:
                errors[k] = str(e)
                continue
            if val in (None, "", [], False) and not (ty == "bool" and k in out):
                out.pop(k, None)
            else:
                out[k] = val
        return out, errors

    def _coerce(self, f, v, type_slug, viewer=None, current=None, subject=None):
        ty = f["type"]
        if ty in ("text", "longtext"):
            v = unix_newlines(v).strip()
            if f.get("validate") == "dna":
                check_dna(v)
            elif f.get("validate") == "doi":
                v = normalize_dois(v)
            return v
        if ty == "number":
            v = (v or "").strip()
            if not v:
                return None
            try:
                x = float(v)
            except ValueError:
                raise ValueError("not a number")
            return int(x) if x == int(x) else x
        if ty == "date":
            v = (v or "").strip()
            if v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                raise ValueError("use YYYY-MM-DD")
            return v
        if ty == "url":
            v = (v or "").strip()
            if v and not re.match(r"https?://", v):
                raise ValueError("must start with http:// or https://")
            return v
        if ty == "select":
            v = (v or "").strip()
            if v == "__new__":
                raise ValueError("type the name of the new " + ("place" if f.get("source") == "storage_places" else "option"))
            opts = self.options_for(type_slug, f)
            if v and opts and v not in opts:
                raise ValueError("not one of the allowed options")
            return v
        if ty == "multiselect":
            vals = [x for x in (v if isinstance(v, list) else [v]) if x]
            opts = self.options_for(type_slug, f)
            if opts and any(x not in opts for x in vals):
                raise ValueError("not one of the allowed options")
            return vals
        if ty == "bool":
            return bool(v)
        if ty == "user":
            v = (v or "").strip()
            if not v:
                return v
            u = self.users_lookup(v)
            if not u:
                raise ValueError("unknown user")
            return u["uid"]                                   # people are stored by user ID, never by name
        if ty == "ref":
            personal, owner = subject or (False, None)
            targets = [self.resolve(x.strip(), owner if personal else None)         # a short personal ID is the record owner's own
                       for x in re.split(r"[,\s]+", v if isinstance(v, str) else " ".join(v)) if x.strip()]
            for i in targets:
                r = self.records.get(i)
                if r and viewer is not None and i not in (current or []) and not self.can_see(viewer, r):
                    r = None                                      # a hidden record (hidden type, or archived by someone else) is as good as missing
                if not r:
                    raise ValueError(f"{i} does not exist")
                if f.get("to") not in (None, "*") and r["type"] != f["to"]:
                    raise ValueError(f"{i} is not a {f['to']}")
                if ids.is_personal(i) and i not in (current or []):
                    if not personal:
                        raise ValueError(f"{i} is in a personal collection: a lab record cannot link to it")
                    if r["created_by"] != owner:
                        raise ValueError(f"{i} is in another person's collection")
            return list(dict.fromkeys(targets))
        return v

    def ordered_slugs(self):
        """Type slugs in menu order: the saved order (config/type_order.yaml), then types not in it (alphabetical; the first
        instance's types in their old order, see builtin.py)."""
        first = [s for s in self.type_order if s in self.schemas]
        rest = sorted((s for s in self.schemas if s not in first), key=lambda s: (builtin.ORDER.index(s) if s in builtin.ORDER else 99, s))
        return first + rest

    def move_type(self, slug, direction, actor):
        """Move a type one place up or down in the menu (admins only; one commit)."""
        if not perms.is_admin(actor):
            raise Forbidden("only administrators may change the order of types")
        if direction not in ("up", "down"):
            raise ValidationError({"order": "direction must be up or down"})
        with self._write_lock():
            if slug not in self.schemas:
                raise NotFound(slug)
            order = self.ordered_slugs()
            i = order.index(slug)
            j = i - 1 if direction == "up" else i + 1
            if 0 <= j < len(order):
                order[i], order[j] = order[j], order[i]
            path = os.path.join(self.repo, "config", "type_order.yaml")
            write_text(path, json.dumps(order, indent=2) + "\n")
            self.commit([path], f"types: move {slug} {direction}", actor)
            self.type_order = order
        self._notify(None)

    def places_in_use(self):
        """Storage places that at least one record uses (a place nobody uses any more simply stops being offered), sorted by name."""
        keys = {s: [f["key"] for f in sch["fields"] if f.get("source") == "storage_places"] for s, sch in self.schemas.items()}
        used = {r["fields"].get(k) for r in self.records.values() for k in keys.get(r["type"], [])}
        return sorted((p for p in self.places if p["id"] in used), key=lambda p: p["name"].casefold())

    MAX_OPTIONS = 100                       # options of one choice field that can be added from record forms

    def _stage_additions(self, type_slug, raw, additions):
        """New choice options and storage places typed in a record form (``additions``: field key -> typed name), applied to ``raw``
        and to copies of the type definition and the place list. Nothing is written: the caller saves them in the same commit as the
        record, and only when the record itself is valid. Returns (raw, schema or None, places or None, notes, errors)."""
        raw, sch, places, notes, errors = dict(raw), None, None, [], {}
        for key, typed in (additions or {}).items():
            f = next((x for x in (sch or self.schemas[type_slug])["fields"] if x["key"] == key), None)
            name = " ".join((typed or "").split())
            if f is None or not name:
                continue
            is_place = f.get("source") == "storage_places"
            if not is_place and not (f["type"] in ("select", "multiselect") and f.get("addable")):
                continue                                                           # not a field that takes new values
            if f["type"] == "select" and raw.get(key) != "__new__":
                continue                                                           # "New …" not chosen: the typed name is ignored
            what = "place" if is_place else "option"
            if len(name) > 60:
                errors[key] = f"give the new {what} a name of at most 60 characters"
                continue
            if is_place:
                current = places if places is not None else self.places
                value = next((p["id"] for p in current if p["name"].casefold() == name.casefold()), None)
                if value is None:
                    if len(current) >= MAX_PLACES:
                        errors[key] = f"there are already {MAX_PLACES} storage places; ask an administrator"
                        continue
                    base = schema_edit.slugify(name) or "place"
                    ids, value, n = {p["id"] for p in current}, base, 2
                    while value in ids:
                        value, n = f"{base}-{n}", n + 1
                    places = current + [{"id": value, "name": name}]
                    notes.append(f"storage place: add {name}")
            else:
                options = list(f.get("options") or [])
                value = next((o for o in options if o.casefold() == name.casefold()), None)
                if value is None:
                    if len(options) >= self.MAX_OPTIONS:
                        errors[key] = f"this field already has {self.MAX_OPTIONS} options"
                        continue
                    value = name
                    base = sch or self.schemas[type_slug]
                    sch = dict(base, fields=[dict(x, options=options + [name]) if x["key"] == key else x for x in base["fields"]])
                    notes.append(f"type {type_slug}: add option '{name}' to {key}")
            if f["type"] == "multiselect":
                raw[key] = list(dict.fromkeys([x for x in (raw.get(key) or []) if x] + [value]))
            else:
                raw[key] = value
        return raw, sch, places, notes, errors

    @contextlib.contextmanager
    def _with_additions(self, type_slug, raw, additions):
        """Make staged options and places visible for validation; put the old ones back unless the block finishes.
        Yields (raw, errors, write) where write() saves the changed files and returns their paths plus the notes for the commit."""
        raw, sch, places, notes, errors = self._stage_additions(type_slug, raw, additions)
        old = (self.schemas[type_slug], self.places)
        if sch is not None:
            self.schemas[type_slug] = sch
        if places is not None:
            self.places = places

        def write():
            paths = []
            if sch is not None:
                paths.append(os.path.join(self.repo, "config", "types", type_slug + ".yaml"))
                write_text(paths[-1], json.dumps(sch, indent=2, ensure_ascii=False) + "\n")
            if places is not None:
                paths.append(os.path.join(self.repo, "config", "storage_places.yaml"))
                os.makedirs(os.path.dirname(paths[-1]), exist_ok=True)
                write_text(paths[-1], json.dumps(places, indent=2, ensure_ascii=False) + "\n")
            return paths, notes
        try:
            yield raw, errors, write
        except BaseException:
            self.schemas[type_slug], self.places = old
            raise

    def add_place(self, name, actor):
        """The ID of the storage place with this name (case-insensitive), adding it to config/storage_places.yaml (one commit) if it is new.
        (Record forms add places together with the record instead: see ``additions`` in create and update.)"""
        if not perms.can_read(actor):
            raise Forbidden("not allowed")
        name = " ".join((name or "").split())
        if not name or len(name) > 60:
            raise ValidationError({"place": "give the new place a name of at most 60 characters"})
        with self._write_lock():
            for p in self.places:
                if p["name"].casefold() == name.casefold():
                    return p["id"]
            if len(self.places) >= MAX_PLACES:
                raise ValidationError({"place": f"there are already {MAX_PLACES} storage places; ask an administrator"})
            base = schema_edit.slugify(name) or "place"
            ids, pid, n = {p["id"] for p in self.places}, base, 2
            while pid in ids:
                pid, n = f"{base}-{n}", n + 1
            places = self.places + [{"id": pid, "name": name}]
            path = os.path.join(self.repo, "config", "storage_places.yaml")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            write_text(path, json.dumps(places, indent=2, ensure_ascii=False) + "\n")
            self.commit([path], f"storage place: add {name}", actor)
            self.places = places
        return pid

    def options_for(self, type_slug, f):
        if f.get("source") == "storage_places":
            return [p["id"] for p in self.places]
        return f.get("options") or []

    # ---- operations
    def _check_name(self, name):
        if not (name or "").strip():
            raise ValidationError({"name": "required"})

    @staticmethod
    def subject_of(rec):
        """What decides which personal records a record may link to: (is it personal, its owner)."""
        return (ids.is_personal(rec["id"]), rec["created_by"])

    def create(self, type_slug, actor, name, raw_fields, notes="", additions=None, personal=False):
        """A new record (one commit), in the lab collection or, with ``personal``, in the actor's own collection.
        ``additions`` (field key -> typed name): new choice options and storage places from the form, saved in the same commit, and
        only if the record is valid."""
        if type_slug not in self.schemas:
            raise NotFound(type_slug)
        if not perms.can_read(actor):
            raise Forbidden("not allowed to create records")
        if not perms.can_read_type(actor, self.schemas[type_slug]):
            raise NotFound(type_slug)
        if personal and not perms.can_create_personal(actor, self.schemas[type_slug]):
            raise Forbidden(f"{self.schemas[type_slug]['name']} records cannot be kept in a personal collection")
        if not personal and not perms.can_create(actor, self.schemas[type_slug]):
            raise Forbidden(f"only administrators may add {self.schemas[type_slug]['name'].lower()} records")
        with self._write_lock(), self._with_additions(type_slug, raw_fields, additions) as (raw_fields, add_errors, write_additions):
            fields, errors = self.clean_fields(type_slug, raw_fields, viewer=actor, subject=(personal, actor["uid"]))
            errors.update(add_errors)
            if not (name or "").strip():
                errors["name"] = "required"
            for f in self.schemas[type_slug]["fields"]:
                if f.get("required") and f["key"] != "name" and f["key"] not in fields and not f.get("hidden"):
                    errors[f["key"]] = "required"
            if errors:
                raise ValidationError(errors)
            extra, added = write_additions()
            n = self._next_number(type_slug, personal, actor["uid"])
            rid = ids.make(self.schemas[type_slug]["prefix"], n, personal, actor["uid"])
            t = now_iso()
            rec = dict(id=rid, type=type_slug, name=unix_newlines(name).strip(), created_by=actor["uid"], created_at=t,
                       updated_at=t, archived=False, legacy_id=n, fields=fields, notes=unix_newlines(notes).strip())
            path = self.path_of(rid, type_slug)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            text = serialize_record(rec, self.order_of(type_slug))
            write_text(path, text)
            self.commit([path, self._counters_path(), *extra], f"{rid}: create by {actor['uid']}", actor, body="\n".join(added) or None)
            rec["_version"] = version_of(text)
            self.records[rid] = rec
        self._notify(rid)
        return rid

    def person_name(self, uid):
        """Display name for a user ID stored in a record ("former member" when the account was erased or never existed)."""
        if not uid:
            return ""
        u = self.users_lookup(uid)
        if u:
            return u["name"]
        return "former member" if UID_RE.match(uid) else uid     # an erased person; other authors (the importer) as they are

    def readable(self, viewer, rid):
        """The record, or None when it does not exist or this viewer may not see its type (so a hidden record looks like a missing one)."""
        rec = self.records.get(rid)
        return rec if rec is not None and self.can_see(viewer, rec) else None

    def can_see(self, viewer, rec):
        """Whether this viewer may see this record: ``perms.can_see_record``, the one rule every read path goes through."""
        def shared():                       # an owner without an account (erased): their choice is gone, so hidden (administrators only)
            owner = self.users_lookup(rec["created_by"])
            return owner is not None and Users.collection_visible(owner, rec["type"])
        return perms.can_see_record(viewer, rec, self.schemas.get(rec["type"]), shared=shared)

    def can_edit(self, actor, rec):
        return perms.can_edit(actor, rec, self.schemas.get(rec["type"]))

    def can_create(self, actor, type_slug):
        return type_slug in self.schemas and perms.can_create(actor, self.schemas[type_slug])

    def readable_types(self, viewer):
        return {s for s, sch in self.schemas.items() if perms.can_read_type(viewer, sch)}

    def visible_records(self, viewer):
        """The records this viewer may see (``can_see``): the whole mapping (no copy) for administrators, who see everything."""
        if perms.is_admin(viewer):
            return self.records
        return {k: r for k, r in self.records.items() if self.can_see(viewer, r)}

    def _get_checked(self, rid, actor):
        rec = self.readable(actor, rid)
        if not rec:
            raise NotFound(rid)
        if not self.can_edit(actor, rec):
            raise Forbidden(f"{actor['username']} may not modify {rid}")
        return rec

    def _rewrite(self, rec, actor, message, extra_paths=(), body=None):
        rec["updated_at"] = now_iso()
        path = self.path_of(rec["id"], rec["type"])
        text = serialize_record(rec, self.order_of(rec["type"]))
        write_text(path, text)
        self.commit([path, *extra_paths], message, actor, body=body)
        rec["_version"] = version_of(text)

    def update(self, rid, actor, name, raw_fields, notes, expected_version, owner=None, additions=None):
        """Save a record. ``owner`` (a user ID or username) hands the record to someone else in the same commit: administrators only.
        ``additions``: new options and places from the form, as in ``create``."""
        with self._write_lock():
            cur = self._get_checked(rid, actor)
            if expected_version != cur["_version"]:
                raise Conflict(f"{rid} was modified by someone else since you opened it")
            if owner and not perms.is_admin(actor):
                raise Forbidden("only administrators may change the owner")
            with self._with_additions(cur["type"], raw_fields, additions) as (raw_fields, add_errors, write_additions):
                self._update(rid, cur, actor, name, raw_fields, notes, owner, add_errors, write_additions)
        self._notify(rid)

    def _update(self, rid, cur, actor, name, raw_fields, notes, owner, add_errors, write_additions):
        fields, errors = self.clean_fields(cur["type"], raw_fields, existing=cur["fields"], viewer=actor, subject=self.subject_of(cur))
        errors.update(add_errors)
        if not (name or "").strip():
            errors["name"] = "required"
        new_owner = None
        if owner:
            u = self.users_lookup(owner)
            if not u:
                errors["owner"] = "unknown user"
            elif u["uid"] != cur["created_by"]:
                if ids.is_personal(rid):
                    errors["owner"] = OWNER_KEPT
                else:
                    new_owner = u["uid"]
        if errors:
            raise ValidationError(errors)
        new = dict(cur, name=unix_newlines(name).strip(), fields=fields, notes=unix_newlines(notes).strip())
        if new_owner:
            new["created_by"] = new_owner
        extra, added = write_additions()
        self._rewrite(new, actor, f"{rid}: update by {actor['uid']}" + (f"; owner {cur['created_by']} -> {new_owner}" if new_owner else ""),
                      extra_paths=extra, body="\n".join(added) or None)
        self.records[rid] = new

    def set_archived(self, rid, actor, flag):
        with self._write_lock():
            cur = self._get_checked(rid, actor)
            if bool(cur["archived"]) == bool(flag):
                return
            new = dict(cur, archived=bool(flag))
            self._rewrite(new, actor, f"{rid}: {'archive' if flag else 'restore'} by {actor['uid']}")
            self.records[rid] = new
        self._notify(rid)

    def set_field(self, rid, actor, key, raw, message):
        """Change one field of one record as one commit with its own message (the scan page adds a barcode this way).
        Same checks as a form save: the record must exist, the actor may edit it, the value must be valid. Returns False when
        nothing changes."""
        with self._write_lock():
            cur = self._get_checked(rid, actor)
            fields, errors = self.clean_fields(cur["type"], {key: raw}, existing=cur["fields"], viewer=actor, subject=self.subject_of(cur))
            if errors:
                raise ValidationError(errors)
            if fields == cur["fields"]:
                return False
            new = dict(cur, fields=fields)
            self._rewrite(new, actor, message)
            self.records[rid] = new
        self._notify(rid)
        return True

    # ---- type definitions (admin)
    def used_options(self, type_slug, key):
        """Values of a choice field that existing records use (they cannot be removed from the list)."""
        used = set()
        for r in self.records.values():
            if r["type"] == type_slug:
                v = r["fields"].get(key)
                used.update(v if isinstance(v, list) else [v] if v else [])
        return used

    def save_schema(self, slug, schema, actor, message=None, create=False):
        """Validate and write one type definition (config/types/<slug>.yaml) as one git commit. Admins only."""
        if not perms.is_admin(actor):
            raise Forbidden("only administrators may change type definitions")
        with self._write_lock():
            old = self.schemas.get(slug)
            if create and old is not None:
                raise ValidationError({"type": f"there is already a type with the file name '{slug}'"})
            if not create and old is None:
                raise NotFound(slug)
            problems = schema_edit.validate_schema(slug, schema, self.schemas, old=old)
            if problems:
                raise ValidationError({"type": "; ".join(problems)})
            path = os.path.join(self.repo, "config", "types", slug + ".yaml")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            write_text(path, json.dumps(schema, indent=2, ensure_ascii=False) + "\n")
            self.commit([path], message or f"type {slug}: change definition", actor)
            self.schemas[slug] = schema
            self._index_prefixes()
            self._check_schemas()
        self._notify(None)

    def delete_type(self, slug, actor):
        """Remove a type that has no records and that no other type links to."""
        if not perms.is_admin(actor):
            raise Forbidden("only administrators may remove types")
        with self._write_lock():
            if slug not in self.schemas:
                raise NotFound(slug)
            n = sum(1 for r in self.records.values() if r["type"] == slug)
            if n:
                raise ValidationError({"type": f"{n} record(s) still use this type (archived ones count); records are never deleted, only archived"})
            users = [s for s, v in self.schemas.items() if s != slug and any(f.get("type") == "ref" and f.get("to") == slug for f in v["fields"])]
            if users:
                raise ValidationError({"type": f"the type {', '.join(users)} links to it"})
            path = os.path.join(self.repo, "config", "types", slug + ".yaml")
            os.remove(path)
            self.commit([path], f"type {slug}: remove (no records)", actor)
            del self.schemas[slug]
            self._index_prefixes()
            self._check_schemas()
            d = os.path.join(self.repo, "data", slug)
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
        self._notify(None)

    # ---- attachments
    def files_dir(self, rid):
        return os.path.join(self.repo, "data", "files", rid)

    def file_fields(self, type_slug):
        """Keys of the type's File fields (the only places where files can be attached)."""
        return [f["key"] for f in self.schemas[type_slug]["fields"] if f["type"] == "file"]

    def _file_field(self, rec, field):
        keys = self.file_fields(rec["type"])
        field = field or (keys[0] if keys else None)
        if field not in keys:
            raise ValidationError({"file": "this type has no file field"})
        return field

    def add_attachment(self, rid, actor, filename, content, field=None):
        """Store an uploaded file with the record (data/files/<ID>/<name>) and list it in the record: one git commit.

        Raises uploads.UploadError (as ValidationError) for oversized, empty or program/script files. Returns the stored name.
        """
        name = uploads.safe_name(filename)
        try:
            uploads.check(name, len(content))
        except uploads.UploadError as e:
            raise ValidationError({"file": f"{name}: {e}"})
        with self._write_lock():
            rec = self._get_checked(rid, actor)
            field = self._file_field(rec, field)
            current = list(rec["fields"].get(field, []))
            if len(current) >= uploads.MAX_FILES_PER_RECORD:
                raise ValidationError({"file": f"a record can hold at most {uploads.MAX_FILES_PER_RECORD} files"})
            folder = self.files_dir(rid)
            taken = (os.listdir(folder) if os.path.isdir(folder) else []) + [os.path.basename(a) for k in self.file_fields(rec["type"]) for a in rec["fields"].get(k, [])]
            name = uploads.unique_name(name, taken)
            path = os.path.realpath(os.path.join(folder, name))
            if os.path.dirname(path) != os.path.realpath(folder):          # cannot happen with safe_name, but never trust a path
                raise ValidationError({"file": "bad file name"})
            os.makedirs(folder, exist_ok=True)
            tmp = path + ".part"
            with open(tmp, "wb") as fh:
                fh.write(content)
            os.replace(tmp, path)
            fields = dict(rec["fields"], **{field: current + [f"{rid}/{name}"]})
            if fields.get("file_missing"):                                   # a document whose file was missing now has one
                fields.pop("file_missing")
            new = dict(rec, fields=fields)
            rpath = self.path_of(rid, rec["type"])
            new["updated_at"] = now_iso()
            text = serialize_record(new, self.order_of(rec["type"]))
            write_text(rpath, text)
            new["_version"] = version_of(text)
            self.commit([rpath, path], f"{rid}: attach {name} by {actor['uid']}", actor)
            self.records[rid] = new
        self._notify(rid)
        return name

    def remove_attachment(self, rid, actor, name, field=None):
        """Delete one attached file and its entry in the record (one git commit; the file stays in git history)."""
        with self._write_lock():
            rec = self._get_checked(rid, actor)
            field = self._file_field(rec, field)
            current = list(rec["fields"].get(field, []))
            entry = next((a for a in current if os.path.basename(a) == name), None)
            if entry is None:
                raise ValidationError({"file": "no such attachment"})
            path = os.path.realpath(os.path.join(self.repo, "data", "files", entry))
            base = os.path.realpath(self.files_dir(rid))
            fields = {k: v for k, v in rec["fields"].items() if k != field}
            rest = [a for a in current if a != entry]
            if rest:
                fields[field] = rest
            new = dict(rec, fields=fields, updated_at=now_iso())
            rpath = self.path_of(rid, rec["type"])
            text = serialize_record(new, self.order_of(rec["type"]))
            write_text(rpath, text)
            new["_version"] = version_of(text)
            paths = [rpath]
            if os.path.dirname(path) == base and os.path.exists(path):
                os.remove(path)
                paths.append(path)
            self.commit(paths, f"{rid}: remove file {name} by {actor['uid']}", actor)
            self.records[rid] = new
        self._notify(rid)

    # ---- bulk edit
    def bulk_fields(self, type_slug):
        """Field definitions that a bulk edit may set: everything except the title, legacy keeper and files."""
        return [f for f in self.schemas[type_slug]["fields"] if fieldrules.is_value(f)]

    def bulk_apply(self, actor, type_slug, ids, action, field=None, value=None, dry_run=False):
        """Apply one change to many records of one type, as ONE git commit.

        Records the actor may not edit, records of another type, unknown IDs and records where the change would be invalid are
        skipped and reported (nothing half-applied). ``dry_run`` computes the same report without writing.
        Returns dict(changed, unchanged, skipped=[(id, reason)], errors={id: message}, sha, description).
        """
        if type_slug not in self.schemas or not perms.can_read_type(actor, self.schemas[type_slug]):
            raise NotFound(type_slug)
        if action not in BULK_ACTIONS:
            raise ValidationError({"action": "unknown action"})
        if action in BULK_ADMIN_ACTIONS and not perms.is_admin(actor):
            raise Forbidden(f"only administrators may use '{action}'")
        fdef = None
        if action in ("set", "add", "remove"):
            fdef = next((f for f in self.bulk_fields(type_slug) if f["key"] == field), None)
            if fdef is None:
                raise ValidationError({"field": "choose a field that can be edited in bulk"})
            if action != "set" and not (fdef["type"] == "multiselect" or fdef["type"] == "ref"):
                raise ValidationError({"field": "only multiselect and reference fields have options to add or remove"})
        if action == "owner":
            u = self.users_lookup(value) if isinstance(value, str) else None
            if not u:
                raise ValidationError({"owner": "unknown user"})
            value = u["uid"]
        ids = list(dict.fromkeys(ids))
        kind = (ids_mod.is_personal(ids[0]), ids_mod.owner_of(ids[0])) if ids else (False, None)   # a bulk edit stays within one collection
        result = dict(changed=[], unchanged=[], skipped=[], errors={}, sha=None, action=action)
        desc = {"set": f"set {field}", "add": f"add to {field}", "remove": f"remove from {field}", "owner": f"owner to {value}",
                "archive": "archive", "restore": "restore"}[action]
        result["description"] = desc
        lock = contextlib.nullcontext() if dry_run else self._write_lock()
        with lock:
            plan = []
            for rid in ids:
                rec = self.records.get(rid)
                if rec is None:
                    result["skipped"].append((rid, "does not exist"))
                    continue
                if rec["type"] != type_slug:
                    result["skipped"].append((rid, f"is not a {self.schemas[type_slug]['name'].lower()}"))
                    continue
                if (ids_mod.is_personal(rid), ids_mod.owner_of(rid)) != kind:
                    result["skipped"].append((rid, "is in a different collection"))
                    continue
                if not self.can_edit(actor, rec):
                    result["skipped"].append((rid, "not yours" if perms.setting(self.schemas[type_slug], "edit") == "owner" else "not allowed"))
                    continue
                try:
                    new = self._bulk_one(rec, action, fdef, value, actor)
                except ValidationError as e:
                    result["errors"][rid] = "; ".join(f"{k}: {v}" for k, v in e.errors.items())
                    continue
                if new is None:
                    result["unchanged"].append(rid)
                else:
                    plan.append((rid, new))
                    result["changed"].append(rid)
            if dry_run or not plan:
                return result
            paths = []
            order = self.order_of(type_slug)
            for rid, new in plan:
                path = self.path_of(rid, type_slug)
                paths.append(path)
                text = serialize_record(new, order)
                write_text(path, text)
                new["_version"] = version_of(text)
            self.commit(paths, f"bulk: {desc} ({len(plan)} {type_slug} records)", actor, body="Records: " + ", ".join(r for r, _ in plan))
            result["sha"] = self.git("rev-parse", "HEAD").strip()
            for rid, new in plan:
                self.records[rid] = new
        for rid, _ in plan:
            self._notify(rid)
        return result

    def _bulk_one(self, rec, action, fdef, value, actor=None):
        """The changed record, or None when nothing would change."""
        if action in ("set", "add", "remove"):
            key = fdef["key"]
            if action == "set":
                raw = value
            else:
                current = list(rec["fields"].get(key, []))
                item = (value or "").strip()
                if not item:
                    raise ValidationError({key: "choose a value"})
                if action == "add":
                    raw = current + ([item] if item not in current else [])
                else:
                    raw = [x for x in current if x != item]
            fields, errors = self.clean_fields(rec["type"], {key: raw}, existing=rec["fields"], viewer=actor, subject=self.subject_of(rec))
            if errors:
                raise ValidationError(errors)
            if fields == rec["fields"]:
                return None
            return dict(rec, fields=fields, updated_at=now_iso())
        if action == "owner":
            if rec["created_by"] == value:
                return None
            if ids.is_personal(rec["id"]):
                raise ValidationError({"owner": OWNER_KEPT})
            return dict(rec, created_by=value, updated_at=now_iso())
        if action in ("archive", "restore"):
            flag = action == "archive"
            return None if bool(rec["archived"]) == flag else dict(rec, archived=flag, updated_at=now_iso())

    def import_table(self, actor, type_slug, header, rows, mapping, dry_run=False, personal=False):
        """Import records from a parsed CSV/TSV file (see tableimport) into one type, as ONE git commit that ``undo`` can revert,
        into the actor's personal collection (``personal``, where the type allows it) or the lab collection (where the actor may create).
        Rows that name an ID update that record, if the actor may change it. Every row is checked first, under the write lock; if any
        row has an error nothing is written.
        Returns the plan (tableimport.plan) plus ``sha`` (the commit, or None when nothing was written) and ``written`` (the IDs)."""
        from . import tableimport
        if type_slug not in self.schemas:
            raise NotFound(type_slug)
        if not perms.can_create_type_records(actor, self.schemas[type_slug], personal):
            if personal:
                raise Forbidden(f"{self.schemas[type_slug]['name']} records cannot be kept in a personal collection")
            raise Forbidden(f"only administrators may add {self.schemas[type_slug]['name'].lower()} records")
        with (contextlib.nullcontext() if dry_run else self._write_lock()):
            result = tableimport.plan(self, actor, type_slug, header, rows, mapping, personal)
            result.update(sha=None, written=[])
            todo = [e for e in result["entries"] if e["action"] in ("create", "update")]
            if dry_run or result["errors"] or not todo:
                return result
            t, order, paths, staged = now_iso(), self.order_of(type_slug), [], []
            for e in todo:
                if e["action"] == "create":
                    n = self._next_number(type_slug, personal, actor["uid"])
                    rid = ids.make(self.schemas[type_slug]["prefix"], n, personal, actor["uid"])
                    rec = dict(id=rid, type=type_slug, name=unix_newlines(e["name"]).strip(), created_by=actor["uid"], created_at=t,
                               updated_at=t, archived=False, legacy_id=n, fields=e["fields"], notes=unix_newlines(e["notes"]).strip())
                else:
                    rid = e["rid"]
                    rec = dict(self.records[rid], name=unix_newlines(e["name"]).strip(), fields=e["fields"],
                               notes=unix_newlines(e["notes"]).strip(), updated_at=t)
                e["rid"] = rid
                path = self.path_of(rid, type_slug)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                text = serialize_record(rec, order)
                write_text(path, text)
                rec["_version"] = version_of(text)
                paths.append(path)
                staged.append((rid, rec))
            if result["created"]:
                paths.append(self._counters_path())
            self.commit(paths, f"bulk: import {result['created']} new, {result['updated']} updated ({len(staged)} {type_slug} records)", actor,
                        body="Records: " + ", ".join(r for r, _ in staged))
            result["sha"] = self.git("rev-parse", "HEAD").strip()
            result["written"] = [r for r, _ in staged]
            for rid, rec in staged:
                self.records[rid] = rec
        for rid, _ in staged:
            self._notify(rid)
        return result

    def undo(self, actor, sha):
        """Revert a bulk commit (one git revert). Allowed for the person who made it and for admins; refused if later changes
        touch the same records (nothing is changed then) or if it was already undone."""
        if not re.fullmatch(r"[0-9a-f]{7,40}", sha or ""):
            raise ValidationError({"undo": "bad commit id"})
        with self._write_lock():
            r = self.run_git("log", "-1", "--format=%H%x09%ae%x09%s", sha)
            if r.returncode != 0 or not r.stdout.strip():
                raise NotFound(sha)
            full, email, subject = r.stdout.strip().split("\t", 2)
            if not subject.startswith("bulk:"):
                raise ValidationError({"undo": "only bulk edits can be undone here"})
            if not (perms.is_admin(actor) or email == author_of(actor)[1]):
                raise Forbidden("only the person who made this bulk edit, or an administrator, can undo it")
            if not perms.is_admin(actor):                                   # rights may have changed since: every record must still be theirs to edit
                touched = [os.path.basename(p)[:-3] for p in self.git("diff-tree", "--no-commit-id", "--name-only", "-r", full).split()
                           if p.startswith("data/") and p.endswith(".md")]
                if any(rid in self.records and not self.can_edit(actor, self.records[rid]) for rid in touched):
                    raise Forbidden("some of these records can no longer be changed by you; ask an administrator to undo this bulk edit")
            if self.git("log", "--format=%H", "--fixed-strings", "--grep", f"This reverts commit {full}").strip():
                raise ValidationError({"undo": "this bulk edit was already undone"})
            if self.run_git("revert", "--no-edit", full, author=author_of(actor)).returncode != 0:
                self.run_git("revert", "--abort")
                raise Conflict("later changes touch the same records, so this bulk edit can no longer be undone automatically")
            self.reload()
        self._notify(None)
        return self.git("rev-parse", "HEAD").strip()

    def drop_field(self, type_slug, key):
        from . import maintenance
        return maintenance.drop_field(self, type_slug, key)

    def rename_prefix(self, type_slug, new_prefix, dry_run=False):
        from . import maintenance
        return maintenance.rename_prefix(self, type_slug, new_prefix, dry_run)

    def _notify(self, rid):
        self._backrefs = self._formers = None      # the reverse-link and former-ID indexes are rebuilt on next use
        for cb in self.on_change:
            cb(rid)

    # ---- queries
    def referenced_by(self, rid):
        """[(field label, record)] for every record whose ref field contains rid (from a cached reverse index)."""
        if getattr(self, "_backrefs", None) is None:
            index = {}
            for r in self.records.values():
                for f in self.schemas[r["type"]]["fields"]:
                    if f["type"] == "ref":
                        for target in r["fields"].get(f["key"], []):
                            index.setdefault(target, []).append((f["label"], r))
            for v in index.values():
                v.sort(key=lambda x: (x[1]["type"], ids.sort_key(x[1])))
            self._backrefs = index
        return list(self._backrefs.get(rid, []))

    # ---- computed fields
    def today(self):
        return datetime.date.today()

    def compute(self, rec, fdef, viewer=None):
        """Value of a computed field for one record: (value, error). Never raises."""
        visible = (lambda r: True) if viewer is None else self.visibility(viewer)
        return formula.evaluate(fdef.get("expr", ""), rec, self, visible=visible, today=self.today())

    def visibility(self, viewer):
        """``can_see`` for one viewer, as a predicate (formulas and the search index take it, so neither leaks a hidden record)."""
        return lambda r: self.can_see(viewer, r)

    # ---- history (read-only; see history.py)
    def recent_activity(self, viewer, limit=8):
        return history.recent_activity(self, viewer, limit)

    def history(self, rid, limit=30):
        return history.record_history(self, rid, limit)

    def record_at(self, rid, sha):
        return history.record_at(self, rid, sha)

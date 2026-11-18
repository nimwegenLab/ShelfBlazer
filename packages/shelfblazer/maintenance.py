"""Maintenance operations run from the command line, rarely: start storing attached files with Git LFS, drop a field
from a type and all its records, give a type a new ID prefix. Each is one commit by the maintenance account.
``Store`` exposes them as methods."""
import glob, json, os, re, shutil

from . import schema_edit
from .errors import NotFound, StoreError, ValidationError
from .recordfile import serialize_record, version_of, write_text
from .store import LFS_ATTRIBUTES, LFS_PATTERNS, MAINTENANCE, author_of, lfs_available     # store.py imports this module only when needed


def enable_lfs(store, rewrite_history=False):
    """Start storing attached files with LFS (one commit). Files already in the history stay there in full unless
    ``rewrite_history`` (``git lfs migrate import``: every commit is rewritten, so do it before the first push)."""
    lfs_available()
    with store._write_lock():
        if store.git("status", "--porcelain").strip():
            raise StoreError("the data repository has uncommitted changes; commit them first")
        if rewrite_history:
            store.git("lfs", "install", "--local")
            store.git("lfs", "migrate", "import", "--everything", "--yes", "--include=" + ",".join(LFS_PATTERNS))
            store.git("lfs", "checkout")                              # migrate leaves pointer files on disk: put the contents back
            return
        path = os.path.join(store.repo, ".gitattributes")
        old = ""
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                old = fh.read()
        missing = [l for l in LFS_ATTRIBUTES.splitlines(True) if l not in old]
        if missing:
            write_text(path, old + ("" if not old or old.endswith("\n") else "\n") + "".join(missing))
        store.git("lfs", "install", "--local")
        dirs = [p.split("/**")[0] for p in LFS_PATTERNS if os.path.isdir(os.path.join(store.repo, p.split("/**")[0]))]
        store.git("add", "--", ".gitattributes")
        if dirs:
            store.git("add", "--renormalize", "--", *dirs)            # the files already there become LFS pointers
        store.commit([".gitattributes", *dirs], "store attached files with Git LFS",
                    MAINTENANCE)


def drop_field(store, type_slug, key):
    """Maintenance: remove a field from a type definition and from every record of that type, in one git commit.

    Returns the number of records that carried the field. The record title ("name") cannot be dropped.
    """
    actor = MAINTENANCE
    changed = []
    with store._write_lock():
        sch = store.schemas.get(type_slug)
        if sch is None:
            raise NotFound(type_slug)
        if key == "name":
            raise ValidationError({"field": "the name cannot be removed"})
        if key not in [f["key"] for f in sch["fields"]]:
            raise ValidationError({"field": f"{type_slug} has no field '{key}'"})
        new_schema = dict(sch, fields=[f for f in sch["fields"] if f["key"] != key],
                          list_columns=[c for c in sch.get("list_columns", []) if c != key])
        spath = os.path.join(store.repo, "config", "types", type_slug + ".yaml")
        write_text(spath, json.dumps(new_schema, indent=2, ensure_ascii=False) + "\n")
        paths = [spath]
        new_order = [f["key"] for f in new_schema["fields"]]
        for rid, rec in list(store.records.items()):
            if rec["type"] != type_slug or key not in rec["fields"]:
                continue
            new = dict(rec, fields={k: v for k, v in rec["fields"].items() if k != key})
            path = store.path_of(rid, type_slug)
            text = serialize_record(new, new_order)
            write_text(path, text)
            new["_version"] = version_of(text)
            paths.append(path)
            changed.append((rid, new))
        store.commit(paths, f"{type_slug}: remove field '{key}' ({len(changed)} records)", actor)
        store.schemas[type_slug] = new_schema
        for rid, new in changed:
            store.records[rid] = new
    for rid, _ in changed:
        store._notify(rid)
    return len(changed)


def rename_prefix(store, type_slug, new_prefix, dry_run=False):
    """Maintenance: give a type a new ID prefix (ST-12 becomes BS-12), in one git commit.

    Renames the record files, rewrites the ID inside them, and rewrites every mention of the old IDs in all records
    (links and notes), so nothing is left pointing at the old IDs. The old prefix is kept in the type definition
    ("former_prefixes") so old IDs still open the record and its history still reads across the rename.
    Returns {"old", "new", "renamed", "files": {type: files changed}, "mentions": [(file, count)] outside the type's own files}.
    With dry_run nothing is written.
    """
    with store._write_lock():
        report = _rename_prefix(store, type_slug, new_prefix, dry_run)
    if not dry_run:
        store.reload()
        store._notify(None)
    return report


def _rename_prefix(store, type_slug, new_prefix, dry_run):
    sch = store.schemas.get(type_slug)
    if sch is None:
        raise NotFound(type_slug)
    old, new = sch["prefix"], (new_prefix or "").strip().upper()
    if not schema_edit.PREFIX_RE.match(new):
        raise ValidationError({"prefix": "the ID prefix must be exactly two capital letters"})
    if new == old:
        raise ValidationError({"prefix": f"{type_slug} already uses {old}"})
    if new in store.prefix_type or new in store.former_prefix:
        raise ValidationError({"prefix": f"the prefix {new} is already used by another type"})
    if store.git("status", "--porcelain", "--", "data", "config", check=False).strip():
        raise StoreError("the data folder has uncommitted changes; commit or discard them first")
    pat = re.compile(rf"(?<![A-Za-z0-9]){old}-(\d+)\b")
    changes, mentions, per_type, renamed = [], [], {}, 0
    for path in sorted(glob.glob(os.path.join(store.repo, "data", "*", "*.md"))):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        new_text, n = pat.subn(rf"{new}-\1", text)
        own = os.path.basename(os.path.dirname(path)) == type_slug
        target = os.path.join(os.path.dirname(path), pat.sub(rf"{new}-\1", os.path.basename(path))) if own else path
        if n or target != path:
            changes.append((path, target, new_text))
            slug = os.path.basename(os.path.dirname(path))
            per_type[slug] = per_type.get(slug, 0) + 1
            renamed += target != path
            if not own:
                mentions.append((os.path.relpath(path, store.repo), n))
    dirs = [(d, os.path.join(os.path.dirname(d), pat.sub(rf"{new}-\1", os.path.basename(d))))
            for d in sorted(glob.glob(os.path.join(store.repo, "data", "files", f"{old}-*"))) if os.path.isdir(d)]
    report = dict(old=old, new=new, renamed=renamed, files=per_type, mentions=mentions, folders=len(dirs))
    if dry_run:
        return report
    for path, target, new_text in changes:
        write_text(target, new_text)
        if target != path:
            os.remove(path)
    for src, dst in dirs:
        shutil.move(src, dst)
    spath = os.path.join(store.repo, "config", "types", type_slug + ".yaml")
    new_schema = dict(sch, prefix=new, former_prefixes=[old] + [p for p in sch.get("former_prefixes", []) if p != old])
    write_text(spath, json.dumps(new_schema, indent=2, ensure_ascii=False) + "\n")
    store.git("add", "-A", "--", "data", "config")
    store.git("commit", "-q", "-m", f"{type_slug}: ID prefix {old} -> {new} ({renamed} records)", author=author_of(MAINTENANCE))
    return report

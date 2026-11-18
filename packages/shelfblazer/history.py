"""Reading the git history of the data repository: recent activity for the home page, the history of one record, and a
record as one of its commits saved it. Read-only; ``Store`` exposes these as methods."""
import json, re

from . import fields as fieldrules
from .errors import NotFound
from .recordfile import parse_record

_ACTIVITY = re.compile(r"^([A-Z]{2}-\d+): (create|update|archive|restore|delete) by (\S+)$")
_BULK = re.compile(r"^bulk: (.+) \((\d+) (\S+) records\)$")


def recent_activity(store, viewer, limit=8):
    """The latest changes to records this viewer may see, newest first: dicts with when, who (user ID), action, id, name, type, deleted."""
    out = []
    ok = store.readable_types(viewer)
    lines = store.git("log", "-300", "--format=%aI%x09%s", "--", "data", check=False).splitlines()
    for line in lines:
        when, _, subject = line.partition("\t")
        m = _ACTIVITY.match(subject)
        if m:
            rid, action, who = m.groups()
            rid = store.canonical_id(rid)
            rec = store.records.get(rid)
            slug = rec["type"] if rec else store.prefix_type.get(rid.split("-")[0])
            if slug not in ok or (rec and not store.can_see(viewer, rec)):
                continue
            if out and (out[-1]["who"], out[-1]["id"], out[-1]["action"]) == (who, rid, action):
                continue                                                    # several saves in a row are one line
            out.append(dict(when=when, who=who, action=action, id=rid, name=rec["name"] if rec else "", type=slug, deleted=rec is None))
        else:
            m = _BULK.match(subject)
            if m and m.group(3) in ok:
                out.append(dict(when=when, who=None, action="bulk", id=None, name=f"{m.group(1)} ({m.group(2)} {store.schemas[m.group(3)]['name'].lower()} records)",
                                type=m.group(3), deleted=False))
        if len(out) >= limit:
            break
    return out


def record_history(store, rid, limit=30):
    rec = store.records.get(rid)
    if not rec:
        raise NotFound(rid)
    out = store.git("log", f"-{limit}", "--format=%h%x09%an%x09%ad%x09%s", "--date=short", "--",
                   *store._rel_paths(rid, rec["type"]), check=False)
    return [dict(zip(("sha", "author", "date", "subject"), l.split("\t", 3))) for l in out.splitlines() if l]


def record_at(store, rid, sha):
    """A record as one of its own commits saved it (read-only, no checkout): dict(rec, schema, sha, author, when, subject, current).
    ``current`` is True for the newest commit that touched the record: that version is the record as it is now.
    ``sha`` must be a commit that changed this record's file; anything else is NotFound. The type definition is the one
    from the same commit, so fields dropped or relabelled since still read as they did then."""
    cur = store.records.get(rid)
    if not cur or not re.fullmatch(r"[0-9a-f]{7,40}", sha or ""):
        raise NotFound(rid)
    rels = store._rel_paths(rid, cur["type"])
    row = store.git("log", "-1", "--format=%H%x09%an%x09%aI%x09%s", sha, "--", *rels, check=False).rstrip("\n").split("\t", 3)
    if len(row) != 4 or not row[0].startswith(sha):                 # unknown commit, or one that did not touch this record
        raise NotFound(rid)
    full, author, when, subject = row
    latest = store.git("log", "-1", "--format=%H", "--", *rels, check=False).strip()      # the newest commit that touched this record
    text = ""
    for rel in rels:                                                # the file's name then: its current one, or one under a former prefix
        text = store.git("show", f"{full}:{rel}", check=False)
        if text.startswith("---\n"):
            break
    if not text.startswith("---\n"):                                # the record was deleted in that commit
        raise NotFound(rid)
    schema_text = store.git("show", f"{full}:config/types/{cur['type']}.yaml", check=False)
    try:
        schema = fieldrules.upgrade_schema(json.loads(schema_text))
    except ValueError:
        schema = store.schemas[cur["type"]]
    return dict(rec=parse_record(text, f"{rid}@{sha}"), schema=schema, sha=full, author=author, when=when, subject=subject, current=(full == latest))

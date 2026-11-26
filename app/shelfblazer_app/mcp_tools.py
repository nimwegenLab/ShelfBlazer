"""The tools and resources of the MCP endpoint: what a person's own agent may read and write, as that person, with member rights.

Everything here is a plain function ``tool(env, actor, **arguments) -> dict`` that calls the ``Store`` in this process (same write
lock, same search index, same permission checks as the web pages), so the visibility rule (``Store.can_see``) applies on every read
and a hidden record never appears in an answer. ``actor`` is the token owner with the role forced to ``user`` (see ``member``): even
an administrator's member token has a member's rights and a member's view. The transport is in ``mcp_server``.

``TOOLS`` is the registry: name -> Tool(function, required scope, description). A tool is offered, and runs, only for tokens whose
scope allows it (``allowed``). Left out of the member tools on purpose (docs/design.md, section 18): move to the lab collection,
bulk edit, files, account settings, scan, export.
"""
import dataclasses, functools
from types import SimpleNamespace
from typing import Callable

from mcp.server.mcpserver.exceptions import ResourceError, ToolError

from shelfblazer import checks, fields as fieldrules, ids, perms, tableimport as ti
from shelfblazer.auth import UID_IN_TEXT
from shelfblazer.query import Query, select
from shelfblazer.store import Conflict, Forbidden, NotFound, ValidationError

SCOPES = ("member", "admin")          # a token's scope; an admin token may also use every member tool
PAGE_MAX = 100                         # records in one answer
PLACES_SHOWN = 200
SHOWN_ROWS = 100                       # import rows in a preview (rows with a problem or a change come first)
HISTORY_SHOWN = 10


class ToolFailure(ToolError, ResourceError):
    """A refusal or a mistake the agent can read and act on: the SDK shows its text to the agent (any other exception is a crash,
    whose text stays on the server)."""


def allowed(token_scope, tool_scope):
    return token_scope in SCOPES and SCOPES.index(token_scope) >= SCOPES.index(tool_scope)


def member(user):
    """The token owner as the actor of every tool: a member, whatever their account says, with their commits marked "(via MCP)"."""
    return dict(user, role="user", via="MCP")


@dataclasses.dataclass(frozen=True)
class Tool:
    fn: Callable
    scope: str
    description: str


TOOLS = {}


def tool(scope="member"):
    """Register a function as a tool: its docstring is the description the agent sees, its error types become ToolFailure."""
    def deco(fn):
        @functools.wraps(fn)
        def guarded(env, actor, *a, **kw):
            try:
                return fn(env, actor, *a, **kw)
            except ValidationError as e:
                raise ToolFailure("not valid: " + "; ".join(f"{k}: {v}" for k, v in e.errors.items())) from None
            except NotFound as e:
                raise ToolFailure(f"{e} not found") from None
            except Conflict as e:
                raise ToolFailure(f"conflict: {e}. Read the record again with get_record and redo the change on the new version.") from None
            except Forbidden as e:
                raise ToolFailure(str(e)) from None
            except ti.FileProblem as e:
                raise ToolFailure(str(e)) from None
        TOOLS[fn.__name__] = Tool(guarded, scope, " ".join((fn.__doc__ or "").split()))
        return guarded
    return deco


# ----------------------------------------------------------------------------- helpers
def _type(env, actor, slug):
    """The type's slug, or a refusal that does not tell a hidden type from a missing one."""
    if slug not in env.store.schemas or slug not in env.store.readable_types(actor):
        raise ToolFailure(f"unknown type {slug!r}: call list_types")
    return slug


def _record(env, actor, rid):
    rec = env.store.readable(actor, env.store.canonical_id(str(rid or "").strip().upper()))
    if not rec:
        raise ToolFailure(f"{rid} not found")
    return rec


def _person(env, uid):
    return env.store.person_name(uid)


def _collection(actor, rec):
    return "lab" if not ids.is_personal(rec["id"]) else "mine" if rec["created_by"] == actor["uid"] else "personal (someone else's)"


def _brief(env, actor, rec):
    return {"id": rec["id"], "name": rec["name"], "type": rec["type"], "archived": bool(rec["archived"]),
            "collection": _collection(actor, rec), "owner": _person(env, rec["created_by"])}


def _field(env, f):
    out = {"key": f["key"], "label": f["label"], "type": f["type"], "required": bool(f.get("required"))}
    if f["type"] in ("select", "multiselect"):
        if fieldrules.is_place(f):
            out["options"] = [{"id": p["id"], "name": p["name"]} for p in env.store.places[:PLACES_SHOWN]]
            out["options_note"] = "storage places: write the id" + (f" (first {PLACES_SHOWN} of {len(env.store.places)})" if len(env.store.places) > PLACES_SHOWN else "")
        else:
            out["options"] = list(f.get("options") or [])
    if f["type"] == "ref":
        out["to"] = f.get("to") or "*"
        out["multiple"] = True
        out["note"] = "record IDs, for example a list of them"
    if f["type"] == "user":
        out["note"] = "a username"
    if f["type"] == "date":
        out["note"] = "YYYY-MM-DD"
    if fieldrules.is_sequence(f):
        out["note"] = "nucleotide sequence"
    return out


def describe_type(env, actor, slug):
    s = env.store.schemas[slug]
    return {"slug": slug, "name": s["name"], "prefix": s["prefix"],
            "fields": [{"key": "name", "label": "Name", "type": "text", "required": True}]
                      + [_field(env, f) for f in s["fields"] if fieldrules.is_value(f) and not f.get("legacy")],
            "can_add_to_lab": env.store.can_create(actor, slug),
            "can_add_to_my_collection": perms.can_create_personal(actor, s),
            "has_conventions": bool(checks.guidance(env.store, slug)),
            "conventions_resource": f"conventions://{slug}", "schema_resource": f"schema://{slug}"}


def _raw(env, slug, given):
    """The values a form would send, from what an agent wrote (JSON): (raw, errors)."""
    defs = {f["key"]: f for f in env.store.schemas[slug]["fields"]}
    raw, errors = {}, {}
    for k, v in (given or {}).items():
        f = defs.get(k)
        if f is None or not fieldrules.is_value(f):
            errors[k] = "this type has no such field" if f is None else "this field cannot be set"
        elif f["type"] == "bool":
            raw[k] = v if isinstance(v, bool) else str(v).strip().lower() in ("yes", "true", "1", "on")
        elif f["type"] in ("multiselect", "ref"):
            raw[k] = [str(x) for x in v] if isinstance(v, list) else str(v)
        elif v is None:
            raw[k] = ""
        elif isinstance(v, (dict, list)):
            errors[k] = "write a single value"
        else:
            raw[k] = str(v)
    return raw, errors


def _finding(f):
    d = dataclasses.asdict(f)
    return {k: v for k, v in d.items() if v not in (None, "", [])}


def _review(env, actor, slug, name, fields, notes, rid=None):
    return [_finding(f) for f in checks.review(env.store, actor, slug, dict(name=name, fields=fields, notes=notes), rid=rid)]


def _problems(env, actor, slug, name, raw, notes, rec=None, personal=False):
    """(cleaned fields, errors, findings) for a record about to be saved: the form's validation plus ``checks.review``. Writes nothing."""
    store = env.store
    raw, errors = _raw(env, slug, raw)
    fields, errs = store.clean_fields(slug, raw, existing=rec["fields"] if rec else None, viewer=actor,
                                      subject=store.subject_of(rec) if rec else (personal, actor["uid"]))
    errors.update(errs)
    if not (name or "").strip():
        errors["name"] = "required"
    if rec is None:
        for f in store.schemas[slug]["fields"]:
            if f.get("required") and f["key"] != "name" and f["key"] not in fields and not f.get("hidden") and f["key"] not in errors:
                errors[f["key"]] = "required"
    return fields, errors, (_review(env, actor, slug, name, fields, notes, rec["id"] if rec else None) if not errors else [])


def _new_findings(before, after):
    """The findings of ``after`` that ``before`` did not have: on an edit only what the change brings is reported."""
    key = lambda f: (f["kind"], f.get("field"), f["message"])
    seen = {key(f) for f in before}
    return [f for f in after if key(f) not in seen]


def _blocking(findings):
    return [f for f in findings if f["kind"] == "duplicate"]


# ----------------------------------------------------------------------------- read tools
@tool()
def list_types(env, actor):
    """The record types you may read, with their fields (key, label, type, required, options) and whether you may add records to the
    lab collection or to your own. Call this first. The field keys are what create_record and update_record take."""
    return {"types": [describe_type(env, actor, s) for s in env.store.ordered_slugs() if s in env.store.readable_types(actor)]}


RECORD_KEYS = {"name": "text", "created_at": "date", "updated_at": "date", "owner": "user", "collection": "select", "type": "select"}
GROUPS_SHOWN = 200


def _searchable(env, slug, key):
    """The field definition a condition, a chosen field, a sort or a count may use: a field people see on the record page (never a
    hidden or system one), or one of the record's own keys (``RECORD_KEYS``)."""
    if key in RECORD_KEYS:
        return {"key": key, "type": RECORD_KEYS[key]}
    if not slug:
        raise ToolFailure(f"{key!r}: give a type to use its fields (only {', '.join(RECORD_KEYS)} work across types)")
    f = next((f for f in env.store.schemas[slug]["fields"] if f["key"] == key), None)
    if f is None or not fieldrules.is_shown(f) or f["type"] == "computed":
        raise ToolFailure(f"{key!r}: {slug} has no such field (list_types shows the field keys)")
    return f


def _values(env, actor, rec, f, seen):
    """A record's value of a field as a list of texts (empty list: no value): people and places by name, yes/no, links by ID (only
    records the person may see), files by name."""
    k = f["key"]
    if k in RECORD_KEYS:
        v = {"name": rec["name"], "created_at": rec["created_at"], "updated_at": rec["updated_at"], "owner": _person(env, rec["created_by"]),
             "collection": _collection(actor, rec), "type": rec["type"]}[k]
        return [v] if v else []
    v = rec["fields"].get(k)
    if v in (None, "", []):
        return []
    if f["type"] == "ref":
        return [i for i in v if i in seen]
    if f["type"] == "file":
        return [x.rsplit("/", 1)[-1] for x in v]
    if isinstance(v, list):
        return [fieldrules.as_text(env.store, f, x) for x in v]
    return [fieldrules.as_text(env.store, f, v)]


def _number(text):
    try:
        return float(str(text).replace(",", "."))
    except ValueError:
        return None


def _test(cond, f):
    """One condition (a string) as a test of a value list. 'empty', 'not empty', 'contains:x', 'not contains:x', '>=x', '<=x', '>x', '<x',
    '!=x', or a value (equal, ignoring case). Numbers compare as numbers, dates and the rest as text (YYYY-MM-DD compares correctly)."""
    c = str(cond).strip()
    low = c.lower()
    if low == "empty":
        return lambda vs: not vs
    if low in ("not empty", "notempty", "any"):
        return lambda vs: bool(vs)
    for prefix, neg in (("not contains:", True), ("contains:", False)):
        if low.startswith(prefix):
            part = low[len(prefix):].strip()
            return lambda vs, part=part, neg=neg: neg != any(part in v.lower() for v in vs)
    for op in (">=", "<=", "!=", ">", "<", "="):
        if c.startswith(op):
            arg = c[len(op):].strip()
            break
    else:
        op, arg = "=", c
    if op == "=":
        return lambda vs: any(v.lower() == arg.lower() for v in vs)
    if op == "!=":
        return lambda vs: not any(v.lower() == arg.lower() for v in vs)
    num = f["type"] == "number"
    a = _number(arg) if num else arg
    if num and a is None:
        raise ToolFailure(f"{f['key']}: {arg!r} is not a number")
    cmp = {">=": lambda x: x >= a, "<=": lambda x: x <= a, ">": lambda x: x > a, "<": lambda x: x < a}[op]
    def test(vs):
        xs = [_number(v) for v in vs] if num else vs
        return any(x is not None and cmp(x) for x in xs)
    return test


def _sorter(env, actor, slug, sort, seen):
    desc = sort.startswith("-")
    key = sort.lstrip("-+")
    if key in ("created", "updated"):
        key += "_at"
    if key == "id":
        return (lambda r: ids.sort_key(r)), desc
    f = _searchable(env, slug, key)
    if f["type"] == "number":
        big = float("inf")
        return (lambda r: (lambda n: (n is None, n if n is not None else big))(_number((_values(env, actor, r, f, seen) or [""])[0]))), desc
    return (lambda r: ((_values(env, actor, r, f, seen) or [""])[0].lower(), ids.sort_key(r))), desc


@tool()
def search_records(env, actor, type: str = "", text: str = "", collection: str = "lab", owner: str = "", archived: bool = False,
                   where: dict | None = None, fields: list | None = None, sequence: str = "", mismatches: int = 0, sort: str = "",
                   group_by: str = "", limit: int = 25, offset: int = 0):
    """Search records you may see, the way the lists and the search box do, and narrow them by field values. ``type``: a type slug (empty:
    all types). ``text``: words, an ID or a code. ``collection``: 'lab' (default), 'mine' (your personal collection), 'others' (personal
    collections other people share) or 'any'. ``owner``: 'me' or a username. ``archived``: also archived records.
    ``where``: conditions on field keys (needs ``type``; name, created_at, updated_at, owner, collection and type work for any type), all of
    which must hold: {"plasmid": "empty"}, {"plasmid": "not empty"}, {"genotype": "contains:lacZ"}, {"status": "ok"} (equal, ignoring case),
    {"updated_at": ">=2026-11-01"}, {"length": "<5000"}; also '!=x', 'not contains:x', '<=x', '>x', and a list of conditions for one field
    (a date range). Links compare by record ID, people and storage places by name.
    ``fields``: field keys whose values come back with each result. ``sequence``: a nucleotide sequence (6 or more bases) to find in the
    type's sequence field, either strand, with up to ``mismatches`` (at most 5). ``sort``: 'id', 'name', 'created', 'updated' or a field
    key, '-' in front for descending (default: relevance with text, else ID). ``group_by``: a field key (or owner, collection, type):
    instead of records, the number of matching records per value (a record with several values counts for each).
    ``total`` is always the number of matching records. At most 100 results per call; page with ``offset``."""
    store = env.store
    slug = _type(env, actor, type) if type else None
    if collection not in ("lab", "mine", "others", "any"):
        raise ToolFailure("collection: use lab, mine, others or any")
    who = ""
    if owner:
        u = actor if owner == "me" else store.users_lookup(owner)
        if not u:
            raise ToolFailure(f"unknown owner {owner!r}")
        who = u["uid"]
    if sequence.strip():
        if not slug:
            raise ToolFailure("sequence: give a type")
        from shelfblazer.query import sequence_field
        if not sequence_field(store, slug):
            raise ToolFailure(f"{slug} has no sequence search")
        if text.strip():
            raise ToolFailure("give either text or sequence, not both")
    seen = store.visible_records(actor)
    conditions = []
    for key, conds in (where or {}).items():
        f = _searchable(env, slug, key)
        conditions += [(f, _test(c, f)) for c in (conds if isinstance(conds, list) else [conds])]
    chosen = [_searchable(env, slug, k) for k in (fields or [])]
    order = _sorter(env, actor, slug, sort, seen) if sort else None
    grouping = _searchable(env, slug, group_by) if group_by else None
    q = Query(type=slug, text=sequence if sequence.strip() else text, archived=bool(archived), owner=who,
              sequence=bool(sequence.strip()), mismatches=max(0, int(mismatches)),
              collection={"lab": "lab", "mine": "personal", "others": "personal", "any": "any"}[collection])
    recs, _ = select(store, env.index, actor, q)
    if collection == "mine":
        recs = [r for r in recs if r["created_by"] == actor["uid"]]
    elif collection == "others":
        recs = [r for r in recs if r["created_by"] != actor["uid"]]
    recs = [r for r in recs if all(test(_values(env, actor, r, f, seen)) for f, test in conditions)]
    if grouping:
        counts = {}
        for r in recs:
            for v in (_values(env, actor, r, grouping, seen) or ["(empty)"]):
                counts[v] = counts.get(v, 0) + 1
        groups = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
        return {"total": len(recs), "group_by": group_by, "values": len(groups),
                "groups": [{"value": v, "count": n} for v, n in groups[:GROUPS_SHOWN]]}
    if order:
        key, desc = order
        recs.sort(key=key, reverse=desc)
    elif not (text.strip() or sequence.strip()):
        recs.sort(key=ids.sort_key)
    limit = max(1, min(int(limit), PAGE_MAX))
    offset = max(0, int(offset))
    out = []
    for r in recs[offset:offset + limit]:
        b = dict(_brief(env, actor, r), created_at=r["created_at"], updated_at=r["updated_at"])
        if chosen:
            b["fields"] = {f["key"]: (lambda vs: vs if f["type"] in ("multiselect", "ref", "file") else (vs[0] if vs else ""))(_values(env, actor, r, f, seen))
                           for f in chosen}
        out.append(b)
    return {"total": len(recs), "offset": offset, "results": out}


@tool()
def get_record(env, actor, id: str):
    """One record: its fields (by key), notes, links in both directions, the names of attached files (not their contents), a few lines
    of history, and ``version``, which update_record needs. Records you may not see are reported as not found."""
    store = env.store
    rec = _record(env, actor, id)
    schema = store.schemas[rec["type"]]
    seen = store.visible_records(actor)
    fields, files = {}, {}
    for f in schema["fields"]:
        k = f["key"]
        if not fieldrules.is_shown(f) or f["type"] == "computed":
            continue
        v = rec["fields"].get(k)
        if f["type"] == "file":
            if v:
                files[k] = [x.rsplit("/", 1)[-1] for x in v]
        elif v not in (None, "", []) or (f["type"] == "bool" and k in rec["fields"]):
            if f["type"] == "ref":
                v = [{"id": i, "name": seen[i]["name"]} for i in v if i in seen]
            elif f["type"] == "user":
                v = _person(env, v)
            fields[k] = v
    linked = [{"id": r["id"], "name": r["name"], "type": r["type"], "field": label} for label, r in store.referenced_by(rec["id"]) if r["id"] in seen]
    history = [{"commit": h["sha"], "date": h["date"], "by": _person(env, h["author"]),
                "what": UID_IN_TEXT.sub(lambda m: _person(env, m.group(0)), h["subject"])} for h in store.history(rec["id"], HISTORY_SHOWN)]
    return dict(_brief(env, actor, rec), fields=fields, notes=rec["notes"], files=files, version=rec["_version"], linked_from=linked,
                can_edit=store.can_edit(actor, rec), created_at=rec["created_at"], updated_at=rec["updated_at"], history=history)


# ----------------------------------------------------------------------------- write tools
@tool()
def check_record(env, actor, type: str, name: str, fields: dict | None = None, notes: str = "", id: str = "", collection: str = "lab"):
    """Check a record that is not saved yet, and write nothing: the form's validation errors, plus findings (possible duplicates, with
    the IDs of the records they match, suggested fixes, warnings). ``id``: an existing record you are changing (``fields`` then names
    only the fields to change, and ``name`` is its name). Read conventions://<type> first, and resolve or ask the person about each finding."""
    slug = _type(env, actor, type)
    rec = _record(env, actor, id) if id else None
    if rec and rec["type"] != slug:
        raise ToolFailure(f"{rec['id']} is not a {slug}")
    cleaned, errors, findings = _problems(env, actor, slug, name, fields, notes, rec, personal=collection == "mine")
    return {"ok": not errors, "errors": errors, "findings": findings, "fields": cleaned}


def _may_add(env, actor, slug, collection):
    if collection not in ("lab", "mine"):
        raise ToolFailure("collection: use 'lab' or 'mine'")
    s = env.store.schemas[slug]
    if collection == "mine" and not perms.can_create_personal(actor, s):
        raise ToolFailure(f"{s['name']} records cannot be kept in your own collection")
    if collection == "lab" and not env.store.can_create(actor, slug):
        raise ToolFailure(f"only administrators may add {s['name'].lower()} records")


@tool()
def create_record(env, actor, type: str, name: str, fields: dict | None = None, notes: str = "", collection: str = "lab",
                  confirm_duplicates: bool = False):
    """Create a record in the lab collection ('lab') or your own ('mine'). The same check as check_record runs first: with errors nothing is
    written; when it finds a possible duplicate nothing is written either, unless the person has confirmed that this is a new stock and
    you pass confirm_duplicates=true. Returns the new ID (to write on the label) and the findings."""
    slug = _type(env, actor, type)
    _may_add(env, actor, slug, collection)
    cleaned, errors, findings = _problems(env, actor, slug, name, fields, notes, personal=collection == "mine")
    if errors:
        return {"created": False, "errors": errors, "findings": findings}
    if _blocking(findings) and not confirm_duplicates:
        return {"created": False, "needs_confirmation": True, "findings": findings,
                "message": "A possible duplicate exists. Show it to the person; if it is a new stock, call again with confirm_duplicates=true."}
    raw, _ = _raw(env, slug, fields)
    rid = env.store.create(slug, actor, name, raw, notes, personal=collection == "mine")
    return {"created": True, "id": rid, "collection": collection, "findings": findings, "version": env.store.records[rid]["_version"]}


@tool()
def update_record(env, actor, id: str, version: str, fields: dict | None = None, name: str | None = None, notes: str | None = None,
                  confirm_duplicates: bool = False):
    """Change the named fields of a record you may edit (others stay as they are; an empty string clears a field). ``version`` is the one
    get_record returned: if the record was changed since (also in the browser) nothing is written and you get a conflict. Findings are
    reported for what the change adds; a new possible duplicate needs confirm_duplicates=true after the person agrees."""
    store = env.store
    rec = _record(env, actor, id)
    if not store.can_edit(actor, rec):
        raise ToolFailure(f"you may not change {rec['id']}")
    if version != rec["_version"]:
        raise Conflict(f"{rec['id']} was modified by someone else since you read it")
    new_name = rec["name"] if name is None else name
    new_notes = rec["notes"] if notes is None else notes
    cleaned, errors, findings = _problems(env, actor, rec["type"], new_name, fields, new_notes, rec)
    if errors:
        return {"updated": False, "errors": errors, "findings": []}
    findings = _new_findings(_review(env, actor, rec["type"], rec["name"], rec["fields"], rec["notes"], rec["id"]), findings)
    if _blocking(findings) and not confirm_duplicates:
        return {"updated": False, "needs_confirmation": True, "findings": findings,
                "message": "This change makes the record a possible duplicate. Show it to the person; if they agree, call again with confirm_duplicates=true."}
    raw, _ = _raw(env, rec["type"], fields)
    store.update(rec["id"], actor, new_name, raw, new_notes, version)
    return {"updated": True, "id": rec["id"], "version": store.records[rec["id"]]["_version"], "findings": findings}


def _archive(env, actor, id, flag):
    rec = _record(env, actor, id)
    if not env.store.can_edit(actor, rec):
        raise ToolFailure(f"you may not change {rec['id']}")
    env.store.set_archived(rec["id"], actor, flag)
    return {"id": rec["id"], "archived": flag, "note": "archiving only hides the record; restore_record brings it back"}


@tool()
def archive_record(env, actor, id: str):
    """Archive a record you may edit (the physical sample was thrown away, or the entry is no longer used). Nothing is deleted:
    restore_record brings it back."""
    return _archive(env, actor, id, True)


@tool()
def restore_record(env, actor, id: str):
    """Restore an archived record of yours."""
    return _archive(env, actor, id, False)


@tool()
def import_table(env, actor, type: str, text: str, columns: dict | None = None, collection: str = "lab", apply: bool = False):
    """Import many records from tab-, comma- or semicolon-separated text with a header line (the same as the web page). With apply=false
    (default) nothing is written: you get the preview, the column mapping and the problems per line. Show it to the person, then call again
    with apply=true: all rows are written as one commit (undo_import reverts it), or none when any row has an error. ``columns`` maps each
    header title of the text to a field key ('name', 'notes', 'id' to update records, or a key from list_types); left out, titles are matched
    to field labels automatically."""
    store = env.store
    slug = _type(env, actor, type)
    _may_add(env, actor, slug, collection)
    header, rows, _ = ti.parse(text)
    tgts = ti.targets(store, slug)
    mapping = ti.auto_map(header, tgts) if columns is None else [columns.get(h) or None for h in header]
    res = store.import_table(actor, slug, header, rows, mapping, dry_run=not apply, personal=collection == "mine")
    shown = sorted([e for e in res["entries"] if e["errors"] or e["warnings"] or e["action"] in ("create", "update")], key=lambda e: (not e["errors"], e["line"]))
    return {"applied": bool(res["sha"]), "commit": res["sha"], "created": res["created"], "updated": res["updated"], "unchanged": res["unchanged"],
            "errors": res["errors"], "warnings": res["warnings"], "written": res["written"],
            "mapping": {h: k for h, k in zip(header, mapping)}, "ignored_columns": [h for h, k in zip(header, mapping) if not k],
            "targets": {k: label.split(" (")[0] for k, label in tgts},
            "rows": [{"line": e["line"], "action": e["action"], "id": e["rid"], "name": e["name"], "errors": e["errors"], "warnings": e["warnings"],
                      "changes": [list(c) for c in e["changes"]]} for e in shown[:SHOWN_ROWS]],
            "rows_not_shown": max(0, len(shown) - SHOWN_ROWS)}


@tool()
def undo_import(env, actor, commit: str):
    """Undo an import or bulk edit that you made (the ``commit`` import_table returned), as one revert. Refused when later changes touch
    the same records, or it was undone already."""
    return {"undone": True, "commit": env.store.undo(actor, commit)}


# ----------------------------------------------------------------------------- resources
def conventions(env, actor, type):
    """conventions://<type>: how this group writes records of the type (Markdown), or a note that none are recorded."""
    slug = _type(env, actor, type)
    return checks.guidance(env.store, slug) or f"No conventions are recorded for {env.store.schemas[slug]['name'].lower()} records."


def schema(env, actor, type):
    """schema://<type>: the fields of the type, as list_types shows them."""
    return describe_type(env, actor, _type(env, actor, type))


def environment(store, index, users):
    return SimpleNamespace(store=store, index=index, users=users)

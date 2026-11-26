"""Personal collections: moving records from a person's collection to the lab collection (design section 17.5).

A move is planned first (``plan_move``: what moves, what the new IDs will be, what stops it) and then applied in one commit
(``apply_move``). A lab record cannot link to a personal one, so the personal records a moved record links to move with it, and the ones
those link to in turn, unless the person chooses to link to a lab record that looks the same instead (``relink``). Nothing moves back:
only an administrator can undo a move (``undo_move``: the records go back to the person's collection, the lab numbers stay unused).
"""
import json, os, re, shutil

from . import fields as fieldrules, ids, perms, seqsearch
from .errors import Conflict, Forbidden, NotFound, ValidationError
from .maintenance import plan_renames
from .recordfile import parse_record, serialize_record, write_text
from .store import MAINTENANCE, author_of, now_iso


MOVE_SUBJECT = "move to the lab collection by "
TO_PERSONAL_SUBJECT = "maintenance: move to the personal collection of "


def _name_key(name):
    return re.sub(r"[\W_]+", "", (name or "").casefold())


def _sequence(store, rec):
    key = (store.schemas[rec["type"]].get("sequence_search") or "")
    value = rec["fields"].get(key) if key else None
    return seqsearch.clean(value) if isinstance(value, str) and value.strip() else ""


def lookalikes(store, viewer, rec):
    """Lab records of the same type that look like this one: the same name (ignoring case, spaces and punctuation), or the same
    sequence for types with a sequence search field. Active records the viewer may see, by ID."""
    name, seq = _name_key(rec["name"]), _sequence(store, rec)
    out = [r for r in store.records.values()
           if r["type"] == rec["type"] and not ids.is_personal(r["id"]) and not r["archived"] and store.can_see(viewer, r)
           and ((name and _name_key(r["name"]) == name) or (seq and _sequence(store, r) == seq))]
    return sorted(out, key=ids.sort_key)


def personal_links(store, rec):
    """The personal records this record links to (its reference fields), in field order."""
    out = []
    for f in store.schemas[rec["type"]]["fields"]:
        if f["type"] == "ref":
            out += [i for i in rec["fields"].get(f["key"], []) if ids.is_personal(i) and i not in out]
    return out


def plan_move(store, actor, rids, relink=None):
    """What moving these personal records to the lab collection takes, without writing anything.

    ``relink`` maps a linked personal ID to the lab record the moved records should link to instead (one of its ``lookalikes``).
    Returns dict(items, moving, problems, new_ids, counts): ``items`` in order, each dict(rec, asked, via, lookalikes, relink, new_id);
    ``moving`` the IDs that move; ``problems`` [(ID, text)] that stop the move; ``new_ids`` old -> predicted new ID (given for real when
    the move is applied); ``counts`` [(type name, number moving)]."""
    relink = relink or {}
    items, problems, order = {}, [], []

    def add(rid, via):
        rec = store.readable(actor, rid)
        if rec is None:
            problems.append((rid, "does not exist")) if via is None else problems.append((rid, f"linked from {via}, but you cannot see it"))
            return
        if not ids.is_personal(rid):
            if via is None:
                problems.append((rid, "is already in the lab collection"))
            return
        schema = store.schemas[rec["type"]]
        candidates = lookalikes(store, actor, rec)
        chosen = relink.get(rid) if via is not None and relink.get(rid) in [c["id"] for c in candidates] else None
        items[rid] = dict(rec=rec, asked=via is None, via=via, lookalikes=candidates, relink=chosen, new_id=None)
        order.append(rid)
        if chosen:
            return
        if not perms.can_edit(actor, rec, schema):
            problems.append((rid, "only its owner or an administrator can move it"))
        if not perms.can_create(actor, schema):
            problems.append((rid, f"only administrators can add {schema['name'].lower()} records to the lab collection"))
        if rec["archived"]:
            problems.append((rid, "is archived: restore it first" + (", or link to a lab record instead" if via else "")))
        for f in schema["fields"]:
            if f.get("required") and f["key"] != "name" and not f.get("hidden") and fieldrules.is_value(f) and f["key"] not in rec["fields"]:
                problems.append((rid, f"{f['label']} is required in the lab collection"))

    for rid in dict.fromkeys(rids):
        if rid not in items:
            add(rid, None)
    i = 0
    while i < len(order):                                          # follow the links of everything that moves
        rid = order[i]
        if not items[rid]["relink"]:
            for target in personal_links(store, items[rid]["rec"]):
                if target not in items:
                    add(target, rid)
        i += 1
    moving = [r for r in order if not items[r]["relink"]]
    by_type = {}
    for rid in moving:
        by_type.setdefault(items[rid]["rec"]["type"], []).append(rid)
    new_ids = {}
    for slug, group in by_type.items():
        for rid, n in zip(group, store.peek_numbers(slug, len(group))):
            new_ids[rid] = items[rid]["new_id"] = ids.make(store.schemas[slug]["prefix"], n)
    counts = [(store.schemas[s]["name"], len(g)) for s, g in by_type.items()]
    return dict(items=[items[r] for r in order], moving=moving, problems=problems, new_ids=new_ids, counts=counts)


def apply_move(store, actor, rids, relink=None):
    """Move the records (and the personal records they link to) to the lab collection in one commit, as ``plan_move`` describes.
    Returns {old ID: new ID}. Raises ValidationError when the plan has problems."""
    with store._write_lock():
        plan = plan_move(store, actor, rids, relink)
        if plan["problems"]:
            raise ValidationError({rid: text for rid, text in plan["problems"]})
        if not plan["moving"]:
            raise NotFound("nothing to move")
        mapping = {}
        for item in plan["items"]:
            if not item["relink"]:
                rec = item["rec"]
                mapping[rec["id"]] = ids.make(store.schemas[rec["type"]]["prefix"], store._next_number(rec["type"]))
        relinks = {item["rec"]["id"]: item["relink"] for item in plan["items"] if item["relink"]}
        changes, folders, _, _ = plan_renames(store, mapping.get)
        moved_paths = {store.path_of(new, store.records[old]["type"]): old for old, new in mapping.items()}
        t = now_iso()
        for path, target, text in changes:
            old = moved_paths.get(target)
            if old:                                                 # a moved record: its new number, its old ID, links to lab records
                rec = parse_record(text, target)
                rec["legacy_id"] = ids.parse(rec["id"]).number
                rec["former_ids"] = rec.get("former_ids", []) + [old]
                rec["updated_at"] = t
                for f in store.schemas[rec["type"]]["fields"]:
                    if f["type"] == "ref" and f["key"] in rec["fields"]:
                        rec["fields"][f["key"]] = list(dict.fromkeys(relinks.get(i, i) for i in rec["fields"][f["key"]]))
                text = serialize_record(rec, store.order_of(rec["type"]))
            write_text(target, text)
            if target != path:
                os.remove(path)
        for src, dst in folders:
            shutil.move(src, dst)
        store.git("add", "-A", "--", "data", "config")
        body = "\n".join([f"{o} -> {n}" for o, n in mapping.items()] + [f"{o}: linked to {n} instead" for o, n in relinks.items()])
        store.git("commit", "-q", "-m", f"{MOVE_SUBJECT}{actor['uid']}: " + ", ".join(f"{o} -> {n}" for o, n in mapping.items()),
                  "-m", body, author=author_of(actor))
        store.reload()
    store._notify(None)
    return mapping


def undo_move(store, actor, sha):
    """Administrators: put the records of one move back into the person's collection, as one commit that reverts it. The counters are
    not reverted, so the lab numbers the move gave stay unused (an ID may already be written on a tube; D5). Refused when later changes
    touch the same records, or when the move was already undone."""
    if not perms.is_admin(actor):
        raise Forbidden("only an administrator can undo a move to the lab collection")
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha or ""):
        raise ValidationError({"undo": "bad commit id"})
    with store._write_lock():
        r = store.run_git("log", "-1", "--format=%H%x09%s", sha)
        if r.returncode != 0 or not r.stdout.strip():
            raise NotFound(sha)
        full, subject = r.stdout.strip().split("\t", 1)
        if not subject.startswith(MOVE_SUBJECT):
            raise ValidationError({"undo": "this commit is not a move to the lab collection"})
        if store.git("log", "--format=%H", "--fixed-strings", "--grep", f"This reverts commit {full}").strip():
            raise ValidationError({"undo": "this move was already undone"})
        # The move also changed config/counters.yaml, and every later record of the type changes the same line, so a plain revert
        # conflicts there. The counters are not reverted anyway (the numbers stay used up): such a conflict is resolved by keeping
        # today's counters; a conflict anywhere else (in data/) is a real one.
        if store.run_git("revert", "--no-commit", full).returncode != 0:
            unmerged = set(store.git("diff", "--name-only", "--diff-filter=U").split())
            if not unmerged or unmerged - {"config/counters.yaml"}:
                store.run_git("revert", "--abort")
                raise Conflict("later changes touch the same records, so this move can no longer be undone automatically")
        store.git("checkout", "HEAD", "--", "config/counters.yaml")        # keep the numbers used up
        store.git("commit", "-q", "-m", f'Revert "{subject}"', "-m", f"This reverts commit {full}.", author=author_of(actor))
        store.reload()
    store._notify(None)
    return store.git("rev-parse", "HEAD").strip()


# ---- the other way, for administrators only (design D43): lab records into one person's collection, from the command line

def plan_to_personal(store, owner, rids):
    """What moving these lab records into ``owner``'s personal collection takes, without writing anything. ``owner`` is a user dict.

    Only the owner's own records move (an owner change is not part of it), of types that allow personal collections. Nothing that stays
    may link to a record that moves (a lab record cannot link to a personal one, and nobody else's personal record to this person's).
    Returns dict(moving, problems, new_ids, counts) like ``plan_move``."""
    problems, moving = [], []
    for rid in dict.fromkeys(rids):
        rec = store.records.get(rid)
        if rec is None:
            problems.append((rid, "does not exist"))
        elif ids.is_personal(rid):
            problems.append((rid, "is already in a personal collection"))
        elif rec["created_by"] != owner["uid"]:
            problems.append((rid, f"belongs to {store.person_name(rec['created_by'])}, not to {owner['name']}"))
        elif perms.personal_setting(store.schemas[rec["type"]]) == "off":
            problems.append((rid, f"{store.schemas[rec['type']]['name']} records cannot be in personal collections (type settings)"))
        else:
            moving.append(rid)
    staying = set(moving)
    for rid in moving:
        for label, r in store.referenced_by(rid):
            if r["id"] not in staying:
                problems.append((rid, f"{r['id']} links to it ({label}) and does not move"))
    by_type, new_ids = {}, {}
    for rid in moving:
        by_type.setdefault(store.records[rid]["type"], []).append(rid)
    for slug, group in by_type.items():
        for rid, n in zip(group, store.peek_numbers(slug, len(group), personal=True, owner=owner["uid"])):
            new_ids[rid] = ids.make(store.schemas[slug]["prefix"], n, True, owner["uid"])
    counts = [(store.schemas[s]["name"], len(g)) for s, g in by_type.items()]
    return dict(moving=moving, problems=problems, new_ids=new_ids, counts=counts)


def apply_to_personal(store, owner, rids):
    """Move lab records into ``owner``'s personal collection in one commit by the maintenance account, as ``plan_to_personal``
    describes: new personal IDs (``ST-P12``), the lab ID kept in "former_ids" so it still opens the record, files and mentions follow.
    The lab numbers stay unused. Returns {old ID: new ID}; raises ValidationError when the plan has problems."""
    with store._write_lock():
        plan = plan_to_personal(store, owner, rids)
        if plan["problems"]:
            raise ValidationError({rid: text for rid, text in plan["problems"]})
        if not plan["moving"]:
            raise NotFound("nothing to move")
        if store.git("status", "--porcelain", "--", "data", "config", check=False).strip():
            raise Conflict("the data folder has uncommitted changes; commit or discard them first")
        mapping = {rid: ids.make(store.schemas[store.records[rid]["type"]]["prefix"],
                                 store._next_number(store.records[rid]["type"], personal=True, owner=owner["uid"]), True, owner["uid"])
                   for rid in plan["moving"]}
        changes, folders, _, _ = plan_renames(store, mapping.get)
        moved_paths = {store.path_of(new, store.records[old]["type"]): old for old, new in mapping.items()}
        t = now_iso()
        for path, target, text in changes:
            old = moved_paths.get(target)
            if old:
                rec = parse_record(text, target)
                rec["legacy_id"] = ids.parse(rec["id"]).number
                rec["former_ids"] = rec.get("former_ids", []) + [old]
                rec["updated_at"] = t
                text = serialize_record(rec, store.order_of(rec["type"]))
            write_text(target, text)
            if target != path:
                os.remove(path)
        for src, dst in folders:
            shutil.move(src, dst)
        store.git("add", "-A", "--", "data", "config")
        counts = ", ".join(f"{n} {name.lower()}" for name, n in plan["counts"])
        store.git("commit", "-q", "-m", f"{TO_PERSONAL_SUBJECT}{owner['uid']} ({counts})",
                  "-m", "\n".join(f"{o} -> {n}" for o, n in mapping.items()), author=author_of(MAINTENANCE))
        store.reload()
    store._notify(None)
    return mapping

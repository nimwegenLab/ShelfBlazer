"""Import records of one type from a delimited text file (CSV, TSV or semicolon-separated), for anyone who may add records of the type.

Pure functions over a Store, in the order the web pages use them: ``decode`` and ``parse`` read the file, ``targets`` and ``auto_map``
offer the columns of the type, ``plan`` checks every row without writing anything. ``Store.import_table`` runs ``plan`` under the write
lock and, when no row has an error, writes everything as one git commit.

Rules: a row with an ID updates that record (only if the person may change it), a row without one creates a record. A blank cell leaves the field as it is (so a
partly filled column never wipes data). Rows are checked with the same rules as the record form. One bad row stops the whole import."""
import csv, io, re
from collections import Counter

from . import checks, fields as fieldrules, ids, perms

MAX_BYTES = 1_000_000                  # the pages send the file text back and forth in forms, which have a 4 MB limit
MAX_ROWS = 5000
ID, NAME, NOTES = "id", "name", "notes"
YES, NO = {"yes", "y", "true", "1", "x", "ticked"}, {"no", "n", "false", "0", ""}
SHOW_OPTIONS = 8                       # how many allowed options an error message lists


class FileProblem(ValueError):
    """The file or the column choice cannot be used at all (shown to the person as a message, not per row)."""


def decode(data):
    """Text from the uploaded bytes: UTF-8 (a spreadsheet's BOM is dropped), else Windows-1252 as older Excel files are saved."""
    if len(data) > MAX_BYTES:
        raise FileProblem(f"the file is larger than {MAX_BYTES // 1000} kB: split it into several files")
    if b"\0" in data:
        raise FileProblem("this does not look like a text file (save the spreadsheet as CSV or TSV first)")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def detect_delimiter(text):
    """Tab, comma or semicolon: whichever the header line has most of (tab wins a tie, then comma)."""
    first = text.lstrip("\n").split("\n", 1)[0]
    counts = [(first.count(d), -i, d) for i, d in enumerate("\t,;")]
    best = max(counts)
    return best[2] if best[0] else ","


def parse(text, delimiter=None):
    """-> (header, rows, delimiter); rows are (first line number in the file, cells), blank lines dropped. Quoted cells may span lines."""
    delimiter = delimiter or detect_delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    out, previous_end = [], 0
    try:
        for cells in reader:
            line, previous_end = previous_end + 1, reader.line_num
            if any(c.strip() for c in cells):
                out.append((line, cells))
            if len(out) > MAX_ROWS + 1:
                raise FileProblem(f"more than {MAX_ROWS} rows: split the file")
    except csv.Error as e:
        raise FileProblem(f"the file could not be read as {'tab' if delimiter == chr(9) else 'delimited'} text ({e})")
    if not out:
        raise FileProblem("the file is empty")
    header = [h.strip() for h in out[0][1]]
    if len(out) < 2:
        raise FileProblem("the file has a header line but no data rows")
    if len(header) > 100:
        raise FileProblem("more than 100 columns: is this the right file?")
    return header, out[1:], delimiter


def targets(store, slug):
    """[(key, label)] the columns a file can fill: ID (to update), Name, the type's fields people fill in, Notes."""
    out = [(ID, "ID (updates a record)"), (NAME, "Name")]
    out += [(f["key"], f["label"]) for f in store.schemas[slug]["fields"] if fieldrules.is_value(f)]
    return out + [(NOTES, "Notes")]


def template_header(store, slug):
    """The column titles of an empty import file for new records: Name, the fields people fill in (not a legacy free-text one that forms
    no longer offer), Notes. No ID column: a row without one creates a record (an export file is the starting point for updates)."""
    sch = store.schemas[slug]
    return ["Name"] + [f["label"] for f in sch["fields"] if fieldrules.is_value(f) and not f.get("legacy")] + ["Notes"]


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def auto_map(header, tgts):
    """For each header cell the key of the column it names (compared by label or key, ignoring case and punctuation), else None.
    A key is taken by the first column that names it."""
    by_name = {}
    for key, label in tgts:
        for name in (key, label.split(" (")[0]):
            by_name.setdefault(_norm(name), key)
    taken, out = set(), []
    for h in header:
        key = by_name.get(_norm(h))
        out.append(key if key and key not in taken else None)
        taken.add(out[-1])
    return out


def _canonical(store, slug, f, text):
    """A typed option or storage place name as the stored value (case-insensitive); anything else is returned as typed."""
    if f.get("source") == "storage_places":
        by = {p["name"].casefold(): p["id"] for p in store.places} | {p["id"].casefold(): p["id"] for p in store.places}
    else:
        by = {o.casefold(): o for o in store.options_for(slug, f)}
    return by.get(text.casefold(), text)


def _convert(store, slug, f, text):
    """The raw value the record form would send for this cell (a ValueError says what is wrong)."""
    ty = f["type"]
    if ty == "bool":
        t = text.lower()
        if t in YES:
            return True
        if t in NO:
            return False
        raise ValueError("use yes or no")
    if ty == "multiselect":
        whole = _canonical(store, slug, f, text)
        if whole != text or whole in store.options_for(slug, f):
            return [whole]                                          # the whole cell is one option (it may contain a comma)
        return [_canonical(store, slug, f, p.strip()) for p in re.split(r"[;,]", text) if p.strip()]
    if ty == "select":
        return _canonical(store, slug, f, text)
    return text


def _show(v):
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, list):
        return ", ".join(map(str, v))
    return "" if v is None else str(v)


def _allowed(store, slug, f):
    if f.get("source") == "storage_places":
        names = [p["name"] for p in store.places]
    else:
        names = list(store.options_for(slug, f))
    more = f" and {len(names) - SHOW_OPTIONS} more" if len(names) > SHOW_OPTIONS else ""
    return ", ".join(names[:SHOW_OPTIONS]) + more


def _check_row(store, actor, slug, rules, draft, cur, line, file_keys):
    """Warnings (text) from the checks (checks.py) for one row that would be written: against the records the person may see, and against the
    rows before it in the file. For an update only what the change adds is reported."""
    out = []
    found = checks.review(store, actor, slug, draft, cur["id"] if cur else None)
    if cur:
        before = checks.review(store, actor, slug, cur, cur["id"])
        found = [f for f in found if not any(f.same_as(b) for b in before)]
    for f in found:
        if f.kind == "duplicate":
            r = f.records[0]
            out.append(f"possible duplicate of {r['id']} {r['name']}{' (archived)' if r['archived'] else ''}: {f.detail}")
        elif f.kind == "fix":
            out.append(f"{checks.field_label(store, slug, f.field)}: suggested \u201c{f.suggestion}\u201d")
        else:
            out.append(f.message)
    if rules:
        for k in checks.keys_of(draft, rules):
            if k in file_keys and file_keys[k] != line:
                out.append(f"same {rules['keys'][k[0]]['label'].removeprefix('same ')} as line {file_keys[k]}")
            file_keys.setdefault(k, line)
    return out


def plan(store, actor, slug, header, rows, mapping, personal=False):
    """Check every row, write nothing. -> dict(entries, created, updated, unchanged, errors, warnings).
    ``personal``: the rows go into the actor's personal collection (new records only; IDs in the file must be the actor's own personal
    records). An entry: dict(line, action 'create'|'update'|'unchanged'|'error', rid, name, notes, fields, changes [(label, old, new)],
    errors {field: message}, warnings [text])."""
    tgts = dict(targets(store, slug))
    if len(mapping) != len(header):
        raise FileProblem("the column choice does not match the file")
    chosen = [k for k in mapping if k]
    if any(k not in tgts for k in chosen):
        raise FileProblem("a column was assigned to something this type does not have")
    dup = [k for k, n in Counter(chosen).items() if n > 1]
    if dup:
        raise FileProblem("two columns are assigned to the same target: " + ", ".join(tgts[k].split(" (")[0] for k in dup))
    if ID not in chosen and NAME not in chosen:
        raise FileProblem("assign one column to Name (to create records) or to ID (to update them)")
    fdefs = {f["key"]: f for f in store.schemas[slug]["fields"]}
    seen_ids, file_keys, entries = {}, {}, []
    rules = store.check_rules.get(slug)
    for line, cells in rows:
        cell = {k: (cells[i].strip() if i < len(cells) else "") for i, k in enumerate(mapping) if k}
        e = dict(line=line, action=None, rid=None, name="", notes="", fields={}, changes=[], errors={}, warnings=[])
        entries.append(e)
        cur = None
        rid_text = cell.get(ID, "")
        if rid_text:
            rid = store.resolve(rid_text, actor["uid"] if personal else None)
            e["rid"] = rid
            cur = store.readable(actor, rid)                              # a record the person may not see looks like a missing one
            if cur is None:
                e["errors"][ID] = f"{rid_text} does not exist"
            elif cur["type"] != slug:
                e["errors"][ID] = f"{rid} is not a {store.schemas[slug]['name'].lower()}"
            elif ids.is_personal(rid) != personal:
                e["errors"][ID] = f"{rid} is in the {'lab' if personal else 'personal'} collection, not the one you import into"
            elif personal and cur["created_by"] != actor["uid"]:
                e["errors"][ID] = f"{rid} is in someone else's collection"
            elif not store.can_edit(actor, cur):
                e["errors"][ID] = f"you may not change {rid} ({'only an administrator' if perms.setting(store.schemas[slug], 'edit') == 'admin' else 'only its owner or an administrator'} can)"
            elif rid in seen_ids:
                e["errors"][ID] = f"{rid} already appears on line {seen_ids[rid]}"
            else:
                seen_ids[rid] = line
        raw = {}
        for key, text in cell.items():
            f = fdefs.get(key)
            if f is None or key in (ID, NAME, NOTES) or text == "":
                continue
            try:
                raw[key] = _convert(store, slug, f, text)
            except ValueError as err:
                e["errors"][key] = str(err)
        name = cell.get(NAME) or (cur["name"] if cur else "")
        if not name:
            e["errors"][NAME] = "required"
        e["name"] = name
        e["notes"] = cell.get(NOTES) or (cur["notes"] if cur else "")
        fields, errs = store.clean_fields(slug, {k: v for k, v in raw.items() if k not in e["errors"]},
                                          existing=cur["fields"] if cur else None, viewer=actor,
                                          subject=store.subject_of(cur) if cur else (personal, actor["uid"]) if personal else None)
        for k, msg in errs.items():
            f = fdefs.get(k, {})
            e["errors"][k] = msg + (f" ({_allowed(store, slug, f)})" if "allowed options" in msg else "")
        e["fields"] = fields
        if cur is None and not rid_text:
            for f in store.schemas[slug]["fields"]:
                if f.get("required") and f["key"] != "name" and fieldrules.is_value(f) and f["key"] not in fields and f["key"] not in e["errors"]:
                    e["errors"][f["key"]] = "required"
        if e["errors"]:
            e["action"] = "error"
            continue
        if cur is None:
            e["action"] = "create"
            e["changes"] = [(fdefs[k]["label"], "", _show(v)) for k, v in fields.items()]
        else:
            e["changes"] = [(fdefs[k]["label"], _show(cur["fields"].get(k)), _show(fields.get(k))) for k in raw if k in fdefs
                            and cur["fields"].get(k) != fields.get(k)]
            if name != cur["name"]:
                e["changes"].insert(0, ("Name", cur["name"], name))
            if e["notes"] != cur["notes"]:
                e["changes"].append(("Notes", "…", "…"))
            e["action"] = "update" if e["changes"] else "unchanged"
            if cur["archived"]:
                e["warnings"].append("this record is archived")
        if e["action"] in ("create", "update"):
            e["warnings"] += _check_row(store, actor, slug, rules, dict(name=name, fields=fields, notes=e["notes"]), cur, line, file_keys)
    count = Counter(e["action"] for e in entries)
    return dict(entries=entries, created=count["create"], updated=count["update"], unchanged=count["unchanged"], errors=count["error"],
                warnings=sum(1 for e in entries if e["warnings"]))

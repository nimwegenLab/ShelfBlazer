"""Checks on a record before it is saved: possible duplicates (and, later, suggested fixes and convention warnings). They warn, never block.

The code is generic. What a group considers "the same" lives in the data repository, one optional file per type,
``config/checks/<type>.yaml`` (JSON-compatible YAML, like the type definitions)::

    {
      "tables":  {"spelling": [["(?i)\\bdh5alfa\\b", "DH5alpha"], ["[∆𝚫]", "Δ"]]},
      "extract": {"background": {"field": "name", "pattern": "^(?P<value>\\S+)"}},
      "duplicate_keys": [
        {"label": "same background, changes and plasmids",
         "parts": [{"extract": "background", "normalise": ["spelling", "casefold"], "required": true},
                   {"field": "genotype", "normalise": ["spelling"], "set": true},
                   {"field": "plasmid"}]}
      ]
    }

* ``tables``: named lists of ``[regular expression, replacement]``, applied in order (Python ``re`` syntax; ``(?i)`` for case-insensitive).
* Built-in normalisers usable in ``normalise`` besides the tables: ``casefold``, ``accents`` (strip accents), ``space`` (collapse spaces),
  ``alnum`` (drop everything but letters and digits), ``dna`` (bases only; a sequence and its reverse complement compare equal).
* ``extract``: a named part of a field, the ``value`` group of a pattern (no match: empty).
* ``duplicate_keys``: two records are possible duplicates when all the parts of one key are equal. A part is a ``field`` or an ``extract``;
  ``set`` splits it into words (``split``: a pattern, default spaces) compared in any order; a ``ref`` field compares the linked IDs as a set.
  A key is skipped for a record when a ``required`` part is empty, or when fewer than ``min_filled`` parts (default 1) have a value:
  "the same background" alone is not a duplicate if the key also names changes and plasmids.

Conventions (all optional, all warnings or suggestions, never blocking)::

      "fields": {"name":     {"fix": ["spelling"]},
                 "genotype": {"warn": [{"extract": "background", "message": "the background belongs in the name"},
                                       {"ref": "plasmid", "message": "plasmids are linked, not written here"},
                                       {"pattern": "(?i)\\b(into|with)\\b", "message": "looks like prose: use the notes"}],
                              "max_length": 120}},
      "mentions": {"plasmid": {"in": ["name", "notes"], "normalise": ["accents", "alnum", "casefold"],
                               "bracket": "\\[(?P<value>[^\\]]+)\\]", "brackets_in": ["name"]}},
      "agree":    [{"extracts": ["name_background", "genotype_background"], "normalise": ["casefold"],
                    "message": "the name and the genotype name different backgrounds"}]

* ``fields.<key>.fix``: tables applied in order; a different result is offered as a suggested value (with a button on the form).
* ``fields.<key>.warn``: a warning when an extract's value is found in the field, when the name of a record linked in a ref field is, or when
  a pattern matches. ``max_length``: a warning for a longer value.
* ``mentions.<ref field>``: records of the linked type whose names appear in the listed fields (compared token by token after the
  normalisers) but are not linked are reported; ``bracket`` and ``brackets_in`` give the convention of writing the linked record's name
  in brackets in those fields: text outside brackets, text in brackets that matches no linked record, and a bracket spelled differently
  from the record's name (offered as a fix) are reported.
* ``agree``: two extracts that are both present must be equal after the normalisers.

A type without a file gets one key: the name, compared without case, accents and repeated spaces. A file that cannot be read or has a
mistake is reported (``Store.schema_warnings``) and the type falls back to that default.

``review`` is the one entry point: the record form, the table import and the MCP tool ``check_record`` call it.
Only records the viewer may see are compared, so a finding never reveals a hidden record.
"""
import glob, hashlib, json, os, re, unicodedata
from dataclasses import dataclass, field as dc_field


BUILTINS = ("casefold", "accents", "space", "alnum", "dna")
DEFAULT_KEY = {"label": "same name", "parts": [{"field": "name", "normalise": ["accents", "casefold", "space"], "required": True}]}
MIN_DNA = 20                      # shorter sequences are too common to call two records the same
COMPLEMENT = str.maketrans("ACGTRYKMBVDHN", "TGCAYRMKVBHDN")


@dataclass
class Finding:
    """One thing to show before saving. ``kind``: 'duplicate' (``records``: the IDs, each a dict id, name, archived),
    'fix' (``suggestion``: the value to use instead) or 'warning'."""
    kind: str
    message: str
    field: str = ""
    suggestion: str = None
    records: list = dc_field(default_factory=list)
    detail: str = ""                            # a duplicate: what the records share ("same name and same genotype")

    def same_as(self, other):
        return self.signature() == other.signature()

    def signature(self):
        return (self.kind, self.field, self.message, self.suggestion, tuple(r["id"] for r in self.records))


def signature(findings):
    """A short code for a set of findings. The form carries it back when the person chose to save anyway; if the form changed so
    that the findings are different, the code no longer matches and the person is asked again."""
    return hashlib.sha256(json.dumps([f.signature() for f in findings]).encode()).hexdigest()[:16]


class RulesError(ValueError):
    pass


# ----------------------------------------------------------------------------- rules
def load_rules(repo, schemas):
    """{type: compiled rules} for every type, and a list of warnings (one per rules file with a mistake)."""
    out, warnings = {slug: compile_rules({}, schemas[slug]) for slug in schemas}, []
    for path in sorted(glob.glob(os.path.join(repo, "config", "checks", "*.yaml"))):
        slug = os.path.basename(path)[:-5]
        if slug not in schemas:
            warnings.append(f"config/checks/{slug}.yaml: there is no type '{slug}'; the file is ignored")
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            out[slug] = compile_rules(raw, schemas[slug])
        except (OSError, ValueError) as e:
            warnings.append(f"config/checks/{slug}.yaml: {e}; only the name is compared")
    return out, warnings


def compile_rules(raw, schema):
    """Check a rules document against the type and compile its patterns. Raises RulesError (a ValueError) with a readable message."""
    if not isinstance(raw, dict):
        raise RulesError("the file must hold one object")
    fields = {f["key"]: f for f in schema.get("fields", [])}
    tables = {}
    for name, rows in (raw.get("tables") or {}).items():
        if name in BUILTINS:
            raise RulesError(f"table '{name}' has the name of a built-in normaliser")
        if not isinstance(rows, list) or not all(isinstance(r, list) and len(r) == 2 and all(isinstance(x, str) for x in r) for r in rows):
            raise RulesError(f"table '{name}' must be a list of [pattern, replacement] pairs")
        tables[name] = [(_compile(p, f"table '{name}'"), rep) for p, rep in rows]
    extracts = {}
    for name, ex in (raw.get("extract") or {}).items():
        if not isinstance(ex, dict) or ex.get("field") not in fields or not isinstance(ex.get("pattern"), str):
            raise RulesError(f"extract '{name}' needs a field of the type and a pattern")
        rx = _compile(ex["pattern"], f"extract '{name}'")
        if "value" not in rx.groupindex:
            raise RulesError(f"extract '{name}': the pattern needs a group (?P<value>...)")
        extracts[name] = (ex["field"], rx)
    keys = []
    for i, key in enumerate(raw.get("duplicate_keys") or [DEFAULT_KEY], 1):
        parts = key.get("parts") if isinstance(key, dict) else None
        if not parts or not isinstance(parts, list):
            raise RulesError(f"duplicate key {i} needs a list of parts")
        compiled = []
        for part in parts:
            compiled.append(_compile_part(part, i, fields, tables, extracts))
        need = key.get("min_filled", 1)
        if not isinstance(need, int) or not 1 <= need <= len(compiled):
            raise RulesError(f"duplicate key {i}: min_filled must be a number from 1 to {len(compiled)}")
        keys.append({"label": str(key.get("label") or "same values"), "parts": compiled, "min_filled": need})
    return {"tables": tables, "extracts": extracts, "keys": keys, "guidance": str(raw.get("guidance") or ""),
            "field_rules": _compile_field_rules(raw.get("fields") or {}, fields, tables, extracts),
            "mentions": _compile_mentions(raw.get("mentions") or {}, fields, tables, schema),
            "agree": _compile_agree(raw.get("agree") or [], tables, extracts)}


def _check_normalisers(names, where, tables):
    if not isinstance(names, list):
        raise RulesError(f"{where}: the normalisers must be a list")
    for n in names:
        if n not in BUILTINS and n not in tables:
            raise RulesError(f"{where}: unknown normaliser '{n}' (a table name, or one of: {', '.join(BUILTINS)})")
    return list(names)


def _check_field(key, fields, where, allow=("name", "notes")):
    if key not in allow and key not in fields:
        raise RulesError(f"{where}: the type has no field '{key}'")


def _compile_field_rules(raw, fields, tables, extracts):
    out = {}
    for key, rule in raw.items():
        where = f"fields.{key}"
        _check_field(key, fields, where)
        if not isinstance(rule, dict):
            raise RulesError(f"{where} must be an object")
        fix = _check_normalisers(rule.get("fix") or [], f"{where}.fix", tables)
        warn = []
        for w in rule.get("warn") or []:
            if not isinstance(w, dict) or sum(k in w for k in ("pattern", "extract", "ref")) != 1:
                raise RulesError(f"{where}.warn: each entry has one of pattern, extract or ref")
            message = str(w.get("message") or "")
            if "pattern" in w:
                warn.append(dict(kind="pattern", rx=_compile(w["pattern"], f"{where}.warn"), message=message or "this does not look right here"))
            elif "extract" in w:
                if w["extract"] not in extracts:
                    raise RulesError(f"{where}.warn: there is no extract '{w['extract']}'")
                warn.append(dict(kind="extract", name=w["extract"], message=message or "this belongs somewhere else"))
            else:
                if fields.get(w["ref"], {}).get("type") != "ref":
                    raise RulesError(f"{where}.warn: '{w['ref']}' is not a link field")
                warn.append(dict(kind="ref", key=w["ref"], message=message or "linked records are not written here"))
        limit = rule.get("max_length")
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            raise RulesError(f"{where}.max_length must be a number")
        out[key] = dict(fix=fix, warn=warn, max_length=limit)
    return out


def _compile_mentions(raw, fields, tables, schema):
    out = {}
    for key, m in raw.items():
        where = f"mentions.{key}"
        f = fields.get(key) or {}
        if f.get("type") != "ref" or f.get("to") in (None, "*"):
            raise RulesError(f"{where}: '{key}' must be a link field to one type")
        if not isinstance(m, dict):
            raise RulesError(f"{where} must be an object")
        for k in list(m.get("in") or []) + list(m.get("brackets_in") or []):
            _check_field(k, fields, where)
        rx = None
        if m.get("bracket"):
            rx = _compile(m["bracket"], where)
            if "value" not in rx.groupindex:
                raise RulesError(f"{where}: the bracket pattern needs a group (?P<value>...)")
        out[key] = dict(to=f["to"], label=f.get("label", key), fields=list(m.get("in") or []), bracket=rx,
                        brackets_in=list(m.get("brackets_in") or []),
                        normalise=_check_normalisers(m.get("normalise") or ["accents", "casefold"], where, tables))
    return out


def _compile_agree(raw, tables, extracts):
    out = []
    for i, a in enumerate(raw, 1):
        pair = a.get("extracts") if isinstance(a, dict) else None
        if not isinstance(pair, list) or len(pair) != 2 or any(e not in extracts for e in pair):
            raise RulesError(f"agree {i}: needs two extracts of the file")
        out.append(dict(extracts=pair, normalise=_check_normalisers(a.get("normalise") or ["casefold"], f"agree {i}", tables),
                        message=str(a.get("message") or "these two disagree")))
    return out


def _compile_part(part, i, fields, tables, extracts):
    where = f"duplicate key {i}"
    if not isinstance(part, dict) or (("field" in part) == ("extract" in part)):
        raise RulesError(f"{where}: each part names either a field or an extract")
    if "field" in part:
        f = part["field"]
        if f != "name" and f not in fields:
            raise RulesError(f"{where}: the type has no field '{f}'")
        kind = fields.get(f, {}).get("type", "text")
    else:
        if part["extract"] not in extracts:
            raise RulesError(f"{where}: there is no extract '{part['extract']}'")
        kind = "text"
    for n in part.get("normalise") or []:
        if n not in BUILTINS and n not in tables:
            raise RulesError(f"{where}: unknown normaliser '{n}' (a table name, or one of: {', '.join(BUILTINS)})")
    return {"field": part.get("field"), "extract": part.get("extract"), "normalise": list(part.get("normalise") or []),
            "set": bool(part.get("set")) or kind == "ref", "ref": kind == "ref", "required": bool(part.get("required")),
            "split": _compile(part.get("split") or r"\s+", where)}


def _compile(pattern, where):
    try:
        return re.compile(pattern)
    except re.error as e:
        raise RulesError(f"{where}: bad pattern {pattern!r} ({e})") from None


# ----------------------------------------------------------------------------- normalising
def normalise(text, names, tables):
    """``text`` passed through the named normalisers, in order."""
    s = str(text or "")
    for n in names:
        if n == "casefold":
            s = s.casefold()
        elif n == "accents":
            s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
        elif n == "space":
            s = " ".join(s.split())
        elif n == "alnum":
            s = "".join(c for c in s if c.isalnum())
        elif n == "dna":
            s = _dna(s)
        else:
            for rx, rep in tables[n]:
                s = rx.sub(rep, s)
    return s.strip()


def _dna(s):
    bases = re.sub(r"[^A-Za-z]", "", s).upper()
    if len(bases) < MIN_DNA:
        return ""
    rc = bases.translate(COMPLEMENT)[::-1]
    return min(bases, rc)


def _text_of(value):
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return "" if value is None or isinstance(value, bool) else str(value)


def part_value(part, rec, rules):
    """The comparable value of one part for a record (a dict with name and fields): a string, a frozenset, or None when empty."""
    if part["extract"]:
        fkey, rx = rules["extracts"][part["extract"]]
        m = rx.search(_text_of(_get(rec, fkey)))
        raw = m.group("value") if m else ""
    else:
        raw = _get(rec, part["field"])
    if part["ref"]:
        ids = frozenset(v for v in (raw if isinstance(raw, list) else [raw]) if v)
        return ids or None
    text = normalise(_text_of(raw), part["normalise"], rules["tables"])
    if part["set"]:
        words = frozenset(w for w in part["split"].split(text) if w)
        return words or None
    return text or None


def _get(rec, key):
    if key in ("name", "notes"):
        return rec.get(key, "")
    return (rec.get("fields") or {}).get(key)


def keys_of(rec, rules):
    """[(key index, value)] for each duplicate key that applies to the record."""
    out = []
    for i, key in enumerate(rules["keys"]):
        vals = [part_value(p, rec, rules) for p in key["parts"]]
        if sum(v is not None for v in vals) < key["min_filled"] or any(v is None and p["required"] for p, v in zip(key["parts"], vals)):
            continue
        out.append((i, tuple(vals)))
    return out


# ----------------------------------------------------------------------------- the index of existing records
class _Index:
    """Per type: key -> record IDs. Built on first use, dropped for a type when one of its records changes (or for all on a reload)."""
    def __init__(self, store):
        self.store, self.by_type = store, {}
        store.on_change.append(self.changed)

    def changed(self, rid):
        if rid is None:
            self.by_type.clear()
            return
        rec = self.store.records.get(rid)
        if rec is not None:
            self.by_type.pop(rec["type"], None)
        else:                                   # gone (purge): its type is unknown now, so start again
            self.by_type.clear()

    def of(self, slug):
        idx = self.by_type.get(slug)
        if idx is None:
            rules = self.store.check_rules[slug]
            idx = {}
            for rec in self.store.records.values():
                if rec["type"] == slug:
                    for k in keys_of(rec, rules):
                        idx.setdefault(k, []).append(rec["id"])
            self.by_type[slug] = idx
        return idx


def _index(store):
    idx = getattr(store, "_check_index", None)
    if idx is None:
        idx = store._check_index = _Index(store)
    return idx


# ----------------------------------------------------------------------------- review
def review(store, viewer, type_slug, draft, rid=None):
    """[Finding] for a record about to be saved. ``draft``: dict(name, fields, notes), fields as cleaned by ``Store.clean_fields``.
    ``rid``: the record being edited (never its own duplicate). Writes nothing."""
    rules = store.check_rules.get(type_slug)
    if rules is None:
        return []
    found = {}                                  # record ID -> labels of the keys it shares
    idx = _index(store).of(type_slug)
    for k in keys_of(draft, rules):
        for other in idx.get(k, ()):
            if other != rid:
                found.setdefault(other, []).append(rules["keys"][k[0]]["label"])
    out = []
    for other in sorted(found, key=lambda r: store.records[r].get("legacy_id") or 0):
        rec = store.records.get(other)
        if rec is None or not store.can_see(viewer, rec):
            continue
        labels = list(dict.fromkeys(found[other]))
        detail = " and the ".join(labels)
        out.append(Finding("duplicate", f"{other} has the {detail}", records=[{"id": other, "name": rec["name"], "archived": bool(rec.get("archived"))}],
                           detail=detail))
    return out + _conventions(store, viewer, type_slug, draft, rules)


# ----------------------------------------------------------------------------- conventions
SEPARATORS = re.compile(r"[\s\[\]\(\),;:]+")
MAX_WORDS = 4                                   # a record name of up to this many words is found in a text
MIN_NAME = 3                                    # shorter names are too common to look for


def field_label(store, type_slug, key):
    if key == "name":
        return "Name"
    if key == "notes":
        return "Notes"
    return next((f["label"] for f in store.schemas[type_slug]["fields"] if f["key"] == key), key)


def _names(store, viewer, slug, norm, tables):
    """normalised name -> [records] for the records of a type the viewer may see (archived ones left out)."""
    out = {}
    for rec in store.records.values():
        if rec["type"] == slug and not rec.get("archived") and store.can_see(viewer, rec):
            n = normalise(rec["name"], norm, tables)
            if len(n) >= MIN_NAME:
                out.setdefault(n, []).append(rec)
    return out


def _found_in(text, names, norm, tables):
    """IDs of the records whose names appear in ``text`` as whole words (a name of several words as several words in a row)."""
    words, found = [w for w in SEPARATORS.split(text) if w], set()
    for i in range(len(words)):
        for n in range(1, MAX_WORDS + 1):
            if i + n <= len(words):
                key = normalise(" ".join(words[i:i + n]), norm, tables)
                for rec in names.get(key, ()):
                    found.add(rec["id"])
    return found


def _conventions(store, viewer, type_slug, draft, rules):
    out, tables = [], rules["tables"]
    fixed = {}                                  # field -> (text, [reasons]): the suggestion builds up over the steps
    for key, rule in rules["field_rules"].items():
        text = _text_of(_get(draft, key))
        label = field_label(store, type_slug, key)
        if rule["fix"]:
            new = normalise(text, rule["fix"], tables) if text.strip() else text
            if new != text.strip():
                fixed[key] = (new, ["standard spelling"])
        for w in rule["warn"]:
            hit = False
            if w["kind"] == "pattern":
                hit = bool(w["rx"].search(text))
            elif w["kind"] == "extract":
                fkey, rx = rules["extracts"][w["name"]]
                m = rx.search(_text_of(_get(draft, fkey)))
                value = normalise(m.group("value"), ["accents", "casefold", "alnum"], tables) if m else ""
                hit = len(value) >= MIN_NAME and value in normalise(text, ["accents", "casefold", "alnum"], tables)
            else:
                ref = rules["mentions"].get(w["key"]) or dict(to=next(f["to"] for f in store.schemas[type_slug]["fields"] if f["key"] == w["key"]),
                                                              normalise=["accents", "casefold", "alnum"])
                names = _names(store, viewer, ref["to"], ref["normalise"], tables)
                linked = set(draft["fields"].get(w["key"]) or [])
                hit = bool(linked & _found_in(text, names, ref["normalise"], tables))
            if hit:
                out.append(Finding("warning", w["message"], field=key))
        if rule["max_length"] and len(text) > rule["max_length"]:
            out.append(Finding("warning", f"{label} is long ({len(text)} characters): is some of it prose for the notes?", field=key))
    for refkey, m in rules["mentions"].items():
        out += _mentions(store, viewer, type_slug, draft, rules, refkey, m, fixed)
    for key, (text, reasons) in fixed.items():
        out.append(Finding("fix", " and ".join(reasons).capitalize(), field=key, suggestion=text))
    for a in rules["agree"]:
        vals = []
        for e in a["extracts"]:
            fkey, rx = rules["extracts"][e]
            mt = rx.search(_text_of(_get(draft, fkey)))
            vals.append(normalise(mt.group("value"), a["normalise"], tables) if mt else "")
        if all(vals) and vals[0] != vals[1]:
            out.append(Finding("warning", a["message"]))
    return out


def _mentions(store, viewer, type_slug, draft, rules, refkey, m, fixed):
    out, tables, norm = [], rules["tables"], m["normalise"]
    names = _names(store, viewer, m["to"], norm, tables)
    linked = list(draft["fields"].get(refkey) or [])
    linked_names = {normalise(store.records[i]["name"], norm, tables): store.records[i] for i in linked if i in store.records}
    target = store.schemas[m["to"]]["name"].lower()
    seen = set()
    for key in m["fields"]:
        text = _text_of(_get(draft, key))
        inside = [mt.group("value") for mt in m["bracket"].finditer(text)] if m["bracket"] else []
        outside = m["bracket"].sub(" ", text) if m["bracket"] else text
        ids_out, ids_in = _found_in(outside, names, norm, tables), set()
        for v in inside:
            ids_in |= {r["id"] for r in names.get(normalise(v, norm, tables), ())}
        for rid in sorted((ids_out | ids_in) - set(linked) - seen):
            seen.add(rid)
            out.append(Finding("warning", f"{store.records[rid]['name']} ({rid}) is named in {field_label(store, type_slug, key).lower()} but not linked in {m['label']}", field=refkey))
        if key not in m["brackets_in"]:
            continue
        for rid in sorted(ids_out):
            out.append(Finding("warning", f"{store.records[rid]['name']} is written outside brackets in {field_label(store, type_slug, key).lower()}; the convention is [{store.records[rid]['name']}]", field=key))
        base, reasons = fixed.get(key, (text, []))
        start = base
        for mt in list(m["bracket"].finditer(start)):
            v = mt.group("value")
            hits = names.get(normalise(v, norm, tables)) or []
            if not hits and normalise(v, norm, tables) in linked_names:
                hits = [linked_names[normalise(v, norm, tables)]]
            if not hits and not any(normalise(v, norm, tables) == k for k in linked_names):
                out.append(Finding("warning", f"[{v}] is not the name of a known {target}", field=key))
            elif len(hits) == 1 and hits[0]["name"] != v:
                base = base.replace(f"[{v}]", f"[{hits[0]['name']}]", 1)
        if inside and linked_names and not any(normalise(v, norm, tables) in linked_names for v in inside):
            out.append(Finding("warning", f"the {target} in brackets is not the one linked ({', '.join(r['name'] for r in linked_names.values())})", field=key))
        if base != start:
            fixed[key] = (base, reasons + [f"the {target} written as its record's name"])
    return out


def guidance(store, type_slug):
    """The ``guidance`` text of a type's rules file (Markdown for people and agents), or ''."""
    return (store.check_rules.get(type_slug) or {}).get("guidance", "")


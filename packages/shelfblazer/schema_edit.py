"""Editing record-type definitions: pure functions that take a type definition (a dict) and return a changed copy, plus validation.

Nothing here touches files; the store writes the result. Every function raises ``SchemaError`` with a sentence for a person.
Rules (see docs/design.md section 9): adding fields is always allowed; labels can change; removing a field only hides it;
a field's type cannot change (except text <-> long text); the ID prefix is fixed once a type exists; an option can be removed
only while no record uses it.
"""
import copy, re

from . import formula

FIELD_TYPES = ("text", "longtext", "number", "date", "select", "multiselect", "bool", "url", "user", "ref", "file", "computed")
TYPE_LABELS = {"text": "Text (one line)", "longtext": "Text (several lines, Markdown)", "number": "Number", "date": "Date", "select": "Choice (one of a list)",
               "multiselect": "Choices (several of a list)", "bool": "Yes / no", "url": "Web link", "user": "Person (username)",
               "ref": "Link to other records", "file": "File (upload)", "computed": "Calculated (formula)"}
LIST_EXTRA_COLUMNS = ("created_by", "created_at")
RESERVED_KEYS = frozenset({"id", "type", "name", "created_by", "created_at", "updated_at", "archived", "legacy_id", "fields", "notes", "owner", "created",
                           "updated", "attachments", "legacy_owner", "true", "false", "none"})
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
PREFIX_RE = re.compile(r"^[A-Z]{2}$")
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
SYSTEM_KEYS = ("legacy_owner",)           # present in imported types; not editable here


class SchemaError(ValueError):
    pass


def is_system(f):
    """Fields the app manages itself (kept from the old database): not editable on the type page."""
    return f["key"] in SYSTEM_KEYS


def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:31]


def key_from_label(label):
    s = re.sub(r"[^a-z0-9]+", "_", (label or "").lower()).strip("_")
    if not s or not s[0].isalpha():
        s = "f_" + s if s else ""
    return s[:32].rstrip("_")


def _fields(schema):
    return schema.get("fields", [])


def get_field(schema, key):
    f = next((f for f in _fields(schema) if f["key"] == key), None)
    if f is None:
        raise SchemaError(f"there is no field '{key}'")
    return f


def new_type(name, prefix, color=None):
    """A new, empty type: just the title field. The caller validates it against the other types."""
    schema = {"name": (name or "").strip(), "prefix": (prefix or "").strip().upper(),
              "list_columns": ["id", "name", "created_by"],
              "fields": [{"key": "name", "label": "Name", "type": "text", "required": True}]}
    if color:
        schema["color"] = color
    return schema


def add_field(schema, label, ftype, key=None, required=False, options=None, to=None, expr=None):
    schema = copy.deepcopy(schema)
    label = (label or "").strip()
    if not label:
        raise SchemaError("give the field a name")
    if ftype not in FIELD_TYPES:
        raise SchemaError("choose a field type")
    key = (key or key_from_label(label)).strip()
    if not KEY_RE.match(key) or key in RESERVED_KEYS:
        raise SchemaError(f"'{key}' cannot be used as a field key (lowercase letters, digits and _, starting with a letter; not a reserved word)")
    if any(f["key"] == key for f in _fields(schema)):
        raise SchemaError(f"this type already has a field '{key}'")
    f = {"key": key, "label": label, "type": ftype}
    if required and ftype not in ("bool", "computed", "file"):
        f["required"] = True
    if ftype in ("select", "multiselect"):
        f["options"] = clean_options(options)
        if not f["options"]:
            raise SchemaError("a choice field needs at least one option (one per line)")
    if ftype == "ref":
        f["to"] = to or "*"
        f["multiple"] = True
    if ftype == "computed":
        f["expr"] = (expr or "").strip()
    schema["fields"].append(f)
    return schema


def clean_options(options):
    if isinstance(options, str):
        options = options.splitlines()
    out = []
    for o in options or []:
        o = " ".join(str(o).split())
        if o and o not in out:
            out.append(o)
    if any(len(o) > 60 for o in out):
        raise SchemaError("an option is longer than 60 characters")
    if len(out) > 100:
        raise SchemaError("at most 100 options")
    return out


def update_field(schema, key, label=None, required=None, ftype=None, add_options=None, remove_options=None, expr=None, display=None, used_options=()):
    schema = copy.deepcopy(schema)
    f = get_field(schema, key)
    if is_system(f):
        raise SchemaError("this is a system field and cannot be changed here")
    if label is not None:
        label = label.strip()
        if not label:
            raise SchemaError("the label cannot be empty")
        f["label"] = label
    if required is not None and key != "name" and f["type"] not in ("bool", "computed", "file"):
        if required:
            f["required"] = True
        else:
            f.pop("required", None)
    if ftype and ftype != f["type"]:
        if {ftype, f["type"]} != {"text", "longtext"}:
            raise SchemaError("the type of a field cannot be changed (only one-line text and multi-line text can be swapped)")
        f["type"] = ftype
    if add_options or remove_options:
        if f["type"] not in ("select", "multiselect") or f.get("source"):
            raise SchemaError("only choice fields have options")
        current = list(f.get("options", []))
        for o in clean_options(remove_options):
            if o in used_options:
                raise SchemaError(f"'{o}' is used by existing records and cannot be removed")
            if o in current:
                current.remove(o)
        for o in clean_options(add_options):
            if o not in current:
                current.append(o)
        if not current:
            raise SchemaError("a choice field needs at least one option")
        f["options"] = current
    if expr is not None:
        if f["type"] != "computed":
            raise SchemaError("only calculated fields have a formula")
        f["expr"] = expr.strip()
    if display is not None:
        if display not in ("short", "full", "outdated"):
            raise SchemaError("unknown display setting")
        if f["type"] != "text":
            raise SchemaError("only text fields have a display setting")
        if display == "full":
            f.pop("display", None)                                   # as stored: the same as no setting
        else:
            f["display"] = display
    return schema


def move_field(schema, key, direction):
    schema = copy.deepcopy(schema)
    fields = schema["fields"]
    movable = [i for i, f in enumerate(fields) if f["key"] != "name"]      # the title stays first
    idx = next((i for i, f in enumerate(fields) if f["key"] == key), None)
    if idx is None or idx not in movable:
        raise SchemaError("this field cannot be moved")
    pos = movable.index(idx)
    new = pos - 1 if direction == "up" else pos + 1 if direction == "down" else None
    if new is None:
        raise SchemaError("direction must be up or down")
    if 0 <= new < len(movable):
        j = movable[new]
        fields[idx], fields[j] = fields[j], fields[idx]
    return schema


def set_hidden(schema, key, hidden):
    schema = copy.deepcopy(schema)
    f = get_field(schema, key)
    if key == "name" or is_system(f):
        raise SchemaError("this field cannot be hidden")
    if hidden:
        f["hidden"] = True
        schema["list_columns"] = [c for c in schema.get("list_columns", []) if c != key]
    else:
        f.pop("hidden", None)
    return schema


def update_settings(schema, name=None, color=None, list_columns=None, permissions=None, icon=None):
    """color, icon: a palette name or icon name, or '' to go back to the default. list_columns: keys the person ticked (order is normalised)."""
    schema = copy.deepcopy(schema)
    if name is not None:
        if not name.strip():
            raise SchemaError("the type needs a name")
        schema["name"] = name.strip()
    if color is not None:
        if color:
            schema["color"] = color
        else:
            schema.pop("color", None)
    if icon is not None:
        if icon:
            schema["icon"] = icon
        else:
            schema.pop("icon", None)
    if list_columns is not None:
        wanted = set(list_columns)
        visible = [f["key"] for f in _fields(schema) if not f.get("hidden") and f["key"] != "name" and f["key"] not in SYSTEM_KEYS and f["type"] != "file"]
        cols = ["id", "name"] + [k for k in visible if k in wanted] + [c for c in LIST_EXTRA_COLUMNS if c in wanted]
        schema["list_columns"] = cols
    if permissions is not None:
        clean = {k: v for k, v in permissions.items() if v}
        if clean:
            schema["permissions"] = clean
        else:
            schema.pop("permissions", None)
    return schema


def validate_schema(slug, schema, others, old=None):
    """Return a list of problems (empty when fine). `others` maps slug -> schema for the other types; `old` is the stored version."""
    from .store import FIELD_DISPLAYS, FIELD_VALIDATORS, TYPE_COLORS, TYPE_ICONS
    problems = []
    if not SLUG_RE.match(slug or ""):
        problems.append("the type's file name must be 2-31 lowercase letters, digits or hyphens")
    name = (schema.get("name") or "").strip()
    if not name or len(name) > 40:
        problems.append("the type needs a name of at most 40 characters")
    prefix = schema.get("prefix", "")
    if not PREFIX_RE.match(prefix):
        problems.append("the ID prefix must be exactly two capital letters (for example ST)")
    elif any(o.get("prefix") == prefix for s, o in others.items() if s != slug):
        problems.append(f"the prefix {prefix} is already used by another type")
    former = schema.get("former_prefixes", [])
    if not isinstance(former, list) or not all(isinstance(x, str) and PREFIX_RE.match(x) for x in former) or prefix in former:
        problems.append("former_prefixes must be a list of two-letter prefixes this type used to have")
    if old is not None and old.get("prefix") != prefix:
        problems.append("the ID prefix cannot be changed once the type exists")
    if "color" in schema and schema["color"] not in TYPE_COLORS:
        problems.append("unknown colour")
    if "icon" in schema and schema["icon"] not in TYPE_ICONS:
        problems.append("unknown icon")
    perms = schema.get("permissions", {})
    allowed = {"read": ("all", "admin"), "create": ("all", "admin"), "edit": ("owner", "admin", "all")}
    for action, value in perms.items():
        if action not in allowed or value not in allowed[action]:
            problems.append(f"unknown permission setting {action}={value}")
    fields = _fields(schema)
    keys = [f.get("key") for f in fields]
    if not fields or fields[0].get("key") != "name" or fields[0].get("type") != "text":
        problems.append("the first field must be the title field 'name'")
    if len(set(keys)) != len(keys):
        problems.append("two fields have the same key")
    old_keys = {f["key"] for f in _fields(old)} if old else set()
    for f in fields:
        k, t = f.get("key", ""), f.get("type")
        if not KEY_RE.match(k) and k not in old_keys:
            problems.append(f"bad field key '{k}'")
        if k in RESERVED_KEYS and k not in old_keys and k != "name":
            problems.append(f"'{k}' is a reserved word")
        if not (f.get("label") or "").strip():
            problems.append(f"field '{k}' needs a label")
        if t not in FIELD_TYPES and not (t == "file" and k in old_keys):
            problems.append(f"field '{k}': unknown type '{t}'")
        if t in ("select", "multiselect") and not f.get("source") and not f.get("options"):
            problems.append(f"field '{k}' needs options")
        if t == "ref":
            to = f.get("to", "*")
            if to != "*" and to not in others and to != slug:
                problems.append(f"field '{k}' points to an unknown type '{to}'")
        if t == "computed":
            err = formula.validate(f.get("expr"), [x for x in keys if x])
            if err:
                problems.append(f"field '{k}': {err}")
        if "display" in f and f["display"] not in FIELD_DISPLAYS:
            problems.append(f"field '{k}': unknown display setting")
        if "validate" in f and f["validate"] not in FIELD_VALIDATORS:
            problems.append(f"field '{k}': unknown validate setting")
        if "legacy" in f and (f["legacy"] is not True or f.get("required") or t in ("file", "computed")):
            problems.append(f"field '{k}': legacy can only be true, on an optional field that is neither a file nor calculated")
        if "help" in f and (not isinstance(f["help"], str) or len(f["help"]) > 200):
            problems.append(f"field '{k}': help must be text of at most 200 characters")
        if f.get("addable") and (t not in ("select", "multiselect") or f.get("source")):
            problems.append(f"field '{k}': only choice fields can allow new options")
    seq_key = schema.get("sequence_search")
    if seq_key is not None and not any(f.get("key") == seq_key and f.get("type") in ("text", "longtext") for f in fields):
        problems.append("sequence_search must name a text field of this type")
    cols = schema.get("list_columns", [])
    if not cols:
        problems.append("choose at least one column for the list")
    hidden = {f["key"] for f in fields if f.get("hidden")}
    for c in cols:
        if c not in ("id", "name") + LIST_EXTRA_COLUMNS and c not in keys:
            problems.append(f"list column '{c}' is not a field")
        if c in hidden:
            problems.append(f"list column '{c}' is a hidden field")
    return problems

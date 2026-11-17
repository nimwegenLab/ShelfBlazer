"""The record file format: Markdown with a front matter of one JSON value per line (docs/design.md, section 4). Pure functions."""
import hashlib, json, re

from .errors import StoreError

TOP_ORDER = ["id", "type", "name", "created_by", "created_at", "updated_at", "archived", "legacy_id"]


def parse_record(text, path="?"):
    if not text.startswith("---\n"):
        raise StoreError(f"{path}: missing front matter")
    head, sep, body = text[4:].partition("\n---\n")
    if not sep:
        raise StoreError(f"{path}: unterminated front matter")
    rec, fields = {}, {}
    for line in head.splitlines():
        if line == "fields:":
            continue                                               # the indented lines below it are the fields
        m = re.match(r"( *)(\w+): (.*)$", line)
        if not m:
            raise StoreError(f"{path}: bad line {line[:50]!r}")
        (fields if m.group(1) else rec)[m.group(2)] = json.loads(m.group(3))
    rec["fields"], rec["notes"] = fields, body.strip()
    return rec


def serialize_record(rec, field_order):
    j = lambda v: json.dumps(v, ensure_ascii=False)
    lines = ["---"] + [f"{k}: {j(rec.get(k))}" for k in TOP_ORDER]
    fields = rec["fields"]
    if fields:
        lines.append("fields:")
        keys = [k for k in field_order if k in fields] + [k for k in fields if k not in field_order]
        lines += [f"  {k}: {j(fields[k])}" for k in keys]
    lines += ["---", rec["notes"].strip(), ""]
    return "\n".join(lines)


def version_of(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def unix_newlines(s):
    """Browsers submit textareas with CRLF; records are stored with LF only (and never with NUL characters)."""
    return (s or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")

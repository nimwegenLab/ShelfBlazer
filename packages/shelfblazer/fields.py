"""What each field of a type definition is for, in one place: which fields people fill in, which are shown, and a stored value as text.

Behaviour comes from the field definition (its type and settings such as "validate", "display", "source", "hidden", "legacy"),
never from its key, so a new type in another lab, club or household works the same way as the built-in ones.
"""
import re

from .schema_edit import is_system

# "display" on a text field: how its text is shown in lists and records.
#   full     as stored (the same as no setting)
#   short    a location: box paths abbreviated ("Box 2 Well G3"), the full text underneath on the record page
#   outdated as stored, followed by "[outdated]" (for locations that come from an old system and need re-checking)
FIELD_DISPLAYS = ("short", "full", "outdated")


def is_value(f):
    """A field people fill in with a value (record form, bulk edit, validation): not the title, not a system field, not a file or
    calculated field, not hidden."""
    return f["key"] != "name" and not is_system(f) and f["type"] not in ("file", "computed") and not f.get("hidden")


def is_file_input(f):
    """A File field people attach files to."""
    return f["type"] == "file" and not is_system(f) and not f.get("hidden")


def is_shown(f):
    """A field that pages and exports show (everything but hidden and system fields; the title is shown separately)."""
    return f["key"] != "name" and not f.get("hidden") and not is_system(f)


def is_sequence(f):
    """A nucleotide sequence ("validate": "dna"): shown in its own block, searched by sequence search rather than as words."""
    return f.get("validate") == "dna"


def is_doi(f):
    return f.get("validate") == "doi"


def is_place(f):
    """A choice of storage place (the shared list in config/storage_places.yaml)."""
    return f.get("source") == "storage_places"


def display_mode(f):
    """How a text field is shown: "short" (abbreviated location), "outdated" (marked), or None (as stored)."""
    d = f.get("display")
    return d if f.get("type") == "text" and d in ("short", "outdated") else None


def place_name(places, pid):
    return next((p["name"] for p in places if p["id"] == pid), pid)


def as_text(store, f, v):
    """A stored value as plain text (exports, sorting, the fallback on pages): people by name, places by name, yes/no, lists joined."""
    if v in (None, "", []):
        return ""
    if f["type"] == "user":
        return store.person_name(v)
    if is_place(f):
        return place_name(store.places, v)
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, list):
        return ", ".join(map(str, v))
    return str(v)


def upgrade_schema(schema):
    """Type definitions written before behaviour moved from field keys into settings: give them the settings their keys used to
    imply, in memory only (the files are left as they are). Returns the schema (a copy when anything changed)."""
    fields, changed = [], False
    for f in schema.get("fields", []):
        if f.get("key") == "storage" and f.get("type") == "text" and "display" not in f:
            f, changed = dict(f, display="short"), True              # the old free-text location: abbreviated by default
        fields.append(f)
    return dict(schema, fields=fields) if changed else schema


# ---------------------------------------------------------------------------------------------- "validate" on a text field
FIELD_VALIDATORS = ("dna", "doi")           # "validate" on a text field: "dna" = only nucleotide letters (check_dna), "doi" = only DOIs (normalize_dois)
DNA_RE = re.compile(r"[ACGTURYSWKMBDHVN\s]*", re.I)             # IUPAC nucleotide codes (U for RNA) and whitespace


DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")            # the pattern Crossref recommends
DOI_PREFIX_RE = re.compile(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)", re.I)


def normalize_dois(text):
    """The DOIs in text (separated by spaces or commas; a "doi:" prefix or a doi.org link is fine) as bare DOIs, comma-separated. Raise ValueError for anything else."""
    text = DOI_PREFIX_RE.sub("", (text or "").strip())
    out = []
    for part in re.split(r"[\s,]+", text):
        part = DOI_PREFIX_RE.sub("", part).rstrip(".,;")
        if not part:
            continue
        if not DOI_RE.fullmatch(part):
            raise ValueError(f"not a DOI: '{part[:40]}'. Give DOIs only, like 10.1038/nmeth895 (a doi.org link is fine; separate several with commas)")
        if part.casefold() not in [d.casefold() for d in out]:
            out.append(part)
    return ", ".join(out)


def check_dna(text):
    """Raise ValueError unless text is a nucleotide sequence: IUPAC letters and white space, optionally with FASTA header lines (starting with >)."""
    lines = [ln for ln in text.split("\n") if not ln.startswith(">")]
    bad = sorted({c for c in "".join(lines) if not DNA_RE.fullmatch(c)})
    if bad:
        raise ValueError("not a DNA sequence: only the letters A C G T U and the IUPAC codes (R Y S W K M B D H V N) are allowed, "
                         "plus an optional >header line; found " + " ".join(repr(c) for c in bad[:6]))

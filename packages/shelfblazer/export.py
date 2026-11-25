"""Tab-separated export of records (one record type), for spreadsheets and scripts.

Pure functions over a Store: ``columns`` lists what can be exported, ``tsv`` writes the chosen columns of the chosen records.
Only what the viewer may see leaves: records they cannot read are skipped, hidden fields are never offered, calculated fields are
evaluated with the viewer's visibility, people are written by display name (never by user ID)."""
import csv, io, re

from . import fields as fieldrules, formula, ids as ids_mod

SEP = ", "                                          # between several values of one cell (options, links, file names)
EXTRA = [("created_by", "Owner"), ("created_at", "Created"), ("archived", "Archived")]
NUMBER = re.compile(r"^[-+]?\d+([.,]\d+)?([eE][-+]?\d+)?$")


def columns(store, slug):
    """[(key, label)] in export order: ID, name, the type's visible fields, then owner, creation time and archived flag."""
    cols = [("id", "ID"), ("name", "Name")]
    for f in store.schemas[slug]["fields"]:
        if fieldrules.is_shown(f):
            cols.append((f["key"], f["label"]))
    return cols + EXTRA


def safe(text):
    """Spreadsheets run cells that start with = + - @ as formulas: a text cell like that gets a leading apostrophe (numbers are left alone)."""
    if text and text[0] in "=+-@\t\r" and not NUMBER.match(text):
        return "'" + text
    return text


def written(rid, viewer):
    """An ID as written in the file: your own personal records as ST-P1 (an import reads it as yours), anyone else's with its owner."""
    return ids_mod.short(rid) if viewer and ids_mod.owner_of(rid) == viewer["uid"] else rid


def value(store, rec, key, viewer):
    """One cell as text."""
    if key == "id":
        return written(rec["id"], viewer)
    if key == "name":
        return rec["name"]
    if key == "created_by":
        return store.person_name(rec["created_by"])
    if key == "created_at":
        return rec["created_at"] or ""
    if key == "archived":
        return "yes" if rec["archived"] else ""
    fdef = next((f for f in store.schemas[rec["type"]]["fields"] if f["key"] == key), None)
    if fdef is None:
        return ""
    if fdef["type"] == "computed":
        v, _ = store.compute(rec, fdef, viewer)
        return formula.format_value(v)
    v = rec["fields"].get(key)
    if isinstance(v, list):
        return SEP.join(written(i, viewer) if fdef["type"] == "ref" else fieldrules.as_text(store, fdef, i) for i in v)
    return fieldrules.as_text(store, fdef, v)


def tsv(store, viewer, slug, ids, keys):
    """Header line plus one line per record the viewer may read (in the order given), only the wanted columns (in export order).
    Fields with tabs, line breaks or quotes are quoted the usual way, so every spreadsheet and ``pandas.read_csv(sep="\\t")`` reads them back."""
    wanted = [(k, label) for k, label in columns(store, slug) if k in set(keys)]
    out = io.StringIO()
    w = csv.writer(out, delimiter="\t", lineterminator="\n")
    w.writerow([safe(label) for _, label in wanted])
    n = 0
    for rid in ids:
        rec = store.readable(viewer, rid)
        if rec is None or rec["type"] != slug:
            continue
        w.writerow([safe(value(store, rec, k, viewer)) for k, _ in wanted])
        n += 1
    return out.getvalue(), n

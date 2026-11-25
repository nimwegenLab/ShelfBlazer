"""How field values look on pages: list cells, record-page values, calculated fields, and the fields of the record form.
Built once per app by ``make`` (they read the store, the accounts and display names)."""
import os, re
from types import SimpleNamespace

from shelfblazer import fields as fieldrules, formula, ids
from shelfblazer.search import norm
from .format import short_storage, storage_chunks
from .views import hue_of, initials

IMAGE_EXTENSIONS = frozenset(("jpg", "jpeg", "png", "webp", "gif", "avif"))      # shown as pictures; other files (HEIC) stay plain links


def make(store, users, display):
    def label_of(slug, key):
        for f in store.schemas[slug]["fields"]:
            if f["key"] == key:
                return f["label"]
        return {"id": "ID", "name": "Name", "created_by": "Owner", "created_at": "Created"}.get(key, key.replace("_", " ").title())

    def computed_cell(rec, fdef, viewer):
        """A computed field for a list or record: dict(text | links, sort, error)."""
        value, err = store.compute(rec, fdef, viewer)
        if isinstance(value, list) and value and all(isinstance(v, str) and v in store.records for v in value):
            return dict(text="", links=[(v, v) for v in value], sort=formula.sort_key(len(value)), error=err)
        return dict(text=formula.format_value(value), sort=formula.sort_key(value), error=err)

    def present(f, v):
        """A stored value of a field (not empty, not a link or calculated field) for a page: dict(text, and chips | url | dois |
        full + outdated). Lists, record pages and old versions all show values through this one function."""
        if f["type"] == "user":
            return dict(text=display(v))
        if fieldrules.is_place(f):
            return dict(text=fieldrules.place_name(store.places, v))
        if fieldrules.is_doi(f) and isinstance(v, str):
            return dict(text=v, dois=[d for d in re.split(r"[\s,]+", v) if d])
        if f["type"] == "url":
            return dict(text=v, url=v)
        mode = fieldrules.display_mode(f)
        if mode and isinstance(v, str):
            shown = short_storage(v) if mode == "short" else v
            return dict(text=shown, full=v if mode == "short" else None, outdated=(mode == "outdated"),
                        chunks=storage_chunks(shown) if mode == "short" else None)
        if f["type"] in ("select", "multiselect"):
            vals = v if isinstance(v, list) else [v]
            return dict(text=", ".join(map(str, vals)), chips=[(x, hue_of(x)) for x in vals])
        return dict(text=fieldrules.as_text(store, f, v))

    def cell(rec, key, viewer=None):
        """One list cell: dict(text, links=[(id, label)], chips, href, avatar, sort, ...)."""
        fdef = next((f for f in store.schemas[rec["type"]]["fields"] if f["key"] == key), None)
        if fdef and fdef["type"] == "computed":
            return computed_cell(rec, fdef, viewer)
        if key == "id":
            return dict(text=rec["id"], href=f"/r/{rec['id']}", sort=ids.sort_key(rec))
        if key == "name":
            return dict(text=rec["name"], sort=norm(rec["name"]))
        if key == "created_by":
            n = display(rec["created_by"]); return dict(text=n, sort=norm(n), avatar=initials(n), ahue=hue_of(rec["created_by"]))
        if key == "created_at":
            return dict(text=(rec["created_at"] or "")[:10], sort=rec["created_at"] or "")
        v = rec["fields"].get(key)
        if v in (None, "", []) or fdef is None:
            return dict(text="" if v in (None, "", []) else str(v), sort="")
        if fdef["type"] == "ref":
            return dict(text="", links=[(i, i) for i in v], sort=",".join(v))
        c = present(fdef, v)
        c["sort"] = re.sub(r"\d+", lambda m: m.group().zfill(6), norm(c["text"])) if fieldrules.display_mode(fdef) else norm(c["text"])  # "Box 2" before "Box 10"
        return c

    def form_descriptors(slug, values=None, errors=None, creating=False, place_new=False, keep_legacy=(), typed=None):
        """Fields of the form. A field marked "legacy": true (the free-text `storage`, a label from the old database) is only offered where the record already has a value (keep_legacy: those keys, or True for all, as in bulk edit); chemicals use Storage place."""
        values, errors, out = values or {}, errors or {}, []
        for f in store.schemas[slug]["fields"]:
            k = f["key"]
            if not (fieldrules.is_value(f) or fieldrules.is_file_input(f)) or (f.get("legacy") and keep_legacy is not True and k not in keep_legacy):
                continue
            opts = []
            if f["type"] in ("select", "multiselect"):
                if fieldrules.is_place(f):
                    opts = [(p["id"], p["name"]) for p in store.places_in_use()]
                    if values.get(k) and values[k] not in dict(opts):                               # just created, not yet used by a saved record
                        opts += [(p["id"], p["name"]) for p in store.places if p["id"] == values[k]]
                    if place_new:
                        opts.append(("__new__", "New place…"))
                else:
                    opts = [(o, o) for o in f.get("options", [])]
                    if place_new and f.get("addable") and f["type"] == "select":
                        opts.append(("__new__", "New option…"))
            v = values.get(k, "")
            if f["type"] == "ref" and isinstance(v, list):
                v = ", ".join(v)
            if f["type"] == "user" and v:
                u = users.resolve(v)
                v = u["username"] if u else ""                                     # the form edits usernames; records hold user IDs
            if f["type"] == "file":
                v = [os.path.basename(x) for x in (v or [])]                                 # the files already attached (shown by name)
            out.append(dict(key=k, label=f["label"], type=f["type"], value=v, options=opts, error=errors.get(k),
                            required=f.get("required"), help=f.get("help"), to=f.get("to"), multiple=f.get("multiple", True), fold=bool(f.get("fold")),
                            new_place=bool(place_new and fieldrules.is_place(f)),
                            new_option=bool(place_new and f.get("addable") and not f.get("source")),
                            new_value=(typed or {}).get(k, "")))                                     # a new option or place typed before an error
        return out

    def picture(rec):
        """The picture of a record: the first image in the file field named by its type's "thumbnail" (path under /files/), else None."""
        key = store.schemas[rec["type"]].get("thumbnail")
        return next((p for p in rec["fields"].get(key, []) if p.rsplit(".", 1)[-1].lower() in IMAGE_EXTENSIONS), None) if key else None

    return SimpleNamespace(label_of=label_of, computed_cell=computed_cell, present=present, cell=cell, form_descriptors=form_descriptors, picture=picture)

"""Record pages: view, an earlier version, new, edit, archive, and downloading attached files."""
import os, re

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from starlette.datastructures import UploadFile

from shelfblazer import fields as fieldrules, ids, perms, uploads
from shelfblazer.auth import UID_RE
from shelfblazer.store import Forbidden, NotFound, ValidationError
from . import scan
from .files import FILES_PER_REQUEST

UID_IN_TEXT = re.compile(r"(?<!~)\b" + UID_RE.pattern.strip("^$") + r"\b")   # user IDs in commit messages ("ST-12: update by U7F3K2QA"), shown as names


def register(app, c):
    store, users = c.store, c.users
    need_user, render, display, flash, check_csrf = c.need_user, c.render, c.display, c.flash, c.check_csrf
    computed_cell, present, form_descriptors, picture = c.computed_cell, c.present, c.form_descriptors, c.picture
    edit_refusal, collections_for = c.edit_refusal, c.collections_for

    async def read_form(request, slug):
        """The record form: (form, raw field values, files chosen in File fields, new options/places typed: key -> name)."""
        form = await request.form()                                               # size already checked by BodyLimit
        check_csrf(request, form)
        raw, files, additions = {}, {}, {}
        try:
            for f in store.schemas[slug]["fields"]:                                       # files chosen in File fields: read now, attached after the record is saved
                if fieldrules.is_file_input(f):
                    for part in form.getlist(f"f_{f['key']}"):
                        if isinstance(part, UploadFile) and part.filename and sum(map(len, files.values())) < FILES_PER_REQUEST:
                            files.setdefault(f["key"], []).append((part.filename, await part.read(uploads.MAX_UPLOAD_BYTES + 1)))
        finally:
            await form.close()
        for f in store.schemas[slug]["fields"]:
            k, ty = f["key"], f["type"]
            if not fieldrules.is_value(f):
                continue
            fk = f"f_{k}"
            if ty == "bool":
                raw[k] = form.get(fk) == "on"
            elif ty == "multiselect":
                raw[k] = form.getlist(fk)
            else:
                if f.get("legacy") and fk not in form:
                    continue                                                           # not offered on this form: keep the stored value
                raw[k] = form.get(fk, "")
            if str(form.get(fk + "_new", "")).strip():
                additions[k] = str(form.get(fk + "_new"))                             # a new option or place: saved with the record, if it is valid
        return form, raw, files, additions

    def attach_files(request, rid, user, files):
        """Attach the files chosen in the form to the saved record (one commit each); problems are reported, the record stays saved."""
        for key, items in files.items():
            for filename, content in items:
                try:
                    store.add_attachment(rid, user, filename, content, field=key)
                    flash(request, f"Added {uploads.safe_name(filename)} to {rid}.")
                except (ValidationError, Forbidden) as e:
                    flash(request, f"Not added: {e.errors.get('file', str(e)) if isinstance(e, ValidationError) else 'you may not change this record'}", "err")

    def attachment_info(rec, key):
        """[(path under data/files, file name, human size or None when the file is missing)] for the record page."""
        out = []
        for rel in rec["fields"].get(key, []):
            full = os.path.join(store.repo, "data", "files", rel)
            out.append((rel, os.path.basename(rel), uploads.human_size(os.path.getsize(full)) if os.path.isfile(full) else None))
        return out

    # ------------------------------------------------------------------ records
    def record_rows(rec, schema, u, seen, calculated=True):
        """The label/value rows of a record page, read with the given type definition (an old version of a record uses the one from
        its commit). Sequences are not rows: see record_blocks."""
        rows = []
        readable_now = store.readable_types(u)
        for f in schema["fields"]:
            k = f["key"]
            if not fieldrules.is_shown(f) or f["type"] == "file" or fieldrules.is_sequence(f):
                continue
            if f["type"] == "computed":
                if not calculated:
                    continue                                    # depends on today's date and on other records: not part of an old version
                c = computed_cell(rec, f, u)
                if c.get("links"):
                    rows.append((f["label"], dict(links=[(i, seen[i]["name"]) for i, _ in c["links"] if i in seen], computed=True)))
                else:
                    rows.append((f["label"], dict(text=c["text"], computed=True, error=c["error"])))
                continue
            v = rec["fields"].get(k)
            if v in (None, "", []) and not (f["type"] == "bool" and k in rec["fields"]):
                continue
            if f["type"] == "ref":
                gone = {i for i in v if i not in seen and i in store.records and store.records[i]["type"] in readable_now}      # archived by someone else: the ID stays, the record does not open
                rows.append((f["label"], dict(links=[(i, seen[i]["name"] if i in seen else "") for i in v if i in seen or i not in store.records or i in gone], gone=gone)))
            else:
                rows.append((f["label"], present(f, v)))
        return rows

    def record_blocks(rec, schema):
        """Sequences, shown under the table in their own folded block: [(label, text)]."""
        return [(f["label"], rec["fields"][f["key"]]) for f in schema["fields"]
                if fieldrules.is_shown(f) and fieldrules.is_sequence(f) and rec["fields"].get(f["key"])]

    @app.get("/r/{rid}", response_class=HTMLResponse)
    async def record(request: Request, rid: str):
        u = need_user(request)
        key = store.resolve(rid, u["uid"])                           # a former prefix (ST-12 after ST became BS), a short personal ID (yours), a moved record's old ID
        if ids.is_personal(rid) and not ids.owner_of(rid) and store.readable(u, key) is None:
            return RedirectResponse(f"/?q={rid}", 302)               # ST-P3 and you have no such record: whose is it? the search says
        if key != rid:
            return RedirectResponse(f"/r/{key}", 302)
        rec = store.readable(u, rid)
        if not rec:
            raise NotFound(rid)
        slug = rec["type"]
        seen = store.visible_records(u)
        rows = record_rows(rec, store.schemas[slug], u, seen)
        refs = {}
        for label, r in store.referenced_by(rid):
            if r["id"] not in seen:
                continue
            refs.setdefault((r["type"], label), []).append(r)
        return render(request, "record.html", rec=rec, picture=picture(rec), schema=store.schemas[slug], rows=rows, blocks_=record_blocks(rec, store.schemas[slug]), refs=refs,
                      edit_mode=perms.setting(store.schemas[slug], "edit"),
                      history=[dict(h, author=display(h["author"]), subject=UID_IN_TEXT.sub(lambda m: display(m.group(0)), h["subject"])) for h in store.history(rid)],
                      file_sections=[(f["key"], f["label"], attachment_info(rec, f["key"]), "files-" + f["key"])
                                     for f in store.schemas[slug]["fields"] if fieldrules.is_file_input(f)])

    @app.get("/r/{rid}/at/{sha}", response_class=HTMLResponse)
    async def record_version(request: Request, rid: str, sha: str):
        """A record as one of its commits saved it: read-only, with the type definition from that commit, for people who may read the record today."""
        u = need_user(request)
        cur = store.readable(u, rid)
        if not cur:
            raise NotFound(rid)
        v = store.record_at(rid, sha)
        if v["current"]:                                                      # the last change of this record is the record as it is now
            return RedirectResponse(f"/r/{rid}", 303)
        rec, schema = v["rec"], v["schema"]
        rows = record_rows(rec, schema, u, store.visible_records(u), calculated=False)
        files = [(f["label"], [os.path.basename(x) for x in rec["fields"].get(f["key"], [])]) for f in schema["fields"]
                 if fieldrules.is_file_input(f) and rec["fields"].get(f["key"])]
        return render(request, "version.html", rec=rec, cur=cur, schema=schema, rows=rows, blocks_=record_blocks(rec, schema), files=files,
                      sha=v["sha"], when=v["when"][:16].replace("T", " "),
                      author=display(v["author"]), subject=UID_IN_TEXT.sub(lambda m: display(m.group(0)), v["subject"]))

    @app.get("/t/{slug}/new", response_class=HTMLResponse)
    async def new_form(request: Request, slug: str, code: str = "", c: str = ""):
        u = need_user(request)
        if slug not in store.schemas or slug not in store.readable_types(u):
            raise HTTPException(404, "unknown type")
        choices = collections_for(u, slug)
        if not choices:
            raise Forbidden(f"Only administrators can add {store.schemas[slug]['name'].lower()} records.")
        chosen = "mine" if c == "mine" and ("mine", "My collection") in choices or len(choices) == 1 and choices[0][0] == "mine" else "lab"
        vals = scan.prefilled(store.schemas[slug], code)                      # coming from a scan: the code is already in the form
        return render(request, "form.html", slug=slug, schema=store.schemas[slug], mode="new", name="", notes="",
                      fields=form_descriptors(slug, vals, creating=True, place_new=True), version="", collections=choices, chosen=chosen)

    @app.post("/t/{slug}/new")
    async def new_submit(request: Request, slug: str):
        u = need_user(request)
        if slug not in store.schemas or slug not in store.readable_types(u):
            raise HTTPException(404, "unknown type")
        choices = collections_for(u, slug)
        if not choices:                                                      # before reading the form: it may add options and places
            raise Forbidden(f"Only administrators can add {store.schemas[slug]['name'].lower()} records.")
        form, raw, files, additions = await read_form(request, slug)
        chosen = "mine" if form.get("collection") == "mine" else "lab"
        if chosen not in [v for v, _ in choices]:
            raise Forbidden(f"You cannot add {store.schemas[slug]['name'].lower()} records to that collection.")
        try:
            rid = store.create(slug, u, form.get("name", ""), raw, form.get("notes", ""), additions=additions, personal=chosen == "mine")
        except ValidationError as e:
            return render(request, "form.html", status=422, slug=slug, schema=store.schemas[slug], mode="new", name=form.get("name", ""),
                          notes=form.get("notes", ""), fields=form_descriptors(slug, raw, e.errors, creating=True, place_new=True, typed=additions),
                          version="", errors=e.errors, collections=choices, chosen=chosen)
        flash(request, f"Created {rid}{' in your collection' if chosen == 'mine' else ''}. Write this ID on the label.")
        attach_files(request, rid, u, files)
        return RedirectResponse((f"/t/{slug}/new" + ("?c=mine" if chosen == "mine" else "")) if form.get("action") == "another" else f"/r/{rid}", 303)

    def owner_choices(u):
        """(user ID, name, username) of everyone an administrator may hand a record to; empty for other people (the form shows no owner box)."""
        return [(p["uid"], p["name"], p["username"]) for p in users.all()] if perms.is_admin(u) else []

    @app.get("/r/{rid}/edit", response_class=HTMLResponse)
    async def edit_form(request: Request, rid: str):
        u = need_user(request)
        rec = store.readable(u, rid)
        if not rec:
            raise NotFound(rid)
        if not store.can_edit(u, rec):
            raise Forbidden(edit_refusal(rec))
        return render(request, "form.html", slug=rec["type"], schema=store.schemas[rec["type"]], mode="edit", rec=rec, name=rec["name"],
                      notes=rec["notes"], fields=form_descriptors(rec["type"], rec["fields"], place_new=True, keep_legacy={k for k, v in rec["fields"].items() if v}), version=rec["_version"],
                      people=owner_choices(u), owner=rec["created_by"])

    @app.post("/r/{rid}/edit")
    async def edit_submit(request: Request, rid: str):
        u = need_user(request)
        rec = store.readable(u, rid)
        if not rec:
            raise NotFound(rid)
        if not store.can_edit(u, rec):                                       # before reading the form: it may add options and places
            raise Forbidden(edit_refusal(rec))
        form, raw, files, additions = await read_form(request, rec["type"])
        try:
            store.update(rid, u, form.get("name", ""), raw, form.get("notes", ""), form.get("version", ""),
                         owner=form.get("owner", "") if perms.is_admin(u) else None, additions=additions)
        except ValidationError as e:
            return render(request, "form.html", status=422, slug=rec["type"], schema=store.schemas[rec["type"]], mode="edit", rec=rec,
                          name=form.get("name", ""), notes=form.get("notes", ""), fields=form_descriptors(rec["type"], {**rec["fields"], **raw}, e.errors, place_new=True, keep_legacy={k for k, v in rec["fields"].items() if v}, typed=additions),
                          version=form.get("version", ""), errors=e.errors, people=owner_choices(u), owner=form.get("owner", rec["created_by"]))
        flash(request, f"Saved {rid}.")
        attach_files(request, rid, u, files)
        return RedirectResponse(f"/r/{rid}", 303)

    @app.post("/r/{rid}/archive")
    async def archive(request: Request, rid: str):
        u = need_user(request)
        form = await request.form()
        check_csrf(request, form)
        rec = store.readable(u, rid)
        if not rec:
            raise NotFound(rid)
        store.set_archived(rid, u, not rec["archived"])
        flash(request, f"{rid} {'restored' if rec['archived'] else 'archived'}.")
        now = store.readable(u, rid)                                          # archiving someone else's record (open types): it is gone for you
        return RedirectResponse(f"/r/{rid}" if now else f"/t/{rec['type']}", 303)

    @app.get("/files/{path:path}")
    async def files(request: Request, path: str):
        u = need_user(request)
        base = os.path.realpath(os.path.join(store.repo, "data", "files"))
        full = os.path.realpath(os.path.join(base, path))
        if not full.startswith(base + os.sep) or not os.path.isfile(full):
            raise HTTPException(404, "file not found")
        owner_dir = os.path.relpath(full, base).split(os.sep)[0]                 # files live in data/files/<record ID>/
        rec = store.records.get(owner_dir)
        if rec is not None:
            if not store.readable(u, owner_dir):                                  # hidden type, or archived by someone else
                raise HTTPException(404, "file not found")
        else:                                                                     # a purged record's folder still has a type by its prefix
            kind = store.prefix_type.get(ids.prefix_of(owner_dir))
            if not perms.is_admin(u) and (kind is None or kind not in store.readable_types(u)):
                raise HTTPException(404, "file not found")
        return FileResponse(full, filename=os.path.basename(full))

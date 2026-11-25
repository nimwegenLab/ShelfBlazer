"""Type administration (admins only): create types, add and change fields and options, colour, list columns, hide fields, remove empty types."""
import copy

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.requests import Request

from shelfblazer import formula, ids, perms, schema_edit as se
from shelfblazer.store import FIELD_DISPLAYS, TYPE_COLORS, TYPE_ICONS, ValidationError


RULE_WORDS = {"read": {"all": "everyone", "admin": "admins"}, "create": {"all": "everyone", "admin": "admins"},
              "edit": {"owner": "creator, admins", "admin": "admins", "all": "everyone"}}


def register(app, c):
    store = c.store

    def back(slug=None):
        return RedirectResponse(f"/admin/types/{slug}" if slug else "/admin/types", 303)

    def edit(request, user, slug, change, message, ok):
        """Apply a pure schema change, validate and save it, flash the outcome, return to the type page."""
        try:
            new = change(copy.deepcopy(store.schemas[slug]))
            store.save_schema(slug, new, user, message=message)
            c.flash(request, ok)
        except (se.SchemaError, ValidationError) as e:
            c.flash(request, e.errors.get("type", str(e)) if isinstance(e, ValidationError) else str(e), "err")
        return back(slug)

    async def admin_form(request):
        user = c.need_admin(request)
        form = await request.form()
        c.check_csrf(request, form)
        return user, form

    def known(slug):
        if slug not in store.schemas:
            raise HTTPException(404, "unknown type")

    @app.get("/admin/types", response_class=HTMLResponse)
    async def types_list(request: Request):
        c.need_admin(request)
        rows = []
        for slug in store.ordered_slugs():
            sch = store.schemas[slug]
            rows.append(dict(slug=slug, schema=sch, restricted=perms.restricted(sch), rules={a: (RULE_WORDS[a][perms.setting(sch, a)], perms.setting(sch, a) != perms.DEFAULTS[a]) for a in perms.DEFAULTS}, n=sum(1 for r in store.records.values() if r["type"] == slug),
                             nfields=sum(1 for f in sch["fields"] if f["key"] != "name" and f["type"] != "file" and f["key"] not in se.SYSTEM_KEYS)))
        return c.render(request, "admin_types.html", rows=rows, colors=TYPE_COLORS, warnings=store.schema_warnings)

    @app.post("/admin/types/{slug}/order")
    async def type_order(request: Request, slug: str):
        user, form = await admin_form(request)
        known(slug)
        try:
            store.move_type(slug, form.get("direction", ""), user)
        except ValidationError as e:
            c.flash(request, e.errors.get("order", str(e)), "err")
        return back()

    @app.post("/admin/types/new")
    async def type_new(request: Request):
        user, form = await admin_form(request)
        name, prefix, color = form.get("name", "").strip(), form.get("prefix", "").strip().upper(), form.get("color", "")
        slug = se.slugify(name)
        try:
            schema = se.new_type(name, prefix, color if color in TYPE_COLORS else None)
            store.save_schema(slug, schema, user, message=f"type {slug}: create", create=True)
        except ValidationError as e:
            c.flash(request, e.errors.get("type", str(e)), "err")
            return back()
        c.flash(request, f"Type {name} created with the prefix {prefix}. Add its fields below; new records get IDs like {prefix}-1.")
        return back(slug)

    @app.get("/admin/types/{slug}", response_class=HTMLResponse)
    async def type_page(request: Request, slug: str):
        c.need_admin(request)
        known(slug)
        schema = store.schemas[slug]
        fields = []
        for f in schema["fields"]:
            system = se.is_system(f)
            fields.append(dict(f=f, system=system, used=store.used_options(slug, f["key"]) if f["type"] in ("select", "multiselect") else set(),
                               movable=f["key"] != "name" and not system))
        columns = [("id", "ID", True), ("name", "Name", True)] + [(f["key"], f["label"], False) for f in schema["fields"] if f["key"] != "name"
                   and not f.get("hidden") and f["key"] not in se.SYSTEM_KEYS and f["type"] != "file"] + [("created_by", "Owner", False), ("created_at", "Created", False)]
        return c.render(request, "admin_type.html", slug=slug, schema=schema, fields=fields, columns=columns, colors=TYPE_COLORS, icons=TYPE_ICONS, types=se.FIELD_TYPES,
                        type_labels=se.TYPE_LABELS, others=[(s, v["name"]) for s, v in store.schemas.items()], n=sum(1 for r in store.records.values() if r["type"] == slug),
                        displays=FIELD_DISPLAYS, perm=schema.get("permissions", {}), warnings=[w for w in store.schema_warnings if f"'{slug}'" in w])

    @app.post("/admin/types/{slug}/settings")
    async def type_settings(request: Request, slug: str):
        user, form = await admin_form(request)
        known(slug)
        perms_in = {k: form.get(f"perm_{k}", "") for k in ("read", "create", "edit", "personal")}
        return edit(request, user, slug, lambda s: se.update_settings(s, name=form.get("name", ""), color=form.get("color", ""), icon=form.get("icon", ""),
                                                                    list_columns=form.getlist("columns"), permissions=perms_in),
                    f"type {slug}: settings", "Settings saved.")

    @app.post("/admin/types/{slug}/fields/add")
    async def field_add(request: Request, slug: str):
        user, form = await admin_form(request)
        known(slug)
        return edit(request, user, slug, lambda s: se.add_field(s, form.get("label", ""), form.get("type", ""), key=form.get("key") or None,
                                                              required=form.get("required") == "on", options=form.get("options", ""),
                                                              to=form.get("to") or None, expr=form.get("expr", "")),
                    f"type {slug}: add field {form.get('label', '').strip()}", f"Field “{form.get('label', '').strip()}” added.")

    @app.post("/admin/types/{slug}/fields/{key}/update")
    async def field_update(request: Request, slug: str, key: str):
        user, form = await admin_form(request)
        known(slug)
        used = store.used_options(slug, key)
        return edit(request, user, slug, lambda s: se.update_field(
            s, key, label=form.get("label"), required=(form.get("required") == "on") if "required_present" in form else None,
            ftype=form.get("type") or None, add_options=form.get("add_options", ""), remove_options=form.getlist("remove_options"),
            expr=form.get("expr") if "expr" in form else None, display=form.get("display") if "display" in form else None, used_options=used),
            f"type {slug}: change field {key}", f"Field {key} saved.")

    @app.post("/admin/types/{slug}/fields/{key}/hidden")
    async def field_hidden(request: Request, slug: str, key: str):
        user, form = await admin_form(request)
        known(slug)
        hide = not any(f["key"] == key and f.get("hidden") for f in store.schemas[slug]["fields"])
        return edit(request, user, slug, lambda s: se.set_hidden(s, key, hide), f"type {slug}: {'hide' if hide else 'show'} field {key}",
                    f"Field {key} is now {'hidden (its data is kept)' if hide else 'shown again'}.")

    @app.post("/admin/types/{slug}/fields/{key}/move")
    async def field_move(request: Request, slug: str, key: str):
        user, form = await admin_form(request)
        known(slug)
        return edit(request, user, slug, lambda s: se.move_field(s, key, form.get("direction", "")), f"type {slug}: move field {key}", f"Moved {key}.")

    @app.post("/admin/types/{slug}/delete")
    async def type_delete(request: Request, slug: str):
        user, form = await admin_form(request)
        known(slug)
        try:
            store.delete_type(slug, user)
        except ValidationError as e:
            c.flash(request, e.errors.get("type", str(e)), "err")
            return back(slug)
        c.flash(request, f"Type {slug} removed.")
        return back()

    @app.get("/admin/types/{slug}/preview")
    async def formula_preview(request: Request, slug: str, expr: str = "", id: str = ""):
        """Try a formula on one record while writing it."""
        c.need_admin(request)
        known(slug)
        keys = [f["key"] for f in store.schemas[slug]["fields"]]
        problem = formula.validate(expr, keys)
        if problem:
            return JSONResponse({"error": problem, "value": None})
        rec = store.records.get(id.strip()) if id.strip() else next((r for r in sorted(store.records.values(), key=ids.sort_key, reverse=True) if r["type"] == slug and not ids.is_personal(r["id"])), None)  # the newest lab record
        if rec is None or rec["type"] != slug:
            return JSONResponse({"error": f"there is no {store.schemas[slug]['name'].lower()} {id.strip() or 'yet'} to try it on", "value": None})
        value, err = formula.evaluate(expr, rec, store, today=store.today())
        return JSONResponse({"error": err, "value": formula.format_value(value), "record": rec["id"]})

"""Import records from a CSV/TSV file (administrators only): upload, choose what each column is, preview every row, then write
everything as one commit that can be undone. The file text travels between the steps in the forms, so nothing is kept on the server."""
import re

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.datastructures import UploadFile
from starlette.requests import Request

from shelfblazer import tableimport as ti

PREVIEW_ROWS = 300            # rows shown on the preview page (rows with problems come first)
SAMPLES = 3                   # example values shown under each column on the mapping page


def register(app, c):
    store = c.store

    async def read(request, slug):
        user = c.need_admin(request)
        form = await request.form()
        c.check_csrf(request, form)
        if slug not in store.schemas:
            raise HTTPException(404, "unknown type")
        return user, form

    def page(request, template, slug, status=200, **ctx):
        return c.render(request, template, status=status, slug=slug, schema=store.schemas[slug], **ctx)

    def mapping_of(form, header):
        return [form.get(f"map_{i}") or None for i in range(len(header))]

    def start(request, slug, message, status=422):
        c.flash(request, message, "err")
        return page(request, "import_upload.html", slug, status=status, max_kb=ti.MAX_BYTES // 1000, max_rows=ti.MAX_ROWS)

    def column_page(request, slug, text, header, rows, delimiter, mapping, message=None, status=200):
        if message:
            c.flash(request, message, "err")
        tgts = ti.targets(store, slug)
        samples = [[] for _ in header]
        for _, cells in rows[:50]:
            for i in range(len(header)):
                v = cells[i].strip() if i < len(cells) else ""
                if v and v not in samples[i] and len(samples[i]) < SAMPLES:
                    samples[i].append(v[:40])
        return page(request, "import_map.html", slug, status=status, text=text, header=header, samples=samples, n=len(rows), mapping=mapping,
                    delimiter={"\t": "tab", ",": "comma", ";": "semicolon"}.get(delimiter, delimiter), targets=tgts)

    def preview_page(request, slug, text, header, mapping, res, status=200):
        entries = res["entries"]
        problems = [e for e in entries if e["errors"] or e["warnings"]]
        rest = [e for e in entries if not (e["errors"] or e["warnings"])]
        shown = sorted(problems + rest[:max(0, PREVIEW_ROWS - len(problems))], key=lambda e: e["line"])
        return page(request, "import_preview.html", slug, status=status, text=text, header=header, mapping=mapping, res=res, shown=shown,
                    total=len(entries), tgts=dict(ti.targets(store, slug)))

    @app.get("/admin/types/{slug}/import", response_class=HTMLResponse)
    async def import_start(request: Request, slug: str):
        c.need_admin(request)
        if slug not in store.schemas:
            raise HTTPException(404, "unknown type")
        return page(request, "import_upload.html", slug, max_kb=ti.MAX_BYTES // 1000, max_rows=ti.MAX_ROWS)

    @app.post("/admin/types/{slug}/import", response_class=HTMLResponse)
    async def import_columns(request: Request, slug: str):
        """Step 1 (a file) and the way back from the preview (the text again): show the columns of the file and what each one is."""
        user, form = await read(request, slug)
        upload = form.get("file")
        try:
            if isinstance(upload, UploadFile) and upload.filename:
                data = await upload.read()
                await upload.close()                                       # the uploaded file is held in a temporary file
                text = ti.decode(data)
            else:
                text = form.get("text", "")
                if not text.strip():
                    return start(request, slug, "Choose a file first.")
            header, rows, delimiter = ti.parse(text)
        except ti.FileProblem as e:
            return start(request, slug, str(e))
        mapping = mapping_of(form, header) if form.get("map_0") is not None or form.get("remapped") else ti.auto_map(header, ti.targets(store, slug))
        return column_page(request, slug, text, header, rows, delimiter, mapping)

    async def check(request, slug, apply):
        user, form = await read(request, slug)
        text = form.get("text", "")
        try:
            header, rows, delimiter = ti.parse(text)
            mapping = mapping_of(form, header)
            res = store.import_table(user, slug, header, rows, mapping, dry_run=not apply)
        except ti.FileProblem as e:
            try:
                header, rows, delimiter = ti.parse(text)
            except ti.FileProblem as e2:
                return start(request, slug, str(e2))
            return column_page(request, slug, text, header, rows, delimiter, mapping_of(form, header), message=str(e), status=422)
        return text, header, mapping, res

    @app.post("/admin/types/{slug}/import/preview", response_class=HTMLResponse)
    async def import_preview(request: Request, slug: str):
        out = await check(request, slug, apply=False)
        if not isinstance(out, tuple):
            return out
        text, header, mapping, res = out
        return preview_page(request, slug, text, header, mapping, res)

    @app.post("/admin/types/{slug}/import/apply", response_class=HTMLResponse)
    async def import_apply(request: Request, slug: str):
        out = await check(request, slug, apply=True)
        if not isinstance(out, tuple):
            return out
        text, header, mapping, res = out
        if not res["sha"]:
            return preview_page(request, slug, text, header, mapping, res, status=422 if res["errors"] else 200)
        return RedirectResponse(f"/admin/types/{slug}/import/done/{res['sha'][:12]}", 303)      # a refresh must not import the file again

    SUBJECT = re.compile(r"^bulk: import (\d+) new, (\d+) updated \((\d+) (\S+) records\)$")

    @app.get("/admin/types/{slug}/import/done/{sha}", response_class=HTMLResponse)
    async def import_done(request: Request, slug: str, sha: str):
        """What an import did, read back from its commit (so the page can be reloaded and shared with other administrators)."""
        c.need_admin(request)
        if slug not in store.schemas or not re.fullmatch(r"[0-9a-f]{7,40}", sha):
            raise HTTPException(404, "unknown import")
        out = store.git("log", "-1", "--format=%s%x00%b", sha, check=False)
        subject, _, body = out.partition("\x00")
        m = SUBJECT.match(subject.strip())
        if not m or m.group(4) != slug:
            raise HTTPException(404, "unknown import")
        ids = [i.strip() for i in body.strip().removeprefix("Records:").split(",") if i.strip()]
        full = store.git("rev-parse", sha).strip()
        return page(request, "import_result.html", slug, created=int(m.group(1)), updated=int(m.group(2)), sha=full, shown_ids=ids[:200], total_ids=len(ids))

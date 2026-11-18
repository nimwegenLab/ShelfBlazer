"""Attach files to records: upload and delete (the download route lives in web.py)."""
from fastapi.responses import RedirectResponse
from starlette.datastructures import UploadFile
from starlette.requests import Request

from shelfblazer import uploads
from shelfblazer.store import Forbidden, NotFound, ValidationError

FILES_PER_REQUEST = 5
MAX_REQUEST_BYTES = FILES_PER_REQUEST * uploads.MAX_UPLOAD_BYTES + 1024 * 1024


def register(app, c):
    store = c.store

    @app.post("/r/{rid}/files")
    async def upload(request: Request, rid: str):
        user = c.need_user(request)
        rec = store.readable(user, rid)                # the size is checked by middleware.BodyLimit before this runs
        if rec is None:
            raise NotFound(rid)
        if not store.can_edit(user, rec):        # before the body is read: a refused upload never reaches the disk
            raise Forbidden(f"you may not change {rid}")
        form = await request.form()
        field = form.get("field") or None
        try:
            c.check_csrf(request, form)
            parts = [f for f in form.getlist("files") if isinstance(f, UploadFile) and f.filename]
            if not parts:
                c.flash(request, "Choose at least one file.", "err")
                return RedirectResponse(f"/r/{rid}", 303)
            stored, refused = [], []
            for part in parts[:FILES_PER_REQUEST]:
                content = await part.read(uploads.MAX_UPLOAD_BYTES + 1)
                try:
                    stored.append(store.add_attachment(rid, user, part.filename, content, field=field))
                except ValidationError as e:
                    refused.append(e.errors.get("file", "not accepted"))
        finally:
            await form.close()                   # release the temporary files of the upload
        if len(parts) > FILES_PER_REQUEST:
            refused.append(f"only the first {FILES_PER_REQUEST} files were taken")
        if stored:
            c.flash(request, f"Added {', '.join(stored)} to {rid}.")
        for msg in refused:
            c.flash(request, f"Not added: {msg}", "err")
        return RedirectResponse(f"/r/{rid}", 303)

    @app.post("/r/{rid}/files/delete")
    async def delete_file(request: Request, rid: str):
        user = c.need_user(request)
        form = await request.form()
        c.check_csrf(request, form)
        name = form.get("name", "")
        try:
            store.remove_attachment(rid, user, name, form.get("field") or None)
            c.flash(request, f"Removed {name} from {rid}.")
        except ValidationError as e:
            c.flash(request, e.errors.get("file", "not removed"), "err")
        return RedirectResponse(f"/r/{rid}", 303)

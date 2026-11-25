"""Barcode scanning, all in this module (plus static/scan.js, templates/scan.html and _scan_ui.html, the vendored decoder in static/vendor/zxing).

A type switches it on with ``"scan_fields": ["barcodes", "catalog_no"]`` in its definition: the text fields whose values are compared with
scanned codes (the first one also receives the codes on a new record). A field may hold several codes separated by commas, semicolons or
new lines. Matching ignores case, spaces, dots, hyphens and slashes, and the leading zeros of long digit strings, so the UPC-A, EAN-13 and
14-digit GTIN forms of one product are the same key, and "0030 073.495" equals "0030073495".

The browser decodes the camera image (the picture never leaves the device) and opens ``/scan?c=<text>``. ``keys`` turns each decoded string
into what identifies the *product*: a GS1 code also holds a lot and an expiry date, of which only the GTIN and the product identifiers are
kept. One record carrying a code opens at once; otherwise the page lists the matches, or offers to add the product with the code filled in
or to add the code to an existing entry (``/scan/link``: choose the entry, tick the codes to keep, save; lot numbers start unticked).
A USB reader that types the code into the home search and presses Enter works the same way (``active``).
"""
import re
from urllib.parse import quote

from fastapi import HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.requests import Request

from shelfblazer import ids
from shelfblazer.errors import ValidationError

SEPARATORS = re.compile(r"[,;\n]+")
GS1_ELEMENT = re.compile(r"\((\d{2,4})\)([^()]*)")                          # "(01)04038917133913(17)250430(10)220590103"
RAW_GTIN = re.compile(r"^01(\d{14})(?=$|\x1d|1[0-7]|2[01]|3\d|9\d|240)")    # the same without brackets: 01 + GTIN + the next element
PRODUCT_ELEMENTS = ("01", "240", "91")      # GTIN, additional product identification, company-internal (some suppliers: the catalog number)
MIN_KEY = 4                                 # shorter codes ("1") say nothing
MAX_CODES = 20
MAX_LENGTH = 300                            # per code; a product code is far shorter


# ---------------------------------------------------------------- the codes
def norm(code):
    """The comparison form of a code (see the module text)."""
    s = re.sub(r"[\s.\-/_]+", "", str(code)).casefold()
    return s.lstrip("0") if s.isdigit() and len(s) >= 8 else s


def keys(text):
    """What a scanned string says about the product, best first: the GTIN (and product identifiers) of a GS1 code, else the code itself.
    A string a decoder wrongly shows in GS1 brackets, such as Integra's plain part number 3717 as "(37)17", is read back as plain text."""
    text = (text or "").strip()
    if not text:
        return []
    if "(" in text:
        found = dict(GS1_ELEMENT.findall(text))
        return [found[a] for a in PRODUCT_ELEMENTS if found.get(a)] or [re.sub(r"[()]", "", text)]
    m = RAW_GTIN.match(text)
    return [m.group(1)] if m else [text]


def wanted(codes):
    """The normalised keys to look for: everything ``keys`` finds, plus the raw strings."""
    out = set()
    for c in list(codes)[:MAX_CODES]:
        for k in keys(c) + [str(c).strip()]:
            n = norm(k)
            if len(n) >= MIN_KEY:
                out.add(n)
    return out


def prefill(codes):
    """The text for a new record's first scan field: the product keys of the scanned codes, once each, comma separated."""
    out = []
    for c in list(codes)[:MAX_CODES]:
        for k in keys(c):
            if len(norm(k)) >= MIN_KEY and norm(k) not in [norm(o) for o in out]:
                out.append(k)
    return ", ".join(out)[:500]


def check_digit_ok(digits):
    """The GS1 check digit (last digit) of an EAN-8, UPC-A, EAN-13 or GTIN-14 is right."""
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def classify(text):
    """[(code, is_product, what)] for one scanned string, as static/scan.js does for the field Scan buttons: which codes say what the
    product is, which may be a lot number. Used to pre-tick the codes to keep."""
    t = (text or "").strip()
    if not t:
        return []
    if "(" in t:
        found = dict(GS1_ELEMENT.findall(t))
        out = [(found[a], True, "product code (GTIN), from a GS1 code" if a == "01" else "product number, from a GS1 code")
               for a in PRODUCT_ELEMENTS if found.get(a)]
        if out:
            return out
        t = re.sub(r"[()]", "", t)
    m = RAW_GTIN.match(t)
    if m:
        return [(m.group(1), True, "product code (GTIN), from a GS1 code; lot and date left out")]
    if re.fullmatch(r"\d{8}|\d{12,14}", t) and check_digit_ok(t):
        return [(t, True, "product code (EAN / UPC / GTIN)")]
    if t.startswith("+$"):
        return [(t, False, "lot or serial number (HIBC)")]
    if re.match(r"\+[A-Z]\d{3}", t):
        return [(t, True, "product code (HIBC)")]
    return [(t, False, "a REF or a lot number? Check against the box")]


def candidates(codes):
    """The codes a scan offers to add to a record, once each, as dicts (code, product, what, keep): with any product code among them,
    only the product codes are ticked; otherwise all are (the person checks them against the box)."""
    out, seen = [], set()
    for c in list(codes)[:MAX_CODES]:
        for code, product, what in classify(c):
            n = norm(code)
            if len(n) >= MIN_KEY and n not in seen:
                seen.add(n)
                out.append(dict(code=code[:MAX_LENGTH], product=product, what=what))
    any_product = any(x["product"] for x in out)
    for x in out:
        x["keep"] = x["product"] or not any_product
    return out


def merged(current, new):
    """The text of a scan field with the new codes appended (comma separated), leaving out the ones it already carries."""
    have = [p.strip() for p in SEPARATORS.split(current or "") if p.strip()]
    known = {norm(p) for p in have}
    for c in new:
        if c.strip() and norm(c) not in known:
            have.append(c.strip())
            known.add(norm(c))
    return ", ".join(have)


# ---------------------------------------------------------------- the records
def types(store, viewer):
    """(slug, name) of the types this person can read that are set up for scanning."""
    readable = store.readable_types(viewer)
    return [(s, sch["name"]) for s, sch in store.schemas.items() if s in readable and sch.get("scan_fields")]


def values(rec, schema):
    """The codes a record carries in its type's scan fields."""
    fields = schema.get("scan_fields")
    out = []
    for key in fields if isinstance(fields, list) else []:
        v = rec["fields"].get(key)
        if isinstance(v, str):
            out += [p.strip() for p in SEPARATORS.split(v) if p.strip()]
    return out


def find(store, viewer, codes):
    """The records this viewer may see that carry one of the scanned codes: active ones first, then by type and number."""
    want = wanted(codes)
    if not want:
        return []
    hits = [r for r in store.visible_records(viewer).values() if any(norm(v) in want for v in values(r, store.schemas[r["type"]]))]
    return sorted(hits, key=lambda r: (r["archived"], r["type"], ids.sort_key(r)))


def active(store, viewer, codes):
    """Like ``find`` without the archived records: what decides whether a scan opens a record at once (an archived duplicate does not hide the one in use)."""
    return [r for r in find(store, viewer, codes) if not r["archived"]]


def prefilled(schema, code):
    """The values for a new record's form when it is opened from a scan (``?code=``): the code in the type's first scan field."""
    fields = schema.get("scan_fields")
    return {fields[0]: code[:500]} if code and isinstance(fields, list) and fields else {}


# ---------------------------------------------------------------- the page
def register(app, c):
    store = c.store

    @app.get("/scan", response_class=HTMLResponse)
    async def scan_result(request: Request, codes: list[str] = Query(default=[], alias="c")):
        user = c.need_user(request)
        codes = [x.strip()[:MAX_LENGTH] for x in codes[:MAX_CODES] if x.strip()]
        hits = find(store, user, codes)
        opens = [h for h in hits if not h["archived"]]
        if len(opens) == 1:
            return RedirectResponse(f"/r/{opens[0]['id']}", 303)
        scannable = types(store, user)
        add = [(s, n) for s, n in scannable if store.can_create(user, s)]
        return c.render(request, "scan.html", codes=codes, hits=hits, add=add, prefill=prefill(codes), types=len(scannable),
                        link_types=",".join(s for s, _ in scannable))

    def link_target(user, rid):
        """The record a code is to be added to, or an explanation why it cannot be (shown on the page)."""
        rec = store.readable(user, store.resolve((rid or "").strip(), user["uid"]))
        if not rec:
            return None, f"There is no entry {rid.strip()}." if (rid or "").strip() else "Choose an entry first."
        fields = store.schemas[rec["type"]].get("scan_fields")
        if not (isinstance(fields, list) and fields):
            return rec, f"{rec['id']} is a {store.schemas[rec['type']]['name'].lower()}, which has no barcode field."
        if not store.can_edit(user, rec):
            return rec, c.edit_refusal(rec) + " Ask them to add the code."
        return rec, None

    @app.get("/scan/link", response_class=HTMLResponse)
    async def scan_link(request: Request, rid: str = "", codes: list[str] = Query(default=[], alias="c")):
        """Second step of "add this code to an existing entry": the chosen record, the codes to add (lot numbers unticked), Save."""
        user = c.need_user(request)
        codes = [x.strip()[:MAX_LENGTH] for x in codes[:MAX_CODES] if x.strip()]
        rec, refusal = link_target(user, rid)
        field = (store.schemas[rec["type"]].get("scan_fields") or [None])[0] if rec and not refusal else None
        have = {norm(v) for v in values(rec, store.schemas[rec["type"]])} if field else set()
        items = [dict(x, present=norm(x["code"]) in have) for x in candidates(codes)]
        label = field and next((f["label"] for f in store.schemas[rec["type"]]["fields"] if f["key"] == field), field)
        return c.render(request, "scan_link.html", codes=codes, rid=rid, rec=rec, refusal=refusal, items=items, field_label=label,
                        back="/scan?" + "&".join("c=" + quote(x, safe="") for x in codes))

    @app.post("/scan/link")
    async def scan_link_save(request: Request):
        user = c.need_user(request)
        form = await request.form()
        c.check_csrf(request, form)
        rec, refusal = link_target(user, str(form.get("rid", "")))
        if refusal or not rec:
            raise HTTPException(403 if rec else 404, refusal or "no such entry")
        add = [str(x).strip()[:MAX_LENGTH] for x in form.getlist("code")[:MAX_CODES] if str(x).strip()]
        if not add:
            c.flash(request, "No code was ticked: nothing was added.", "error")
            return RedirectResponse(f"/r/{rec['id']}", 303)
        field = store.schemas[rec["type"]]["scan_fields"][0]
        new = merged(rec["fields"].get(field) if isinstance(rec["fields"].get(field), str) else "", add)
        noun = "barcode" if len(add) == 1 else "barcodes"
        try:
            changed = store.set_field(rec["id"], user, field, new, f"{rec['id']}: {noun} added from a scan by {user['uid']}")
        except ValidationError as e:
            raise HTTPException(400, "; ".join(f"{k}: {v}" for k, v in e.errors.items()))
        c.flash(request, f"{noun.capitalize()} {', '.join(add)} added to {rec['id']}." if changed else f"{rec['id']} already carries {'this code' if len(add) == 1 else 'these codes'}.")
        return RedirectResponse(f"/r/{rec['id']}", 303)

"""Display helpers (pure functions, no web code)."""
import re

_BOX = re.compile(r"^box '(.*)'$", re.IGNORECASE)
_WELL = re.compile(r"^[A-Za-z]{1,2}\d{1,2}$")
_NUM = re.compile(r"box\s*(\d+)", re.IGNORECASE)          # "(box 2)", "Box2", "box3"
_LETTER = re.compile(r"box\s*([A-Za-z])(?![A-Za-z0-9])", re.IGNORECASE)   # "F1_rack11_BoxE" -> E (not "box collection")


def _box_label(name):
    m = _NUM.search(name)
    if m:
        return f"Box {int(m.group(1))}"
    m = _LETTER.search(name)
    if m:
        return f"Box {m.group(1).upper()}"
    return name.strip()


def _wells(pos):
    cells = [c.strip() for c in pos.split(",") if c.strip()]
    if not cells or not all(_WELL.match(c) for c in cells):
        return None
    if len(cells) == 1:
        return f"Well {cells[0]}"
    if len(cells) <= 3:
        return "Wells " + ", ".join(cells)
    return f"Wells {cells[0]} … {cells[-1]} ({len(cells)})"


def _one(text, with_freezer):
    segs = [s.strip() for s in text.split(" / ")]
    for i, seg in enumerate(segs):
        m = _BOX.match(seg)
        if not m:
            continue
        name = m.group(1).strip()
        if not name:
            return ""
        out = _box_label(name)
        pos = segs[i + 1] if i + 1 < len(segs) else ""
        wells = _wells(pos) if pos else None
        if wells:
            out += " " + wells
        elif pos:
            out += " " + pos
        if with_freezer and len(segs) > 1 and segs[0].lower().startswith("room"):
            out += f" ({segs[1]})"
        return out
    return text.strip()   # not a box path (for example "legacy box 111 (missing)" or text typed by a person): leave alone


def short_storage(text):
    """'Room 6035 / Freezer 1 -80 / ... / box 'Strains collec (box 2)' / G3'  ->  'Box 2 Well G3'.

    Several locations ('...; also: ...') are each abbreviated; the freezer is added in brackets only when they are in different freezers.
    Anything that is not a box path is returned unchanged. The stored text is never modified; this is display only.
    """
    if not text or not isinstance(text, str):
        return text
    parts = [p for p in text.split("; also: ")]
    freezers = {p.split(" / ")[1].strip() for p in parts if p.count(" / ")}
    show_freezer = len(parts) > 1 and len(freezers) > 1      # only when the locations are in different freezers
    out = [x for x in (_one(p, show_freezer) for p in parts) if x]
    return "; also ".join(out) if out else text


def storage_chunks(text):
    """The short storage text in pieces that should not be broken inside, for wrapping in narrow list columns:
    'Box 2 Well C5 (Freezer 1 -80); also Box 2 Well A7 (Freezer 2 -80)' ->
    ['Box 2 Well C5', '(Freezer 1 -80);', 'also Box 2 Well A7', '(Freezer 2 -80)']. Anything else is one piece."""
    if not text or not isinstance(text, str):
        return [text] if text else []
    locations = text.split("; also ")
    out = []
    for i, loc in enumerate(locations):
        m = re.match(r"^(.*\S) (\([^()]*\))$", loc)
        pieces = [m.group(1), m.group(2)] if m else [loc]
        if i:
            pieces[0] = "also " + pieces[0]
        if i < len(locations) - 1:
            pieces[-1] += ";"
        out += pieces
    return out

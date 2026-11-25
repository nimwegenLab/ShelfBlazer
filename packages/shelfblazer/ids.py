"""Record IDs: the one place that knows what an ID looks like.

An ID is a type's two-letter prefix, a hyphen and a number without zero padding: ``PL-142`` (design D5). A record in a person's
collection has a ``P`` before its number, ``ST-P17``, and each person counts their own: Alice's ``ST-P1`` and Bob's ``ST-P1`` are
different records (design section 17.2). What stays unique is the ID together with its owner, so the key of a personal record, the one
its file, its links and its address use, carries the owner's user ID: ``ST-P17~U7F3K2QA``. People see and type the short form
(``ST-P17``; ``ST-P17[Alice]`` when it is someone else's): a short personal ID means "in the collection of the person it is read for"
(``qualify``). Whether a record is personal is read from its ID, never stored apart. Everything that reads or writes IDs (the store,
links in notes, the activity list, renaming a prefix, the pages and the browser's record picker) uses this module, so a change to the
ID grammar is made here once.
"""
import re
from typing import NamedTuple

_OWNER = r"[A-Za-z0-9]{3,16}"                          # a user ID (U7F3K2QA); loose so that test accounts with plain names work too
PATTERN = rf"[A-Z]{{2}}-(?:P\d+(?:~{_OWNER})?|\d+)"       # an ID, no groups, not anchored
_BODY = rf"([A-Z]{{2}})-(P)?(\d+)(?(2)(?:~({_OWNER}))?)"   # an owner only after a P: a lab ID never has one
FULL = re.compile(rf"^{_BODY}$")                       # a whole string that is an ID; groups: prefix, "P" or None, number, owner or None
IN_TEXT = re.compile(rf"(?<![A-Za-z0-9_]){_BODY}\b")   # IDs inside text; same groups
JS_FULL = rf"^[A-Z]{{2}}-(P\d+(~{_OWNER})?|\d+)$"       # FULL for the browser (static/picker.js reads it from the page)


class RecordId(NamedTuple):
    prefix: str
    number: int
    personal: bool = False
    owner: str = None                                  # only in the key of a personal record


def parse(text):
    """The parts of an ID, ignoring surrounding spaces and the case of its prefix; None for anything else."""
    head, tilde, owner = (text or "").strip().partition("~")
    m = FULL.match(head.upper() + (tilde + owner if tilde else ""))
    return RecordId(m.group(1), int(m.group(3)), m.group(2) == "P", m.group(4)) if m else None


def make(prefix, number, personal=False, owner=None):
    """An ID. A personal one with an ``owner`` is a key (``ST-P17~U7F3K2QA``); without, the short form (``ST-P17``)."""
    return f"{prefix}-{'P' if personal else ''}{number}" + (f"~{owner}" if personal and owner else "")


def is_personal(rid):
    """True for the ID of a record in a person's collection (``ST-P17``). Cheap: it runs for every record on every page."""
    return isinstance(rid, str) and len(rid) > 4 and rid[2] == "-" and rid[3] == "P"


def owner_of(rid):
    """The user ID in the key of a personal record, else None (a lab ID, or a short personal ID)."""
    return rid.partition("~")[2] or None if is_personal(rid) and isinstance(rid, str) else None


def short(rid):
    """The ID as people write it: the key without its owner (``ST-P17~U7F3K2QA`` -> ``ST-P17``)."""
    return rid.partition("~")[0] if isinstance(rid, str) else rid


def qualify(rid, owner):
    """The key for an ID read in the context of ``owner``: a short personal ID gets that owner, anything else is returned as it is."""
    return f"{rid}~{owner}" if owner and is_personal(rid) and "~" not in rid else rid


def label(rid, viewer_uid, name):
    """What a person sees for an ID: the short form, with ``[name]`` after it when it is someone else's personal record."""
    owner = owner_of(rid)
    return short(rid) + (f"[{name(owner)}]" if owner and owner != viewer_uid else "")


def prefix_of(rid):
    """The prefix part of an ID-like string (upper case), or None: also for strings that are not valid IDs, such as a folder name."""
    head, sep, _ = str(rid or "").partition("-")
    return head.upper() if sep and head else None


def mentions(prefix):
    """A pattern for the IDs of one prefix inside text, personal ones included (group 1: "P" and the number), for rewriting them."""
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(prefix)}-(P?\d+)\b")


def sort_key(rec):
    """The order of records by ID within a type: the lab collection first, then personal records; by number within each (then owner)."""
    n = rec.get("legacy_id")
    return (1 if is_personal(rec.get("id")) else 0, n if isinstance(n, int) else 0, owner_of(rec.get("id")) or "")

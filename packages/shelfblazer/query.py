"""Which records a list shows: one description of a selection (``Query``) and one function that runs it (``select``).

The list page, the home search and the record picker all select records through here, so a new filter (for example the collection a
record is in) is added once. ``select`` always applies the viewer's visibility (``Store.visibility``); the filters only narrow further.
Sorting by a column and paging stay with the page that shows the records.
"""
from dataclasses import dataclass

from . import ids, seqsearch


@dataclass(frozen=True)
class Query:
    type: str = None            # one type (a list page); None: every type
    types: frozenset = None     # or a set of types (the picker); None: no limit
    text: str = ""              # words, IDs or codes; or a DNA sequence when ``sequence``
    fuzzy: bool = True          # also close spellings when there are few matches
    sequence: bool = False      # ``text`` is a sequence, searched in the type's sequence field
    mismatches: int = 0
    owner: str = ""             # a user ID: only that person's records
    archived: bool = False      # include the archived records the viewer may see
    collection: str = "lab"     # "lab" (default), "personal" (every visible personal collection; with ``owner``, one person's),
                                # "lab+mine" (the lab and the viewer's own: what a form of a personal record may link to) or "any"


def sequence_field(store, slug):
    """The field a type's sequence search looks in (its "sequence_search" setting), or None."""
    schema = store.schemas.get(slug) or {}
    key = schema.get("sequence_search")
    return key if any(f["key"] == key for f in schema.get("fields", [])) else None


def in_collection(viewer, rec, collection):
    """Whether a record belongs to the named collection selection (see ``Query.collection``)."""
    personal = ids.is_personal(rec["id"])
    return {"lab": not personal, "personal": personal, "any": True,
            "lab+mine": not personal or rec["created_by"] == viewer["uid"]}.get(collection, not personal)


def select(store, index, viewer, query):
    """(records, owners): the records the viewer may see that match the query, in relevance order when there is text to match and in
    no particular order otherwise; ``owners`` are the user IDs owning the matches before the owner filter (for the owner menu)."""
    visible = store.visibility(viewer)

    def in_collection_(r):
        return in_collection(viewer, r, query.collection)

    def in_scope(r):
        return ((query.type is None or r["type"] == query.type) and (query.types is None or r["type"] in query.types)
                and in_collection_(r) and visible(r))
    text = query.text.strip()
    seq_key = sequence_field(store, query.type) if query.sequence and query.type else None
    if text and seq_key:
        recs = [r for r, _ in seqsearch.find([r for r in store.records.values() if in_scope(r)], seq_key, text, query.mismatches)]
    elif text:
        recs = [store.records[d["id"]] for d in index.search(text, include_archived=query.archived, type_slug=query.type, types=query.types,
                                                             fuzzy=query.fuzzy, visible=visible)]
        recs = [r for r in recs if in_collection_(r)]
    else:
        recs = [r for r in store.records.values() if in_scope(r)]
    if not query.archived:
        recs = [r for r in recs if not r["archived"]]
    owners = {r["created_by"] for r in recs}
    if query.owner:
        recs = [r for r in recs if r["created_by"] == query.owner]
    return recs, owners

"""Sequence search (a lab-specific extra, switched on per type with "sequence_search": "<field key>").

Finds records whose sequence field contains the query, on either strand, allowing a few mismatches (substitutions only,
like the "mismatches" setting of Benchling's sequence search). Spaces, line breaks, FASTA header lines and case are ignored;
U counts as T. Nothing here knows about records or the web app beyond ``find``, so it can be taken out without touching the rest.
"""
import re

MIN_LENGTH = 6          # shorter queries match almost everything
MAX_MISMATCHES = 5
_COMP = str.maketrans("acgtrykmbdhvn", "tgcayrmkvhdbn")


def clean(text):
    """The bases of a pasted sequence: lower case, no white space, no FASTA header lines, U read as T."""
    lines = [ln for ln in (text or "").split("\n") if not ln.lstrip().startswith(">")]
    return re.sub(r"\s+", "", "".join(lines)).lower().replace("u", "t")


def revcomp(seq):
    return seq.lower().translate(_COMP)[::-1]


def allowed_mismatches(query, wanted):
    """The mismatch count actually used: between 0 and MAX_MISMATCHES, and at most a third of the query."""
    return max(0, min(int(wanted or 0), MAX_MISMATCHES, len(query) // 3))


def mismatches_at(query, text, pos, limit):
    """Number of differing positions of query against text[pos:], or None as soon as it exceeds limit."""
    n = 0
    for a, b in zip(query, text[pos:pos + len(query)]):
        if a != b:
            n += 1
            if n > limit:
                return None
    return n


def best(query, text, k):
    """The fewest mismatches with which query occurs in text (at most k), or None. Exact pieces of the query locate the candidates
    (a query with k mismatches always contains one of k+1 pieces unchanged), which are then checked."""
    if len(query) > len(text):
        return None
    if k == 0:
        return 0 if query in text else None
    size = len(query) // (k + 1)
    found = None
    for i in range(k + 1):
        start = i * size
        piece = query[start:start + size] if i < k else query[start:]
        pos = text.find(piece)
        while pos != -1:
            at = pos - start
            if 0 <= at <= len(text) - len(query):
                m = mismatches_at(query, text, at, k if found is None else found - 1)
                if m is not None:
                    found = m
                    if found == 0:
                        return 0
            pos = text.find(piece, pos + 1)
    return found


def find(records, key, query, mismatches=0):
    """[(record, mismatches)] for the records whose ``key`` field contains the query on either strand, fewest mismatches first."""
    q = clean(query)
    if len(q) < MIN_LENGTH or re.search(r"[^acgtrykmbdhvn]", q):
        return []
    k = allowed_mismatches(q, mismatches)
    rc = revcomp(q)
    out = []
    for r in records:
        text = clean(r["fields"].get(key) or "")
        if not text:
            continue
        hits = [m for m in (best(q, text, k), best(rc, text, k) if rc != q else None) if m is not None]
        if hits:
            out.append((r, min(hits)))
    out.sort(key=lambda x: (x[1], x[0]["id"]))
    return out


def usable(query):
    """True when the query is a sequence long enough to search for (the list page says so when it is not)."""
    q = clean(query)
    return len(q) >= MIN_LENGTH and not re.search(r"[^acgtrykmbdhvn]", q)

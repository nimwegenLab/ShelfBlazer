"""In-memory search index (about 2,500 records): token AND matching, ID boosts, typo tolerance via RapidFuzz."""
import re, unicodedata

from rapidfuzz.distance import OSA  # edit distance where a swap of adjacent letters counts as one edit

from . import fields as fieldrules, ids

ID_LIKE = re.compile(r"[a-z]{2}-?\d+")


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).lower()
    return "".join(c for c in s if not unicodedata.combining(c))


class Index:
    def __init__(self, store, users_lookup):
        self.store, self.users_lookup = store, users_lookup
        self.docs = {}
        self.rebuild()
        store.on_change.append(self._changed)

    def _doc(self, r):
        owner = self.users_lookup(r["created_by"]) if ids.is_personal(r["id"]) else None
        parts = [ids.short(r["id"]), r["name"], r["notes"][:600]] + ([owner["name"]] if owner else [])     # "ST-P3" and the owner's name find a personal record
        skip = {f["key"] for f in self.store.schemas.get(r["type"], {}).get("fields", [])
                if f.get("hidden") or fieldrules.is_sequence(f)}         # sequences have their own search (seqsearch)
        parts += [" ".join(map(str, v)) if isinstance(v, list) else str(v) for k, v in r["fields"].items()
                  if k not in skip and not isinstance(v, bool)]
        return {"id": r["id"], "type": r["type"], "name": r["name"], "n": norm(r["name"]), "archived": r["archived"], "owner": r["created_by"],
                "text": norm(" ".join(parts)), "words": None, "num": ids.sort_key(r)}

    def rebuild(self):
        self.docs = {rid: self._doc(r) for rid, r in self.store.records.items()}

    def _changed(self, rid):
        if rid is None:                     # many records changed at once (for example after an undo)
            self.rebuild()
            return
        r = self.store.records.get(rid)
        if r:
            self.docs[rid] = self._doc(r)
        else:
            self.docs.pop(rid, None)

    def search(self, q, include_archived=False, type_slug=None, types=None, fuzzy=True, visible=None):
        """Records matching ``q``, best first. ``visible`` is the viewer's predicate (``Store.visibility``): records it rejects are never
        returned. ``include_archived`` adds the archived records the viewer may see; ``type_slug`` and ``types`` narrow to some types."""
        tokens = norm(q).split()
        if not tokens:
            return []
        scored = []
        for d in self.docs.values():
            if self._hidden(d, include_archived, visible) or (type_slug and d["type"] != type_slug) or (types is not None and d["type"] not in types):
                continue
            if all(t in d["text"] for t in tokens):
                scored.append((self._score(d, tokens), d["num"], d))
        if fuzzy and len(scored) < 5:
            seen = {d["id"] for _, _, d in scored}
            for d in self.docs.values():
                if d["id"] in seen or self._hidden(d, include_archived, visible) or (type_slug and d["type"] != type_slug) or (types is not None and d["type"] not in types):
                    continue
                if all(t in d["text"] or self._fuzzy(d, t) for t in tokens):
                    scored.append((1, d["num"], d))
        scored.sort(key=lambda x: (-x[0], x[2]["type"], x[1]))
        return [d for _, _, d in scored]

    def _hidden(self, d, include_archived, visible):
        return (d["archived"] and not include_archived) or (visible is not None and not visible(self.store.records[d["id"]]))

    @staticmethod
    def _score(d, tokens):
        s, did = 0, d["id"].lower()
        for t in tokens:
            if did == t: s += 1000
            elif did.startswith(t): s += 300
            elif d["n"].startswith(t): s += 80
            elif t in d["n"]: s += 40
            else: s += 5
        return s

    @staticmethod
    def _fuzzy(d, token):
        if len(token) < 4 or ID_LIKE.fullmatch(token):                      # an ID such as st-889 is never "close to" another ID
            return False
        if d["words"] is None:
            d["words"] = list(dict.fromkeys(w for w in re.split(r"[^a-z0-9]+", d["text"]) if len(w) >= 4))[:400]
        m = 2 if len(token) >= 8 else 1
        return any(OSA.distance(token, w[: len(token) + m], score_cutoff=m) <= m for w in d["words"])

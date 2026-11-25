"""How record IDs are shown to people (design section 17.2).

A personal record's key carries its owner (``ST-P17~U7F3K2QA``); that is what files, links and addresses use. People see ``ST-P17``,
and ``ST-P17[Alice]`` when it is someone else's (``shelfblazer.ids.label``). Pages are written with the keys and this module turns the
keys in the *visible* text of a finished page into labels: text between tags, and the title, aria-label, alt and placeholder of a tag;
never an address (href, action), a value or a data attribute, nor script, style or the hidden text of an import. One place, so that no
template has to remember it; ``tests/test_visibility_matrix.py`` and ``test_collections_app.py`` check that no key leaks into the text.
"""
import re

from shelfblazer import ids

_OPAQUE = re.compile(r"(<script\b.*?</script>|<style\b.*?</style>|<textarea\b[^>]*\bhidden\b[^>]*>.*?</textarea>)", re.S | re.I)
_TAG = re.compile(r"(<[^>]+>)")
_ATTR = re.compile(r'(\b(?:title|aria-label|alt|placeholder)=")([^"]*)(")')


def labels(html, viewer_uid, name):
    """``html`` with the keys of personal records in its visible text shown as labels for this viewer; ``name(uid)`` gives a display name."""
    if "~" not in html:
        return html

    def text(t):
        return ids.IN_TEXT.sub(lambda m: ids.label(m.group(0), viewer_uid, name) if m.group(4) else m.group(0), t)

    def tag(t):
        return _ATTR.sub(lambda m: m.group(1) + text(m.group(2)) + m.group(3), t)
    out = []
    for block in _OPAQUE.split(html):
        if _OPAQUE.fullmatch(block):
            out.append(block)
            continue
        out.append("".join(tag(part) if part.startswith("<") else text(part) for part in _TAG.split(block)))
    return "".join(out)

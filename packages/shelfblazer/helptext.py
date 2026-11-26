"""The lab's own section of the Help page: ``config/help.md`` in the data repository.

Markdown (the subset of ``markdown.py``). Its first heading, a line starting with ``# ``, is the title of the section on the Help
page; the rest is its text. No file, or an empty one, means no section. Administrators edit it on the Admin pages; saving is one commit.
"""
import os

from . import perms
from .errors import Forbidden, ValidationError
from .recordfile import unix_newlines, write_text

PATH = ("config", "help.md")
MAX_CHARS = 20000


def split(text):
    """(title, body) of a help text: the first ``# `` line is the title (None when the text has none), the rest is the body."""
    lines = unix_newlines(text).split("\n")
    for i, line in enumerate(lines):
        if line.strip():
            if line.startswith("# "):
                return line[2:].strip() or None, "\n".join(lines[i + 1:]).strip()
            break
    return None, "\n".join(lines).strip()


def read(store):
    """The text of ``config/help.md`` ("" when there is none). Read on every request: it is small and may be changed outside the app."""
    try:
        with open(os.path.join(store.repo, *PATH), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def save(store, text, actor):
    """Write ``config/help.md`` as one commit (administrators only). An empty text removes the section."""
    if not perms.is_admin(actor):
        raise Forbidden("only administrators may change the help text")
    text = unix_newlines(text).strip()
    if len(text) > MAX_CHARS:
        raise ValidationError({"help": f"the text has {len(text)} characters; at most {MAX_CHARS} are kept"})
    path = os.path.join(store.repo, *PATH)
    with store._write_lock():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_text(path, text + "\n" if text else "")
        store.commit([path], "help text: change" if text else "help text: remove", actor)

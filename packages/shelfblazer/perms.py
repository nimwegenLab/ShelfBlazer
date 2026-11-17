"""The permission rules in one place (see docs/design.md section 5 and D26).

``actor`` is a user dict with at least ``uid`` (the stable user ID records store as ``created_by``), ``role`` ('user' | 'admin') and ``active``.

Defaults (what happens when a type definition has no ``permissions``): everyone signed in can read and create records of the type;
a record can be edited, archived and restored only by its creator or an administrator.
There is no delete: git keeps every version anyway, so removing a record means archiving it. An archived record is hidden from
everybody except its creator and administrators (see ``can_see_archived``); restoring it brings it back.

A type definition may change this with roles only (never with named people):

    "permissions": {"read": "all" | "admin", "create": "all" | "admin", "edit": "owner" | "admin" | "all"}

* read = admin: only administrators see the type at all (it is absent from menus, searches, lists, links and downloads).
* create = admin: only administrators add records.
* edit = admin: only administrators change and archive records.  edit = all: every signed-in reader may change and archive
  records (whoever archives a record someone else created no longer sees it; its creator and administrators do).
Administrators can do everything with every type. Nobody can edit what they cannot read.
"""

DEFAULTS = {"read": "all", "create": "all", "edit": "owner"}
CHOICES = {"read": ("all", "admin"), "create": ("all", "admin"), "edit": ("owner", "admin", "all")}


def is_admin(actor):
    return bool(actor) and actor.get("active", True) and actor.get("role") == "admin"


def can_read(actor):
    """Signed in and active (says nothing about a particular type)."""
    return bool(actor) and actor.get("active", True)


def setting(schema, action):
    value = ((schema or {}).get("permissions") or {}).get(action)
    return value if value in CHOICES[action] else DEFAULTS[action]


def restricted(schema):
    """True when the type has any non-default permission (used to skip filtering work when nothing is restricted)."""
    return any(setting(schema, a) != DEFAULTS[a] for a in DEFAULTS)


def can_read_type(actor, schema):
    if not can_read(actor):
        return False
    return is_admin(actor) or setting(schema, "read") == "all"


can_read_record = can_read_type          # records follow their type


def can_create(actor, schema):
    if not can_read_type(actor, schema):
        return False
    return is_admin(actor) or setting(schema, "create") == "all"


def can_edit(actor, rec, schema):
    """Change, archive and restore a record, and manage its files."""
    if not can_read_type(actor, schema):
        return False
    if is_admin(actor):
        return True
    mode = setting(schema, "edit")
    return mode == "all" or (mode == "owner" and rec.get("created_by") == actor["uid"])


def can_see_archived(actor, rec):
    """An archived record is visible to its creator and to administrators only (the caller has checked the type is readable)."""
    return is_admin(actor) or (can_read(actor) and rec.get("created_by") == actor["uid"])


def can_manage_users(actor):
    return is_admin(actor)


def can_manage_types(actor):
    return is_admin(actor)

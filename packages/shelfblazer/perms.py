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

Personal collections (design section 17): ``"permissions": {"personal": "off" | "all" | "admin"}`` (default off) lets people keep records of
the type in their own collection (IDs like ``ST-P17``, see ids.py). A personal record is changed, archived and restored by its owner and
administrators only, whatever ``edit`` says. Others who can read the type see it unless the owner unchecked "Visible to others" for the
type; that hides it in the app only (the repository and its backups still hold it).
"""
from . import ids

DEFAULTS = {"read": "all", "create": "all", "edit": "owner"}
CHOICES = {"read": ("all", "admin"), "create": ("all", "admin"), "edit": ("owner", "admin", "all")}
PERSONAL_CHOICES = ("off", "all", "admin")                   # "permissions.personal"; kept apart from the three rules above


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


def personal_setting(schema):
    value = ((schema or {}).get("permissions") or {}).get("personal")
    return value if value in PERSONAL_CHOICES else "off"


def can_create_personal(actor, schema):
    """Add records of this type to one's own collection."""
    mode = personal_setting(schema)
    return can_read_type(actor, schema) and (mode == "all" or (mode == "admin" and is_admin(actor)))


def can_create_type_records(actor, schema, personal):
    """Add records of this type to the chosen collection: one's own (``personal``) or the lab's."""
    return can_create_personal(actor, schema) if personal else can_create(actor, schema)


def owns_or_admin(actor, rec):
    return is_admin(actor) or (can_read(actor) and rec.get("created_by") == actor.get("uid"))


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
    if ids.is_personal(rec.get("id")):
        return rec.get("created_by") == actor["uid"]
    mode = setting(schema, "edit")
    return mode == "all" or (mode == "owner" and rec.get("created_by") == actor["uid"])


def can_see_record(actor, rec, schema, shared=lambda: True):
    """THE rule for reading a record, applied on every read path (pages, search, links, formulas, files, history, export):
    its type must be readable; an archived record is seen by its creator and administrators only; so is a personal record whose
    owner hides that collection from others. ``shared()`` answers whether the owner's collection of this type is visible to others
    (asked only when it matters)."""
    if not can_read_type(actor, schema):
        return False
    if rec.get("archived") and not can_see_archived(actor, rec):
        return False
    return not ids.is_personal(rec.get("id")) or owns_or_admin(actor, rec) or shared()


def can_see_archived(actor, rec):
    """An archived record is visible to its creator and to administrators only (the caller has checked the type is readable)."""
    return owns_or_admin(actor, rec)


def can_manage_users(actor):
    return is_admin(actor)


def can_manage_types(actor):
    return is_admin(actor)

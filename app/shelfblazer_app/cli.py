"""Command line: initialise the data repo and instance, import user stubs, set passwords, run the server.

    shelfblazer init-data --empty  # a new empty instance: <home>/data with a starter type, and an admin account
    shelfblazer init-data          # records git repo from <home>/import_out into <home>/data
    shelfblazer import-users       # account stubs from <home>/import_out/users_stubs.yaml into <home>/instance
    shelfblazer set-password admin
    shelfblazer set-passphrase
    shelfblazer run --port 8000
    shelfblazer drop-field document category --yes     # maintenance: remove a field everywhere (one commit)
    shelfblazer rename-prefix strain BS --yes          # maintenance: new ID prefix for a type, ST-12 -> BS-12 (one commit; without --yes a preview)
    shelfblazer lfs-setup          # store attached files with Git LFS in an existing data repo (one commit)
    shelfblazer erase-user alice --yes                 # right to erasure: remove the account, keep its user ID as a tombstone

Everything the server needs lives under one runtime home (default ``var/``, or $SHELFBLAZER_HOME, or --home):
``<home>/data`` (records), ``<home>/instance`` (users, secret key). --data / --instance override the single folders.
"""
import argparse, getpass, glob, json, os, re, shutil, subprocess, sys

from shelfblazer.auth import Users, check_password_strength

from . import __version__


# The one type a new empty instance starts with; administrators add, change and remove types on the Types page.
STARTER_TYPE = {"name": "Item", "prefix": "IT", "color": "blue", "icon": "box", "list_columns": ["id", "name", "location"],
                "fields": [{"key": "name", "label": "Name", "type": "text", "required": True},
                           {"key": "location", "label": "Location", "type": "select", "source": "storage_places"}]}


def init_repo(dst, no_lfs, author, message):
    """git init in dst (attached files in Git LFS unless no_lfs) and one commit of everything in it. Removes dst if LFS is wanted but missing."""
    if not no_lfs:                                       # attached files go to Git LFS from the first commit
        from shelfblazer.store import LFS_ATTRIBUTES, StoreError, lfs_available
        try:
            lfs_available()
        except StoreError as e:
            shutil.rmtree(dst)
            sys.exit(f"{e}. Install it, or add --no-lfs to keep files in plain git.")
        with open(os.path.join(dst, ".gitattributes"), "w", encoding="utf-8") as fh:
            fh.write(LFS_ATTRIBUTES)
    env = dict(os.environ, GIT_AUTHOR_NAME=author, GIT_AUTHOR_EMAIL="shelfblazer@localhost",
               GIT_COMMITTER_NAME=author, GIT_COMMITTER_EMAIL="shelfblazer@localhost")
    for args in (["init", "-q", "-b", "main"], *([] if no_lfs else [["lfs", "install", "--local"]]), ["add", "-A"], ["commit", "-q", "-m", message]):
        subprocess.run(["git", "-C", dst, *args], check=True, env=env, stdout=subprocess.DEVNULL)
    print(f"data repository created at {dst}" + ("" if no_lfs else " (attached files in Git LFS)"))


def cmd_init_data(a):
    dst = os.path.abspath(a.to)
    if a.empty:
        if a.src_given:
            sys.exit("--empty and --from cannot be combined")
        return init_empty(a, dst)
    src = os.path.abspath(a.src)
    if not (os.path.isdir(os.path.join(src, "data")) and os.path.isdir(os.path.join(src, "config"))):
        sys.exit(f"{src} does not look like an import folder (needs config/ and data/); use --empty to start without data")
    if os.path.exists(dst) and os.listdir(dst):
        sys.exit(f"{dst} exists and is not empty")
    os.makedirs(dst, exist_ok=True)
    for d in ("config", "data"):                          # import_out/legacy (old tables, unlinked files) stays out: the old system's backup keeps it
        shutil.copytree(os.path.join(src, d), os.path.join(dst, d))
    users = Users(os.path.join(a.instance, "users.json"))
    stubs = os.path.join(src, "users_stubs.yaml")
    if os.path.exists(stubs):
        made, _, _ = import_stubs(users, stubs)
        if made:
            print(f"user stubs created: {made} (in {a.instance})")
    converted, added = owners_to_uids(dst, users)
    print(f"owners written as user IDs in {converted} records" + (f"; accounts created for unknown owners: {', '.join(added)}" if added else ""))
    init_repo(dst, a.no_lfs, "ShelfBlazer import", "Initial import")


def init_empty(a, dst):
    """A new instance without data: one starter type and an administrator account (no password yet)."""
    if os.path.exists(dst) and os.listdir(dst):
        sys.exit(f"{dst} exists and is not empty")
    types = os.path.join(dst, "config", "types")
    os.makedirs(types, exist_ok=True)
    os.makedirs(os.path.join(dst, "data"), exist_ok=True)
    with open(os.path.join(types, "item.yaml"), "w", encoding="utf-8") as fh:
        json.dump(STARTER_TYPE, fh, indent=2)
        fh.write("\n")
    init_repo(dst, a.no_lfs, "ShelfBlazer init", "Initial empty data repository")
    users = Users(os.path.join(a.instance, "users.json"))
    if not users.get("admin"):
        users.create("admin", "Administrator", "admin", password=None, active=True)
        print(f"account 'admin' created in {a.instance} (no password yet)")
    print("next: shelfblazer set-password admin, then shelfblazer run")


def import_stubs(users, path, create=True):
    """Accounts (without passwords) for the importer's user stubs. Existing accounts are left alone, except that an imported one
    (it has a LabCollector ID) gets the creation date the importer found. With create=False nobody is created (people erased or
    left out on purpose do not come back). -> (created, already present, creation dates set)"""
    made = skipped = dated = 0
    with open(path, encoding="utf-8") as fh:
        stubs = json.load(fh)
    for s in stubs:
        have = users.get(s["username"])
        if have:
            skipped += 1
            if s.get("created") and (have.get("legacy") or {}).get("admin_id") and users.set_created(s["username"], s["created"]):
                dated += 1
            continue
        if not create:
            continue
        users.create(s["username"], s.get("name") or s["username"], s.get("role", "user"), password=None, active=True,
                     legacy={"admin_id": s.get("legacy_admin_id"), "contact_id": s.get("legacy_contact_id")}, created=s.get("created"))
        made += 1
    return made, skipped, dated


LINE = re.compile(r'^( *)(\w+): (.*)$')


def owners_to_uids(repo, users):
    """Importer output names people by username; the data repo stores user IDs (docs/design.md 16.2). Rewrites created_by and every
    Person field in place, before the first commit, so no username ever enters the history. An owner without an account gets an inactive one."""
    person_keys = {}
    for p in glob.glob(os.path.join(repo, "config", "types", "*.yaml")):
        with open(p, encoding="utf-8") as fh:
            person_keys[os.path.basename(p)[:-5]] = {f["key"] for f in json.load(fh).get("fields", []) if f.get("type") == "user"}
    added, converted = [], 0

    def uid_of(name):
        u = users.resolve(name)
        if not u:
            users.create(name, name, "user", active=False)
            added.append(name)
            u = users.get(name)
        return u["uid"]

    for p in glob.glob(os.path.join(repo, "data", "*", "*.md")):
        keys = person_keys.get(os.path.basename(os.path.dirname(p)), set())
        with open(p, encoding="utf-8") as fh:
            head, sep, body = fh.read()[4:].partition("\n---\n")
        out, changed = [], False
        for line in head.split("\n"):
            m = LINE.match(line)
            if m and ((not m.group(1) and m.group(2) == "created_by") or (m.group(1) and m.group(2) in keys)):
                v = json.loads(m.group(3))
                if v:
                    nv = uid_of(v) if isinstance(v, str) else v
                    if nv != v:
                        line, changed = f"{m.group(1)}{m.group(2)}: {json.dumps(nv, ensure_ascii=False)}", True
            out.append(line)
        if changed:
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("---\n" + "\n".join(out) + sep + body)
            converted += 1
    return converted, added


def cmd_import_users(a):
    made, skipped, dated = import_stubs(Users(os.path.join(a.instance, "users.json")), a.stubs, create=not a.dates_only)
    print(f"user stubs created: {made}, already present: {skipped}, creation dates updated: {dated}. "
          "New ones cannot sign in until an administrator issues a set-password link.")


def cmd_erase_user(a):
    users = Users(os.path.join(a.instance, "users.json"))
    u = users.get(a.username)
    if not u:
        sys.exit(f"no such user: {a.username}")
    print(f"Erasing {a.username} ({u['name']}): the account goes for good; records and history keep only the user ID {u['uid']} and show 'former member'.")
    if not a.yes:
        sys.exit("Nothing changed. Add --yes to do it. Deactivating the account (Users page) is the reversible alternative.")
    try:
        users.erase(a.username)
    except ValueError as e:
        sys.exit(str(e))
    print("Done. Backups of users.json still hold the entry until they expire (section 16.5 of the plan).")


def cmd_set_password(a):
    users = Users(os.path.join(a.instance, "users.json"))
    if not users.get(a.username):
        sys.exit(f"no such user: {a.username}")
    pw = getpass.getpass("New password: ")
    if pw != getpass.getpass("Repeat: "):
        sys.exit("passwords differ")
    err = check_password_strength(pw)
    if err:
        sys.exit(err)
    users.set_password(a.username, pw)
    print("password set")


def cmd_set_passphrase(a):
    users = Users(os.path.join(a.instance, "users.json"))
    phrase = getpass.getpass("Sign-up passphrase (empty disables sign-up): ")
    users.set_signup_passphrase(phrase or None)
    print("sign-up passphrase " + ("updated" if phrase else "disabled"))


def cmd_drop_field(a):
    from shelfblazer.store import Store, StoreError
    store = Store(a.data)
    sch = store.schemas.get(a.type)
    if sch is None:
        sys.exit(f"unknown type '{a.type}' (types: {', '.join(sorted(store.schemas))})")
    n = sum(1 for r in store.records.values() if r["type"] == a.type and a.field in r["fields"])
    print(f"Field '{a.field}' of type '{a.type}': removing it from the type definition and from {n} record(s), as one git commit.")
    if not a.yes:
        sys.exit("Nothing changed. Add --yes to do it (the old values stay in the git history).")
    try:
        done = store.drop_field(a.type, a.field)
    except StoreError as e:
        sys.exit(str(e))
    print(f"Done: {done} record(s) updated. A running server picks the change up by itself.")


def cmd_rename_prefix(a):
    from shelfblazer.store import Store, StoreError
    store = Store(a.data)
    try:
        r = store.rename_prefix(a.type, a.prefix, dry_run=not a.yes)
    except StoreError as e:
        sys.exit(str(e))
    print(f"Type '{a.type}': ID prefix {r['old']} -> {r['new']}: {r['renamed']} record file(s) renamed; "
          f"records rewritten per type: {r['files']}; {r['folders']} attachment folder(s) renamed.")
    for path, n in r["mentions"][:40]:
        print(f"  mentions in another type's record: {path} ({n})")
    if not a.yes:
        sys.exit("Nothing changed (preview). Add --yes to do it, as one git commit; the old IDs keep opening the records.")
    print("Done. A running server picks the change up by itself.")


def cmd_lfs_setup(a):
    from shelfblazer.store import Store, StoreError
    store = Store(a.data)
    if store.uses_lfs() and not a.rewrite_history:
        sys.exit("This data repository already stores attached files with Git LFS.")
    if a.rewrite_history and not a.yes:
        sys.exit("--rewrite-history rewrites every commit (new commit ids; a clone or backup remote made before must be replaced). "
                 "Add --yes to do it.")
    try:
        store.enable_lfs(rewrite_history=a.rewrite_history)
    except StoreError as e:
        sys.exit(str(e))
    print("Attached files are now stored with Git LFS" + (" in the whole history." if a.rewrite_history else
          " (files committed before stay in the history in full; --rewrite-history converts them too).") + " A running server picks it up by itself.")


def cmd_run(a):
    import uvicorn
    from .web import create_app
    extra = {"forwarded_allow_ips": a.forwarded_allow_ips} if a.forwarded_allow_ips else {}
    uvicorn.run(create_app(a.data, a.instance, https_only=a.https), host=a.host, port=a.port, log_level="info", proxy_headers=True, **extra)


def resolve_paths(a):
    """Fill in every folder the chosen command needs from the runtime home, unless given explicitly."""
    home = a.home or os.environ.get("SHELFBLAZER_HOME") or "var"
    a.home = home
    a.instance = a.instance or os.path.join(home, "instance")
    if a.cmd == "init-data":
        a.src_given = bool(a.src)
        a.src = a.src or os.path.join(home, "import_out")
        a.to = a.to or os.path.join(home, "data")
    elif a.cmd == "import-users":
        a.stubs = a.stubs or os.path.join(home, "import_out", "users_stubs.yaml")
    elif a.cmd in ("run", "drop-field", "rename-prefix", "lfs-setup"):
        a.data = a.data or os.path.join(home, "data")
    return a


def main(argv=None):
    p = argparse.ArgumentParser(prog="shelfblazer")
    p.add_argument("--version", action="version", version=f"shelfblazer {__version__}")
    p.add_argument("--home", help="runtime home holding data/ and instance/ (default: $SHELFBLAZER_HOME or ./var)")
    p.add_argument("--instance", help="folder for users.json and secret.key (default: <home>/instance; keep it outside the data repo)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init-data", help="create the data repository: --empty for a new instance, or --from an import folder"); s.add_argument("--from", dest="src"); s.add_argument("--to")
    s.add_argument("--empty", action="store_true", help="start a new instance: one starter type, an admin account, no records")
    s.add_argument("--no-lfs", action="store_true", help="keep attached files in plain git instead of Git LFS"); s.set_defaults(f=cmd_init_data)
    s = sub.add_parser("import-users"); s.add_argument("--stubs"); s.add_argument("--dates-only", action="store_true", help="create nobody; only set the creation dates of existing imported accounts")
    s.set_defaults(f=cmd_import_users)
    s = sub.add_parser("set-password"); s.add_argument("username"); s.set_defaults(f=cmd_set_password)
    s = sub.add_parser("set-passphrase"); s.set_defaults(f=cmd_set_passphrase)
    s = sub.add_parser("erase-user", help="remove a person for good (right to erasure); records keep their user ID only")
    s.add_argument("username"); s.add_argument("--yes", action="store_true"); s.set_defaults(f=cmd_erase_user)
    s = sub.add_parser("drop-field", help="remove a field from a type and from all its records (one git commit)")
    s.add_argument("type"); s.add_argument("field"); s.add_argument("--data"); s.add_argument("--yes", action="store_true"); s.set_defaults(f=cmd_drop_field)
    s = sub.add_parser("rename-prefix", help="give a type a new ID prefix, rewriting records and links (one git commit; preview without --yes)")
    s.add_argument("type"); s.add_argument("prefix"); s.add_argument("--data"); s.add_argument("--yes", action="store_true"); s.set_defaults(f=cmd_rename_prefix)
    s = sub.add_parser("lfs-setup", help="store attached files with Git LFS in an existing data repository")
    s.add_argument("--data"); s.add_argument("--rewrite-history", action="store_true", help="also convert the files already in the history (rewrites commits)")
    s.add_argument("--yes", action="store_true"); s.set_defaults(f=cmd_lfs_setup)
    s = sub.add_parser("run"); s.add_argument("--data"); s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000); s.add_argument("--https", action="store_true", help="mark cookies Secure (use behind HTTPS)")
    s.add_argument("--forwarded-allow-ips", help="address(es) of the reverse proxy whose X-Forwarded-For is trusted, so throttling sees real client "
                   "addresses (default: 127.0.0.1, or $FORWARDED_ALLOW_IPS)"); s.set_defaults(f=cmd_run)
    a = resolve_paths(p.parse_args(argv))
    a.f(a)


if __name__ == "__main__":
    main()

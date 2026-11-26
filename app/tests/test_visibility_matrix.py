"""Every read path, for records the viewer may not see (design section 17.3).

Three hidden records: one archived by its creator (D40), one in a type only administrators read, and one in a personal collection
whose owner unchecked "Visible to others" (design section 17.3). Bob requests every page and API that
can show records; none may show their names, open them, link to them or count them. Personal collections add a third kind of hidden
record to the same test. A new route fails ``test_every_route_is_covered`` until it is listed here: checked, or exempt with a reason."""
import json, re

from test_app import Base, STRAIN
from test_mcp_app import McpBase
from shelfblazer_app import mcp_tools

SECRET_A, SECRET_T, SECRET_P = "SECRETARCHIVED", "SECRETTYPE", "SECRETPERSONAL"
VAULT = {"name": "Vault", "prefix": "VT", "list_columns": ["id", "name"], "scan_fields": ["code"], "permissions": {"read": "admin"}, "fields": [
    {"key": "name", "label": "Name", "type": "text", "required": True},
    {"key": "code", "label": "Code", "type": "text"},
    {"key": "strain", "label": "Strain", "type": "ref", "to": "strain"}]}
CODE = "4006381333931"

# route path -> why it needs no check here (everything else must be requested by the tests below)
EXEMPT = {
    "/login": "no records", "/logout": "no records", "/signup": "no records", "/reset/{token}": "no records", "/me": "no records",
    "/me/password": "no records", "/me/assistant": "no records (the person's own tokens)", "/look": "no records", "/appearance": "no records",
    "/admin/users": "administrators, who see everything", "/admin/users/{username}/entries": "administrators",
    "/admin/users/create": "administrators", "/admin/users/{username}/update": "administrators",
    "/admin/users/{username}/reset-link": "administrators", "/admin/passphrase": "administrators",
    "/admin/types": "administrators", "/admin/types/new": "administrators", "/admin/types/{slug}": "administrators",
    "/admin/types/{slug}/order": "administrators", "/admin/types/{slug}/settings": "administrators",
    "/admin/types/{slug}/fields/add": "administrators", "/admin/types/{slug}/fields/{key}/update": "administrators",
    "/admin/types/{slug}/fields/{key}/hidden": "administrators", "/admin/types/{slug}/fields/{key}/move": "administrators",
    "/admin/types/{slug}/delete": "administrators", "/admin/types/{slug}/preview": "administrators",
    "/admin/types/{slug}/import": "only redirects to /t/{slug}/import",
    "/t/{slug}/new": "a form for a new record (its links are checked by test_a_link_to_a_hidden_record_is_refused)",
    "/me/tokens": "the person's own tokens", "/me/tokens/{tid}/revoke": "the person's own tokens", "/bulk/undo": "reverts the person's own bulk commit", "/me/collections": "the person's own settings",
    "/help": "the shared text and the lab's own section; IDs in it link only to records the viewer may see (test_help_app)", "/admin/help": "administrators, who see everything",
    "/static": "files of the app", "/version": "no records", "/t/{slug}/import/template": "column titles of a type, no records",
}


class Matrix(McpBase):
    def setUp(self):
        super().setUp()
        root, alice = self.users.get("root"), self.users.get("alice")
        self.bobs_plasmid = self.create("bob", "plasmid", name="pBob")
        self.store.save_schema("vault", VAULT, root, create=True)
        self.store.save_schema("strain", dict(STRAIN, permissions={"personal": "all"},
                                              fields=STRAIN["fields"] + [{"key": "files", "label": "Files", "type": "file", "multiple": True}]), root)
        self.hidden_a = self.store.create("strain", alice, f"{SECRET_A} strain", {"plasmid": [self.bobs_plasmid]}, notes="secret notes")
        self.store.add_attachment(self.hidden_a, alice, "map.txt", b"SECRET FILE")
        self.store.set_archived(self.hidden_a, alice, True)
        self.hidden_t = self.store.create("vault", root, f"{SECRET_T} item", {"code": CODE})
        self.hidden_p = self.store.create("strain", alice, f"{SECRET_P} strain", {"plasmid": [self.bobs_plasmid]}, personal=True)
        self.store.add_attachment(self.hidden_p, alice, "own.txt", b"SECRET OWN FILE")
        self.users.set_collection_visible("alice", "strain", False)
        self.sha = self.store.git("log", "--format=%h", "-1", "--", f"data/strain/{self.hidden_a}.md").strip()
        self.store.update(self.bobs_plasmid, self.users.get("bob"), "pBob", {}, f"see {self.hidden_a}, {self.hidden_t} and {self.hidden_p}",
                          self.store.records[self.bobs_plasmid]["_version"])
        self.bob = self.client("bob")
        self.covered = set()
        self.covered_tools = set()
        self.mcp = self.rpc("bob")

    def tool(self, tool_name, /, **args):
        """Bob's agent calls a tool: (is_error, answer). The answer, as text, must show nothing of the hidden records."""
        self.covered_tools.add(tool_name)
        err, out = self.mcp.call(tool_name, **args)
        text = (out if isinstance(out, str) else json.dumps(out)).lower()
        if isinstance(out, dict) and out.get("notes"):          # the notes are his own words, which may mention any ID (as on the record page)
            text = text.replace(json.dumps(out["notes"]).lower()[1:-1], "")
        asked = [x for v in args.values() for x in (v.values() if isinstance(v, dict) else [v]) if isinstance(x, str) and x]
        for word in {w for v in asked for w in v.split()} | set(asked):      # an answer may repeat what he asked for, never more
            if len(word) >= 4:
                text = text.replace(word.lower(), "")
        for token in (SECRET_A, SECRET_T, SECRET_P, "secret notes", "SECRET FILE", "SECRET OWN FILE", self.hidden_a, self.hidden_t, self.hidden_p):
            self.assertNotIn(token.lower(), text, f"{tool_name} {args}")
        return err, out

    def get(self, path, **kw):
        self.covered.add(path.split("?")[0])
        return self.bob.get(path, **kw)

    def assert_clean(self, r, where):
        for token in (SECRET_A, SECRET_T, SECRET_P, "secret notes", "SECRET FILE", "SECRET OWN FILE"):
            self.assertNotIn(token, r.text, where)
        for rid in (self.hidden_a, self.hidden_t, self.hidden_p):
            self.assertNotIn(f'href="/r/{rid}"', r.text, where)
            self.assertNotEqual(r.headers.get("location"), f"/r/{rid}", where)

    def test_pages_and_apis(self):
        a, t, p = self.hidden_a, self.hidden_t, self.hidden_p
        for path in ("/", "/?q=secretpers", f"/?q={p}", "/t/strain?q=secretpers", f"/api/records?q=secretpers", f"/api/records?ids={p}",
                     f"/scan/link?rid={p}&c={CODE}", f"/move?ids={p}", f"/move/confirm?ids={p}", f"/?q=secretarch", f"/?q=secretarch&archived=1", f"/?q=secrettyp", f"/?q={a}", f"/?q={t}", f"/?q={CODE}",
                     "/t/strain", "/t/strain?archived=1", f"/t/strain?q=secretarch&archived=1", "/t/strain?archived=1&view=cards",
                     f"/t/strain?q={a}&archived=1", f"/t/plasmid?q=pBob", f"/r/{self.bobs_plasmid}",
                     f"/api/records?q=secretarch&archived=1", f"/api/records?q=secrettyp", f"/api/records?ids={a},{t}",
                     "/api/records?archived=1&limit=20", "/api/records?type=vault", f"/scan?c={CODE}", f"/scan/link?rid={a}&c={CODE}",
                     f"/scan/link?rid={t}&c={CODE}"):
            self.assert_clean(self.get(path), path)
        self.assert_clean(self.get(f"/?q=secretarch", headers={"x-live": "1"}), "live search")

    def test_the_records_themselves_answer_404(self):
        for rid in (self.hidden_a, self.hidden_t, self.hidden_p):
            for path in (f"/r/{rid}", f"/r/{rid}/edit"):
                r = self.get(path)
                self.assertEqual(r.status_code, 404, path)
                self.assert_clean(r, path)
        for path in (f"/r/{self.hidden_a}/at/{self.sha}", f"/files/{self.hidden_a}/map.txt", f"/files/{self.hidden_p}/own.txt", "/t/vault"):
            r = self.get(path)
            self.assertEqual(r.status_code, 404, path)
            self.assert_clean(r, path)
        self.covered |= {"/r/{rid}", "/r/{rid}/edit", "/r/{rid}/at/{sha}", "/files/{path:path}", "/t/{slug}", "/api/records", "/scan",
                         "/scan/link", "/"}

    def test_writes_and_exports_skip_or_refuse_them(self):
        a = self.hidden_a
        for path, data in ((f"/r/{a}/edit", {"name": "x"}), (f"/r/{a}/archive", {}), (f"/r/{a}/files/delete", {"name": "map.txt"}),
                           ("/scan/link", {"rid": a, "code": CODE})):
            self.assertIn(self.post(self.bob, path, data).status_code, (403, 404), path)
        r = self.bob.post(f"/r/{a}/files", data={"csrf_token": self.token(self.bob)}, files={"file": ("x.txt", b"x")})
        self.assertIn(r.status_code, (403, 404))
        for path in ("/t/strain/export", "/t/strain/export/download", "/t/strain/bulk", "/t/strain/bulk/apply", "/move/preview"):
            r = self.post(self.bob, path, {"ids": [a, self.hidden_p], "col": ["name"], "action": "archive"})
            self.assert_clean(r, path)
        self.assertNotIn(a, self.post(self.bob, "/t/strain/export/download", {"ids": [a], "col": ["id"]}).text)
        self.assertEqual(self.store.records[a]["archived"], True)

    def test_the_import_neither_shows_nor_changes_them(self):
        a, t, p = self.hidden_a, self.hidden_t, self.hidden_p
        self.assert_clean(self.get("/t/strain/import"), "import page")
        before = {rid: dict(rec) for rid, rec in self.store.records.items()}
        ids = f"ID\tName\n{a}\tx\n{t}\tx\n{p}\tx\n"
        names = f"Name\n{SECRET_A} strain\n{SECRET_P} strain\n"
        for path in ("preview", "apply"):
            r = self.post(self.bob, f"/t/strain/import/{path}", {"text": ids, "map_0": "id", "map_1": "name"})
            self.assert_clean(r, path)
            self.assertIn("does not exist", r.text)
        r = self.post(self.bob, "/t/strain/import/preview", {"text": names, "map_0": "name"})      # the names are his own file's: no hint they exist
        for rid in (a, t, p):
            self.assertNotIn(rid, r.text)
        self.assertNotIn("same name as", r.text)
        self.assert_clean(self.post(self.bob, "/t/strain/import", {"text": ids, "map_0": "id", "map_1": "name"}), "columns")
        self.assertEqual(self.bob.get(f"/t/strain/import/done/{self.sha}").status_code, 404)
        self.covered |= {"/t/{slug}/import", "/t/{slug}/import/preview", "/t/{slug}/import/apply", "/t/{slug}/import/done/{sha}"}
        self.assertEqual({rid: dict(rec) for rid, rec in self.store.records.items()}, before)

    def test_a_link_to_a_hidden_record_is_refused(self):
        r = self.post(self.bob, "/t/strain/new", {"name": "x", "f_plasmid": "", "f_storage": ""})
        self.assertEqual(r.status_code, 303)
        for rid in (self.hidden_a,):
            r = self.post(self.bob, "/t/strain/new", {"name": "y", "f_plasmid": rid})
            self.assertNotEqual(r.status_code, 303)

    def test_writes_to_the_personal_record_are_refused(self):
        p = self.hidden_p
        for path, data in ((f"/r/{p}/edit", {"name": "x"}), (f"/r/{p}/archive", {}), ("/scan/link", {"rid": p, "code": CODE}), ("/move", {"ids": [p]})):
            self.assertIn(self.post(self.bob, path, data).status_code, (403, 404), path)
        self.assertEqual(self.store.records[p]["name"], f"{SECRET_P} strain")

    def test_once_visible_again_bob_sees_the_personal_record(self):
        self.users.set_collection_visible("alice", "strain", True)              # proves the checks above can see it when allowed
        self.assertIn(SECRET_P, self.get("/?q=secretpers").text)
        self.assertEqual(self.get(f"/r/{self.hidden_p}").status_code, 200)
        self.assertIn(f'href="/r/{self.hidden_p}"', self.get(f"/r/{self.bobs_plasmid}").text)

    def test_once_the_owner_is_erased_the_personal_record_is_for_administrators_only(self):
        self.users.set_collection_visible("alice", "strain", True)
        self.users.erase("alice")                                                 # her choice goes with her account
        self.assertEqual(self.get(f"/r/{self.hidden_p}").status_code, 404)
        self.assert_clean(self.get("/?q=secretpers"), "search after erasure")
        self.assertEqual(self.client("root").get(f"/r/{self.hidden_p}").status_code, 200)

    def test_formulas_do_not_count_them(self):
        fdef = {"key": "n", "label": "N", "type": "computed", "expr": "count(referenced_by('strain'))"}
        value, _ = self.store.compute(self.store.records[self.bobs_plasmid], fdef, self.users.get("bob"))
        self.assertEqual(value, 0)
        value, _ = self.store.compute(self.store.records[self.bobs_plasmid], fdef, self.users.get("alice"))
        self.assertEqual(value, 2)                                  # the creator still counts her archived and her personal strain

    def test_the_mcp_tools_never_show_or_change_them(self):
        a, t, p = self.hidden_a, self.hidden_t, self.hidden_p
        before = {rid: dict(rec) for rid, rec in self.store.records.items()}
        _, types = self.tool("list_types")
        self.assertNotIn("vault", [x["slug"] for x in types["types"]])
        for coll in ("lab", "mine", "others", "any"):
            for q in ("secretarch", "secretpers", "secrettyp", a, t, p, CODE, ""):
                for arch in (False, True):
                    _, res = self.tool("search_records", text=q, collection=coll, archived=arch)
                    self.assertFalse({r["id"] for r in res["results"]} & {a, t, p}, (coll, q, arch))
        self.assertTrue(self.tool("search_records", type="vault")[0])
        # conditions, chosen fields, sorting and counts work on what he may see only: the hidden strains both link to his plasmid
        for coll in ("lab", "any"):
            _, res = self.tool("search_records", type="strain", collection=coll, archived=True, where={"plasmid": f"={self.bobs_plasmid}"})
            self.assertEqual(res["total"], 0, coll)
            _, res = self.tool("search_records", type="strain", collection=coll, archived=True, group_by="plasmid")
            self.assertEqual(res["groups"], [], coll)
            for by in ("owner", "collection", "type"):
                _, res = self.tool("search_records", collection=coll, archived=True, group_by=by)
                self.assertEqual(res["total"], 1, (coll, by))                                          # his plasmid only
            _, res = self.tool("search_records", collection=coll, archived=True, sort="-updated", fields=["name", "updated_at"])
            self.assertEqual([r["id"] for r in res["results"]], [self.bobs_plasmid])
            _, res = self.tool("search_records", type="strain", collection=coll, archived=True, where={"name": "contains:secret"})
            self.assertEqual(res["total"], 0)
        self.assertTrue(self.tool("search_records", type="vault", where={"code": "not empty"})[0])
        for rid in (a, t, p):
            self.assertTrue(self.tool("get_record", id=rid)[0])
            self.assertTrue(self.tool("get_record", id=rid.lower())[0])
            self.assertTrue(self.tool("get_picture", id=rid)[0])
            self.assertTrue(self.tool("update_record", id=rid, version=before[rid]["_version"], name="x")[0])
            self.assertTrue(self.tool("archive_record", id=rid)[0])
            self.assertTrue(self.tool("restore_record", id=rid)[0])
            self.assertTrue(self.tool("check_record", type="strain", name="x", id=rid)[0])
        self.assertTrue(self.tool("check_record", type="vault", name="x")[0])
        self.assertTrue(self.tool("create_record", type="vault", name="x")[0])
        self.assertTrue(self.tool("import_table", type="vault", text="Name\nx\n", apply=True)[0])
        self.assertEqual(self.tool("get_record", id=self.bobs_plasmid)[1]["linked_from"], [])               # her strains are hidden from him
        for uri in ("conventions://vault", "schema://vault"):
            self.assertIsNotNone(self.mcp.read(uri)[0])
        # a link to a hidden record, and a name equal to a hidden record's, tell him nothing
        _, r = self.tool("check_record", type="strain", name="x", fields={"plasmid": a})
        self.assertEqual(set(r["errors"]), {"plasmid"})
        _, r = self.tool("create_record", type="strain", name="x", fields={"plasmid": p})
        self.assertFalse(r["created"])
        for name in (f"{SECRET_A} strain", f"{SECRET_P} strain"):
            _, r = self.tool("check_record", type="strain", name=name)
            self.assertEqual(r["findings"], [], name)
        _, r = self.tool("import_table", type="strain", text=f"ID\tName\n{a}\tx\n{t}\tx\n{p}\tx\n", columns={"ID": "id", "Name": "name"}, apply=True)
        self.assertEqual((r["applied"], r["errors"]), (False, 3))
        _, r = self.tool("import_table", type="strain", text=f"Name\n{SECRET_A} strain\n", apply=False)
        self.assertEqual(r["rows"][0]["warnings"], [])
        self.assertTrue(self.tool("undo_import", commit=self.sha)[0])
        self.assertEqual({rid: dict(rec) for rid, rec in self.store.records.items()}, before)

    def test_every_tool_is_covered(self):
        """A new tool fails here until the test above requests it for the hidden records."""
        self.test_the_mcp_tools_never_show_or_change_them()
        self.assertEqual(self.covered_tools, set(mcp_tools.TOOLS), "a new tool: check it against the hidden records in test_the_mcp_tools_never_show_or_change_them")

    def test_every_route_is_covered(self):
        checked = {"/mcp",       # every tool is requested for the hidden records in test_the_mcp_tools_never_show_or_change_them
                   "/", "/t/{slug}", "/api/records", "/r/{rid}", "/r/{rid}/edit", "/r/{rid}/at/{sha}", "/files/{path:path}", "/scan",
                   "/scan/link", "/r/{rid}/archive", "/r/{rid}/files", "/r/{rid}/files/delete", "/t/{slug}/export",
                   "/t/{slug}/export/download", "/t/{slug}/bulk", "/t/{slug}/bulk/apply", "/move", "/move/confirm", "/move/preview",
                   "/t/{slug}/import", "/t/{slug}/import/preview", "/t/{slug}/import/apply", "/t/{slug}/import/done/{sha}"}
        routes = {getattr(r, "path", None) for r in self.app.routes} - {None}
        missing = sorted(p for p in routes if p not in checked and p not in EXEMPT)
        self.assertEqual(missing, [], "a new route: add a check for hidden records above, or list it in EXEMPT with the reason")

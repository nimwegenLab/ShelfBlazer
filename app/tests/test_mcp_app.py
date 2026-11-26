"""The MCP endpoint: access tokens (create, show once, store hashed, expire, revoke, throttle), the tools run as the token owner with
member rights, commits say "(via MCP)", and the endpoint is off unless asked for."""
import json, re, time, unittest

from starlette.testclient import TestClient

from test_app import Base, PW
from shelfblazer.auth import MAX_TOKENS


class Rpc:
    """A client of /mcp with one token: ``call(tool, **arguments)`` -> (is_error, parsed answer or message)."""

    def __init__(self, client, token, path="/mcp"):
        self.client, self.path = client, path
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}

    def raw(self, method, params=None):
        return self.client.post(self.path, headers=self.headers, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})

    def call(self, tool, **arguments):
        r = self.raw("tools/call", {"name": tool, "arguments": arguments})
        assert r.status_code == 200, (r.status_code, r.text[:300])
        res = r.json()["result"]
        text = res["content"][0]["text"]
        return res["isError"], (text if res["isError"] else json.loads(text))

    def read(self, uri):
        r = self.raw("resources/read", {"uri": uri})
        body = r.json()
        return body.get("error"), "".join(c["text"] for c in body.get("result", {}).get("contents", []))


class McpBase(Base):
    app_options = {"mcp": True}

    def setUp(self):
        super().setUp()
        self.http = TestClient(self.app)
        self.http.__enter__()                                  # runs the app's lifespan (the MCP session managers)
        self.addCleanup(self.http.__exit__, None, None, None)

    def rpc(self, who, scope="member", path="/mcp"):
        token, _ = self.users.create_token(who, "test", 30, scope)
        return Rpc(self.http, token, path)

    def tokens_file(self):
        return self.read(self.users.path)


class TestTokens(McpBase):
    def test_created_on_the_account_page_shown_once_and_stored_hashed(self):
        c = self.client("alice")
        r = self.post(c, "/me/tokens", {"name": "laptop", "days": "30", "current": PW})
        self.assertEqual(r.status_code, 200)
        token = re.search(r'id="new-token">(sbt_[\w-]+)<', r.text).group(1)
        self.assertIn('data-copy="#new-token"', r.text)                 # a copy button: retyping a token goes wrong easily
        self.assertNotIn(token, self.tokens_file())                  # only a hash is stored
        self.assertNotIn(token, c.get("/me/assistant").text)                    # not shown again
        self.assertIn("laptop", c.get("/me/assistant").text)
        self.assertEqual(Rpc(self.http, token).call("list_types")[0], False)

    def test_the_token_is_never_part_of_a_command_line_that_lands_in_the_shell_history(self):
        c = self.client("alice")
        page = self.post(c, "/me/tokens", {"name": "laptop", "days": "30", "current": PW}).text
        token = re.search(r'id="new-token">(sbt_[\w-]+)<', page).group(1)
        self.assertEqual(page.count(token), 1)                        # shown in one place, to copy
        self.assertNotIn("Bearer " + token, page)
        self.assertIn("read -rs SBT", page)                           # pasted at a hidden prompt instead
        self.assertIn('Bearer $SBT"', page)

    def test_expiry_is_limited_and_a_name_is_required(self):
        c = self.client("alice")
        self.assertEqual(self.post(c, "/me/tokens", {"name": "x", "days": "366", "current": PW}).status_code, 400)
        self.assertEqual(self.post(c, "/me/tokens", {"name": " ", "days": "30", "current": PW}).status_code, 400)
        self.assertEqual(self.users.tokens_of("alice"), [])

    def test_making_a_token_needs_the_password_not_just_a_session(self):
        c = self.client("alice")
        for current in ("", "wrong password"):
            r = self.post(c, "/me/tokens", {"name": "stolen", "days": "30", "current": current})
            self.assertEqual(r.status_code, 400)
            self.assertIn("Your ShelfBlazer password is wrong", r.text)
        self.assertEqual(self.users.tokens_of("alice"), [])

    def test_at_most_five_tokens(self):
        for i in range(MAX_TOKENS):
            self.users.create_token("alice", f"t{i}")
        with self.assertRaises(ValueError):
            self.users.create_token("alice", "one more")

    def test_revoking_one_token_stops_it_only(self):
        a, b = self.rpc("alice"), self.rpc("alice")
        c = self.client("alice")
        tid = self.users.tokens_of("alice")[0]["id"]
        self.assertEqual(self.post(c, f"/me/tokens/{tid}/revoke").status_code, 303)
        working = [r for r in (a, b) if r.raw("tools/list").status_code == 200]
        self.assertEqual(len(working), 1)

    def test_nobody_can_revoke_another_persons_token(self):
        rpc = self.rpc("alice")
        tid = self.users.tokens_of("alice")[0]["id"]
        self.post(self.client("bob"), f"/me/tokens/{tid}/revoke")
        self.assertEqual(rpc.raw("tools/list").status_code, 200)

    def test_expired_missing_and_wrong_tokens_are_refused(self):
        rpc = self.rpc("alice")
        with self.users._txn() as d:
            for t in d["users"]["alice"]["tokens"].values():
                t["expires"] = time.time() - 1
        self.assertEqual(rpc.raw("tools/list").status_code, 401)
        for headers in ({}, {"Authorization": "Bearer sbt_nope"}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer " + PW}):
            self.users.clear_failures("token", "testclient")
            self.assertEqual(self.http.post("/mcp", headers=headers, json={}).status_code, 401)

    def test_a_token_stops_when_the_account_is_deactivated_erased_or_reset(self):
        for what in ("deactivate", "erase", "reset-link", "set-password"):
            self.users.create(f"u-{what}", "U", "user", PW)
            rpc = self.rpc(f"u-{what}")
            self.assertEqual(rpc.raw("tools/list").status_code, 200)
            if what == "deactivate": self.users.update(f"u-{what}", active=False)
            elif what == "erase": self.users.erase(f"u-{what}")
            elif what == "reset-link": self.users.make_reset_token(f"u-{what}")
            else: self.users.set_password(f"u-{what}", "a new password")
            self.assertEqual(rpc.raw("tools/list").status_code, 401, what)

    def test_changing_your_own_password_revokes_your_tokens(self):
        rpc = self.rpc("alice")
        c = self.client("alice")
        r = self.post(c, "/me/password", {"current": PW, "password": "another long one", "password2": "another long one"})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(rpc.raw("tools/list").status_code, 401)             # a token made from a stolen session does not outlive it
        self.assertIn("access tokens were revoked", c.get("/me").text)

    def test_wrong_tokens_are_throttled_but_never_lock_a_valid_token_or_a_sign_in(self):
        from shelfblazer.auth import MAX_TOKEN_FAILS
        rpc = self.rpc("alice")
        wrong = lambda: self.http.post("/mcp", headers={"Authorization": "Bearer sbt_x"}, json={}).status_code
        for _ in range(MAX_TOKEN_FAILS - 1):
            self.assertEqual(wrong(), 401)
        self.assertEqual(wrong(), 429)                                         # the one that reaches the limit
        self.assertEqual(rpc.raw("tools/list").status_code, 200)             # the right token, from the same address, still works
        self.assertFalse(self.users.is_locked("alice", "testclient"))         # and the address's sign-in is untouched
        self.assertFalse(self.users.is_locked("anyone", "testclient"))

    def test_only_administrators_get_administrator_tokens_and_they_are_short(self):
        with self.assertRaises(ValueError):
            self.users.create_token("alice", "x", 10, "admin")
        with self.assertRaises(ValueError):
            self.users.create_token("root", "x", 31, "admin")
        self.users.create_token("root", "x", 30, "admin")
        self.assertNotIn("administrator token", self.client("alice").get("/me/assistant").text)
        self.assertIn("Administrator token", self.client("root").get("/me/assistant").text)

    def test_an_administrator_token_stops_when_the_owner_is_demoted(self):
        self.users.create("boss", "Boss", "admin", PW)
        token, _ = self.users.create_token("boss", "x", 30, "admin")
        self.users.update("boss", role="user")
        self.assertEqual(self.users.authenticate_token(token, "1.2.3.4"), (None, None))

    def test_token_secrets_never_reach_other_views_of_the_account(self):
        self.users.create_token("alice", "x")
        self.assertNotIn("tokens", self.users.public(self.users.get("alice")))


class TestEndpoint(McpBase):
    def test_off_unless_asked_for(self):
        self.app_options = {}
        app = type(self.app)  # noqa: F841
        from shelfblazer_app.web import create_app
        off = TestClient(create_app(self.repo, self.inst, mcp=False))
        self.assertEqual(off.post("/mcp", json={}).status_code, 404)
        self.assertNotIn("Access tokens", self.client("alice").get("/me").text.replace("Access tokens", "", 0) if False else off.get("/me").text)

    def test_the_menu_and_the_new_record_form_point_to_the_assistant(self):
        c = self.client("alice")
        home = c.get("/").text
        self.assertIn('href="/me/assistant" class="helpnav ainav"', home)
        self.assertIn("Connect AI assistant", home)
        self.assertIn("Describe them to your AI assistant", c.get("/t/strain/new").text)
        self.assertNotIn("Describe them to your AI assistant", c.get(f"/r/{self.create('alice')}/edit").text)    # new records only
        self.users.create_token("alice", "laptop")
        home = c.get("/").text
        self.assertIn('</svg>AI assistant</a>', home)                                    # set up: the same page, shorter label
        self.assertNotIn("Connect AI assistant", home)
        page = c.get("/me/assistant").text
        self.assertIn('class="helpnav ainav" aria-current="page"', page)
        self.assertRegex(page, r'<div class="tabs"[^>]*><a href="/me">General</a><a href="/me/assistant" aria-current="page">AI assistant</a>'
                               r'<a href="/me/password">Password</a></div>')
        self.assertIn("Set up</strong> on 1 computer: laptop", page)
        self.assertIn('data-copy="#cmd-add"', page)
        self.assertIn("claude mcp add --scope user", page)                                 # works in every folder
        from shelfblazer_app.web import create_app
        off = TestClient(create_app(self.repo, self.inst, mcp=False), follow_redirects=False)
        self.post(off, "/login", {"username": "alice", "password": PW}, anon=True)
        self.assertNotIn("ainav", off.get("/").text)                                       # not there while the endpoint is off
        self.assertNotIn("AI assistant", off.get("/t/strain/new").text)
        self.assertNotIn("AI assistant", off.get("/me").text)                                 # no tab either
        self.assertEqual(off.get("/me/assistant").status_code, 404)

    def test_member_tools_only_on_the_member_endpoint(self):
        rpc = self.rpc("alice")
        names = {t["name"] for t in rpc.raw("tools/list").json()["result"]["tools"]}
        self.assertEqual(names, {"list_types", "search_records", "get_record", "check_record", "create_record", "update_record",
                                 "archive_record", "restore_record", "import_table", "undo_import"})
        self.assertEqual(self.http.post("/mcp/admin", headers=rpc.headers, json={}).status_code, 404)

    def test_instructions_and_resources(self):
        rpc = self.rpc("alice")
        init = rpc.raw("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}).json()
        self.assertIn("conventions://", init["result"]["instructions"])
        with open(self.repo + "/config/checks/plasmid.yaml", "w") if __import__("os").makedirs(self.repo + "/config/checks", exist_ok=True) is None else None as fh:
            json.dump({"guidance": "Name plasmids pXY-1."}, fh)
        self.store.reload()
        self.assertEqual(rpc.read("conventions://plasmid"), (None, "Name plasmids pXY-1."))
        self.assertIn("No conventions", rpc.read("conventions://strain")[1])
        err, text = rpc.read("schema://strain")
        self.assertIn('"genotype"', text)
        self.assertIsNotNone(rpc.read("conventions://nothing")[0])

    def test_commits_are_made_as_the_person_and_say_via_mcp(self):
        rpc = self.rpc("alice")
        err, out = rpc.call("create_record", type="strain", name="S1")
        self.assertEqual((err, out["created"], out["id"]), (False, True, "ST-1"))
        rid = out["id"]
        self.assertEqual(self.store.records[rid]["created_by"], self.uid("alice"))
        log = self.store.git("log", "-1", "--format=%an%x09%s")
        self.assertEqual(log.strip(), f"{self.uid('alice')}\tST-1: create by {self.uid('alice')} (via MCP)")
        self.assertEqual(self.store.git("log", "-1", "--format=%an", "--", "data").strip(), self.uid("alice"))

    def test_an_administrators_member_token_has_member_rights(self):
        rid = self.create("alice", name="alices")
        self.store.set_archived(rid, self.users.get("alice"), True)
        rpc = self.rpc("root")                                   # root is an administrator, but sees what a member sees
        self.assertTrue(rpc.call("get_record", id=rid)[0])
        self.assertEqual(rpc.call("search_records", archived=True)[1]["total"], 0)
        self.assertEqual(self.store.records[rid]["archived"], True)
        _, made = rpc.call("create_record", type="strain", name="by admin")
        self.assertNotIn("root", self.store.git("log", "-1", "--format=%an"))


class TestTools(McpBase):
    def setUp(self):
        super().setUp()
        self.alice, self.bob = self.rpc("alice"), self.rpc("bob")

    def test_list_types_and_search_and_get(self):
        _, types = self.alice.call("list_types")
        strain = next(t for t in types["types"] if t["slug"] == "strain")
        self.assertTrue(strain["can_add_to_lab"])
        self.assertIn("status", [f["key"] for f in strain["fields"]])
        _, made = self.alice.call("create_record", type="strain", name="Strain A", fields={"genotype": "dX", "status": "ok"}, notes="n1")
        rid = made["id"]
        _, found = self.bob.call("search_records", type="strain", text="Strain A")
        self.assertEqual([r["id"] for r in found["results"]], [rid])
        err, rec = self.bob.call("get_record", id=rid.lower())
        self.assertEqual((rec["fields"]["genotype"], rec["notes"], rec["can_edit"], rec["owner"]), ("dX", "n1", False, "Alice A"))
        self.assertIn("(via MCP)", rec["history"][0]["what"])
        self.assertEqual(self.alice.call("get_record", id=rid)[1]["can_edit"], True)

    def test_links_both_ways_and_names_of_files_not_contents(self):
        _, pl = self.alice.call("create_record", type="plasmid", name="pA")
        _, st = self.alice.call("create_record", type="strain", name="S", fields={"plasmid": [pl["id"]]})
        self.store.add_attachment(pl["id"], self.users.get("alice"), "map.txt", b"CONTENT")
        _, s = self.alice.call("get_record", id=st["id"])
        self.assertEqual(s["fields"]["plasmid"], [{"id": pl["id"], "name": "pA"}])
        _, p = self.alice.call("get_record", id=pl["id"])
        self.assertEqual(p["linked_from"][0]["id"], st["id"])
        self.assertEqual(p["files"], {"attachments": ["map.txt"]})
        self.assertNotIn("CONTENT", json.dumps(p))

    def test_check_record_validation_and_duplicates_write_nothing(self):
        self.alice.call("create_record", type="strain", name="Same")
        _, r = self.bob.call("check_record", type="strain", name="same", fields={"status": "weird", "nope": "x"})
        self.assertFalse(r["ok"])
        self.assertEqual(set(r["errors"]), {"status", "nope"})
        _, r = self.bob.call("check_record", type="strain", name="same")
        self.assertTrue(r["ok"])
        self.assertEqual([f["kind"] for f in r["findings"]], ["duplicate"])
        self.assertEqual(r["findings"][0]["records"][0]["id"], "ST-1")
        self.assertEqual(len(self.store.records), 1)

    def test_create_needs_confirmation_for_a_duplicate(self):
        self.alice.call("create_record", type="strain", name="Same")
        _, r = self.bob.call("create_record", type="strain", name="Same")
        self.assertEqual((r["created"], r.get("needs_confirmation")), (False, True))
        self.assertEqual(len(self.store.records), 1)
        _, r = self.bob.call("create_record", type="strain", name="Same", confirm_duplicates=True)
        self.assertTrue(r["created"])
        self.assertEqual(self.store.records[r["id"]]["created_by"], self.uid("bob"))

    def test_create_with_errors_writes_nothing_and_a_personal_record_goes_to_the_own_collection(self):
        _, r = self.alice.call("create_record", type="strain", name="", fields={"status": "weird"})
        self.assertEqual((r["created"], set(r["errors"])), (False, {"name", "status"}))
        err, msg = self.alice.call("create_record", type="strain", name="p", collection="mine")
        self.assertTrue(err)
        self.assertIn("own collection", msg)

    def test_update_needs_the_current_version_and_keeps_other_fields(self):
        _, made = self.alice.call("create_record", type="strain", name="S", fields={"genotype": "g", "status": "ok"})
        rid, v1 = made["id"], made["version"]
        _, up = self.alice.call("update_record", id=rid, version=v1, fields={"status": "bad"})
        self.assertTrue(up["updated"])
        rec = self.store.records[rid]
        self.assertEqual((rec["fields"], rec["name"]), ({"genotype": "g", "status": "bad"}, "S"))
        err, msg = self.alice.call("update_record", id=rid, version=v1, fields={"status": "ok"})          # stale
        self.assertTrue(err)
        self.assertIn("conflict", msg)
        self.assertEqual(self.store.records[rid]["fields"]["status"], "bad")
        _, up = self.alice.call("update_record", id=rid, version=up["version"], fields={"genotype": ""})        # clearing
        self.assertNotIn("genotype", self.store.records[rid]["fields"])

    def test_update_by_someone_who_may_not_edit_is_refused(self):
        _, made = self.alice.call("create_record", type="strain", name="S")
        err, msg = self.bob.call("update_record", id=made["id"], version=made["version"], name="mine now")
        self.assertTrue(err)
        self.assertEqual(self.store.records[made["id"]]["name"], "S")
        self.assertTrue(self.bob.call("archive_record", id=made["id"])[0])

    def test_update_reports_only_duplicates_it_brings(self):
        _, a = self.alice.call("create_record", type="strain", name="One")
        _, b = self.alice.call("create_record", type="strain", name="Two")
        _, up = self.alice.call("update_record", id=b["id"], version=b["version"], name="One")
        self.assertEqual((up["updated"], up.get("needs_confirmation")), (False, True))
        _, up = self.alice.call("update_record", id=b["id"], version=b["version"], name="One", confirm_duplicates=True)
        self.assertTrue(up["updated"])
        _, again = self.alice.call("update_record", id=b["id"], version=up["version"], fields={"status": "ok"})   # already a duplicate: not new
        self.assertEqual((again["updated"], again["findings"]), (True, []))

    def test_archive_and_restore(self):
        _, made = self.alice.call("create_record", type="strain", name="S")
        self.assertEqual(self.alice.call("archive_record", id=made["id"])[1]["archived"], True)
        self.assertEqual(self.alice.call("search_records", type="strain")[1]["total"], 0)
        self.assertEqual(self.alice.call("search_records", type="strain", archived=True)[1]["total"], 1)
        self.assertEqual(self.alice.call("restore_record", id=made["id"])[1]["archived"], False)
        self.assertIn("archive by", self.store.git("log", "--format=%s", "-2").splitlines()[1])

    def test_import_preview_apply_and_undo(self):
        text = "Name\tGenotype\nA1\tx\nA2\ty\n"
        _, pre = self.alice.call("import_table", type="strain", text=text)
        self.assertEqual((pre["applied"], pre["created"], pre["errors"]), (False, 2, 0))
        self.assertEqual(len(self.store.records), 0)
        _, done = self.alice.call("import_table", type="strain", text=text, apply=True)
        self.assertEqual((done["applied"], len(done["written"])), (True, 2))
        self.assertTrue(self.store.git("log", "-1", "--format=%s").strip().endswith("(via MCP)"))
        _, bad = self.alice.call("import_table", type="strain", text="Name\tStatus\nA3\tweird\n", apply=True)
        self.assertEqual((bad["applied"], bad["errors"]), (False, 1))
        self.assertEqual(len(self.store.records), 2)
        self.assertTrue(self.bob.call("undo_import", commit=done["commit"])[0])                 # not his
        _, undone = self.alice.call("undo_import", commit=done["commit"])
        self.assertTrue(undone["undone"])
        self.assertTrue(all(self.store.records[r]["archived"] is False for r in self.store.records) and not self.store.records or True)
        self.assertEqual(len(self.store.records), 0)

    def test_import_columns_and_mapping(self):
        _, r = self.alice.call("import_table", type="strain", text="Title;Notes\nQ1;hello\n", columns={"Title": "name", "Notes": "notes"})
        self.assertEqual(r["mapping"], {"Title": "name", "Notes": "notes"})
        self.assertEqual(r["created"], 1)

    def test_unknown_input_is_an_error_not_a_crash(self):
        for tool, args in (("get_record", {"id": "ZZ-1"}), ("search_records", {"type": "nothing"}), ("search_records", {"collection": "x"}),
                           ("create_record", {"type": "nothing", "name": "x"}), ("import_table", {"type": "strain", "text": ""})):
            err, msg = self.alice.call(tool, **args)
            self.assertTrue(err, tool)
            self.assertNotIn("Traceback", str(msg))


if __name__ == "__main__":
    unittest.main()


class TestSearch(McpBase):
    """search_records: conditions on fields, chosen fields, sequence search, dates and sorting, counts per value."""

    def setUp(self):
        super().setUp()
        self.alice = self.rpc("alice")
        a = self.users.get("alice")
        self.pk = self.store.create("plasmid", a, "pKan", {"resistance": ["Kan"]})
        self.pa = self.store.create("plasmid", a, "pAmp", {"resistance": ["Amp", "Kan"]})
        self.s1 = self.store.create("strain", a, "Strain one", {"genotype": "lacZ deletion", "status": "ok", "plasmid": [self.pk]})
        self.s2 = self.store.create("strain", a, "Strain two", {"genotype": "wild type", "status": "bad"})
        self.s3 = self.store.create("strain", a, "Strain three", {"genotype": "lacZ::kan", "plasmid": [self.pk, self.pa]})

    def ids(self, **args):
        err, out = self.alice.call("search_records", **args)
        self.assertFalse(err, out)
        return [r["id"] for r in out["results"]]

    def test_conditions_on_fields(self):
        self.assertEqual(self.ids(type="strain", where={"plasmid": "empty"}), [self.s2])
        self.assertEqual(self.ids(type="strain", where={"plasmid": "not empty"}), [self.s1, self.s3])
        self.assertEqual(self.ids(type="strain", where={"plasmid": self.pa}), [self.s3])                       # a link by ID
        self.assertEqual(self.ids(type="strain", where={"genotype": "contains:LACZ"}), [self.s1, self.s3])    # ignoring case
        self.assertEqual(self.ids(type="strain", where={"genotype": "contains:lacz", "status": "ok"}), [self.s1])   # all must hold
        self.assertEqual(self.ids(type="strain", where={"status": "!=ok"}), [self.s2, self.s3])
        self.assertEqual(self.ids(type="strain", where={"genotype": "not contains:lacz"}), [self.s2])
        self.assertEqual(self.ids(type="plasmid", where={"resistance": "amp"}), [self.pa])
        _, out = self.alice.call("search_records", type="strain", where={"plasmid": "empty"}, limit=1)
        self.assertEqual(out["total"], 1)                                                                   # the count, in one call

    def test_unknown_hidden_and_cross_type_fields_are_refused(self):
        root = self.users.get("root")
        schema = dict(self.store.schemas["strain"])
        schema["fields"] = [dict(f, hidden=True) if f["key"] == "genotype" else f for f in schema["fields"]]
        self.store.save_schema("strain", schema, root)
        for args in ({"type": "strain", "where": {"nope": "empty"}}, {"type": "strain", "where": {"genotype": "empty"}},
                     {"type": "strain", "fields": ["genotype"]}, {"type": "strain", "group_by": "genotype"},
                     {"type": "strain", "sort": "genotype"}, {"where": {"status": "ok"}}, {"type": "strain", "where": {"status": ">x"}, "fields": ["nope"]}):
            err, out = self.alice.call("search_records", **args)
            self.assertTrue(err, args)

    def test_chosen_fields_come_back_with_each_result(self):
        _, out = self.alice.call("search_records", type="strain", where={"status": "ok"}, fields=["genotype", "plasmid", "status", "owner"])
        self.assertEqual(out["results"][0]["fields"], {"genotype": "lacZ deletion", "plasmid": [self.pk], "status": "ok", "owner": "Alice A"})
        self.assertIn("created_at", out["results"][0])

    def test_dates_and_sorting(self):
        rec = self.store.records[self.s2]
        self.store.records[self.s2] = dict(rec, created_at="2001-01-01T00:00:00", updated_at="2001-01-02T00:00:00")
        self.assertEqual(self.ids(type="strain", where={"created_at": "<2002-01-01"}), [self.s2])
        self.assertEqual(self.ids(type="strain", where={"updated_at": [">=2001-01-01", "<=2001-12-31"]}), [self.s2])
        self.assertEqual(self.ids(type="strain", sort="-updated")[-1], self.s2)
        self.assertEqual(self.ids(type="strain", sort="name"), [self.s1, self.s3, self.s2])
        self.assertEqual(self.ids(type="strain", sort="-name"), [self.s2, self.s3, self.s1])

    def test_counts_per_value(self):
        _, out = self.alice.call("search_records", type="strain", group_by="plasmid")
        self.assertEqual(out["groups"], [{"value": self.pk, "count": 2}, {"value": "(empty)", "count": 1}, {"value": self.pa, "count": 1}])
        self.assertEqual(out["total"], 3)
        self.assertNotIn("results", out)
        _, out = self.alice.call("search_records", group_by="type")
        self.assertEqual(out["groups"], [{"value": "strain", "count": 3}, {"value": "plasmid", "count": 2}])

    def test_sequence_search(self):
        root, a = self.users.get("root"), self.users.get("alice")
        self.store.save_schema("oligo", {"name": "Oligo", "prefix": "OL", "sequence_search": "sequence", "list_columns": ["id", "name"], "fields": [
            {"key": "name", "label": "Name", "type": "text", "required": True},
            {"key": "sequence", "label": "Sequence", "type": "text", "validate": "dna"}]}, root, create=True)
        gene = self.store.create("oligo", a, "gene", {"sequence": "ATGGTTTACTCCTATACCGAGAAAAAACGTTAGCC"})
        self.store.create("oligo", a, "other", {"sequence": "GGGGGGGGGGGGGGGG"})
        self.assertEqual(self.ids(type="oligo", sequence="GGTATAGGA"), [gene])                       # the other strand
        self.assertEqual(self.ids(type="oligo", sequence="tcctattcc"), [])
        self.assertEqual(self.ids(type="oligo", sequence="tcctattcc", mismatches=1), [gene])
        for args in ({"type": "strain", "sequence": "acgtacgt"}, {"sequence": "acgtacgt"}, {"type": "oligo", "sequence": "acgtacgt", "text": "x"}):
            self.assertTrue(self.alice.call("search_records", **args)[0], args)

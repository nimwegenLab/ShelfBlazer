"""Type definitions: pure editing functions, validation, and the store's create / change / remove."""
import copy, json, os, subprocess, tempfile, unittest

from helpers import purge
from shelfblazer import schema_edit as se
from shelfblazer.search import Index
from shelfblazer.store import Forbidden, NotFound, Store, ValidationError

BASE = {"name": "Lipid", "prefix": "LP", "list_columns": ["id", "name", "created_by"], "fields": [
    {"key": "name", "label": "Name", "type": "text", "required": True},
    {"key": "kind", "label": "Kind", "type": "select", "options": ["a", "b", "c"]},
    {"key": "tags", "label": "Tags", "type": "multiselect", "options": ["x", "y"]},
    {"key": "note", "label": "Note", "type": "text"}]}
OTHERS = {"strain": {"name": "Strain", "prefix": "ST", "list_columns": ["id", "name"], "fields": [{"key": "name", "label": "Name", "type": "text", "required": True}]}}
ROOT = {"username": "root", "uid": "root", "name": "Root", "role": "admin", "active": True}
ALICE = {"username": "alice", "uid": "alice", "name": "Alice", "role": "user", "active": True}


class TestPureEditing(unittest.TestCase):
    def valid(self, schema, slug="lipid", others=OTHERS, old=None):
        return se.validate_schema(slug, schema, {**others, slug: schema}, old=old)

    def test_slugs_and_keys(self):
        self.assertEqual(se.slugify("Lipid  Stocks!"), "lipid-stocks")
        self.assertEqual(se.slugify("  ###  "), "")
        self.assertEqual(se.key_from_label("Molecular weight (Da)"), "molecular_weight_da")
        self.assertEqual(se.key_from_label("3D structure"), "f_3d_structure")
        self.assertEqual(se.key_from_label("é"), "")

    def test_a_new_type_is_valid_and_minimal(self):
        t = se.new_type(" Lipid ", "lp", "teal")
        self.assertEqual((t["name"], t["prefix"], t["color"], t["list_columns"]), ("Lipid", "LP", "teal", ["id", "name", "created_by"]))
        self.assertEqual([f["key"] for f in t["fields"]], ["name"])
        self.assertEqual(self.valid(t), [])

    def test_every_field_type_can_be_added(self):
        t = se.new_type("Thing", "TH")
        t = se.add_field(t, "Count", "number")
        t = se.add_field(t, "Opened", "date")
        t = se.add_field(t, "Kind", "select", options="a\nb\n a \n")
        t = se.add_field(t, "Tags", "multiselect", options=["x", "y"])
        t = se.add_field(t, "Valid", "bool", required=True)
        t = se.add_field(t, "Where", "url")
        t = se.add_field(t, "Who", "user")
        t = se.add_field(t, "Parent", "ref", to="strain")
        t = se.add_field(t, "Anything", "ref")
        t = se.add_field(t, "Description", "longtext", required=True)
        t = se.add_field(t, "Double", "computed", expr="count * 2", key="double")
        by = {f["key"]: f for f in t["fields"]}
        self.assertEqual(by["kind"]["options"], ["a", "b"])
        self.assertEqual((by["parent"]["to"], by["parent"]["multiple"], by["anything"]["to"]), ("strain", True, "*"))
        self.assertNotIn("required", by["valid"])                                          # a yes/no field is never "required"
        self.assertTrue(by["description"]["required"])
        self.assertEqual(self.valid(t), [])

    def test_adding_fields_is_checked(self):
        t = copy.deepcopy(BASE)
        for kwargs, fragment in (({"label": "", "ftype": "text"}, "give the field a name"), ({"label": "X", "ftype": "weird"}, "field type"),
                                 ({"label": "Id", "ftype": "text"}, "reserved"), ({"label": "Name again", "ftype": "text", "key": "name"}, "reserved"),
                                 ({"label": "Kind", "ftype": "text"}, "already has"), ({"label": "Bad Key", "ftype": "text", "key": "Bad Key"}, "cannot be used"),
                                 ({"label": "Choice", "ftype": "select", "options": ""}, "at least one option"),
                                 ({"label": "Choice", "ftype": "select", "options": ["x" * 61]}, "60 characters")):
            with self.assertRaises(se.SchemaError, msg=str(kwargs)) as cm:
                se.add_field(t, **kwargs)
            self.assertIn(fragment, str(cm.exception))
        self.assertEqual(t, BASE)                                                           # the original is never modified

    def test_update_field_rules(self):
        t = copy.deepcopy(BASE)
        t2 = se.update_field(t, "note", label="Remarks", required=True)
        self.assertEqual((t2["fields"][3]["label"], t2["fields"][3]["required"]), ("Remarks", True))
        self.assertNotIn("required", se.update_field(t2, "note", required=False)["fields"][3])
        self.assertEqual(se.update_field(t, "note", ftype="longtext")["fields"][3]["type"], "longtext")
        with self.assertRaises(se.SchemaError):
            se.update_field(t, "note", ftype="number")                                      # no lossy type changes
        with self.assertRaises(se.SchemaError):
            se.update_field(t, "kind", label="  ")
        with self.assertRaises(se.SchemaError):
            se.update_field(t, "ghost", label="x")
        self.assertTrue(se.update_field(t, "name", required=False)["fields"][0]["required"])  # the title is always required

    def test_options_can_grow_and_shrink_but_not_while_used(self):
        t = copy.deepcopy(BASE)
        t2 = se.update_field(t, "kind", add_options="d\n a\n e", remove_options=["c"])
        self.assertEqual(t2["fields"][1]["options"], ["a", "b", "d", "e"])
        with self.assertRaises(se.SchemaError) as cm:
            se.update_field(t, "kind", remove_options=["a"], used_options={"a"})
        self.assertIn("used by existing records", str(cm.exception))
        with self.assertRaises(se.SchemaError):
            se.update_field(t, "kind", remove_options=["a", "b", "c"])                       # never empty
        with self.assertRaises(se.SchemaError):
            se.update_field(t, "note", add_options="x")                                       # not a choice field

    def test_formula_and_display_edits(self):
        t = se.add_field(copy.deepcopy(BASE), "Double", "computed", expr="1")
        self.assertEqual(se.update_field(t, "double", expr=" 2 + 2 ")["fields"][-1]["expr"], "2 + 2")
        with self.assertRaises(se.SchemaError):
            se.update_field(t, "note", expr="1")
        self.assertEqual(se.update_field(t, "note", display="outdated")["fields"][3]["display"], "outdated")
        self.assertNotIn("display", se.update_field(se.update_field(t, "note", display="outdated"), "note", display="full")["fields"][3])
        with self.assertRaises(se.SchemaError):
            se.update_field(t, "note", display="loud")

    def test_system_fields_are_protected(self):
        t = copy.deepcopy(BASE)
        t["fields"].append({"key": "legacy_owner", "label": "Legacy owner", "type": "text"})
        for key in ("legacy_owner",):
            with self.assertRaises(se.SchemaError):
                se.update_field(t, key, label="x")
            with self.assertRaises(se.SchemaError):
                se.set_hidden(t, key, True)
        self.assertEqual(self.valid(t, old=t), [])                                           # but they stay valid

    def test_the_imported_files_field_is_an_ordinary_file_field(self):
        t = copy.deepcopy(BASE)
        t["fields"].append({"key": "attachments", "label": "Files", "type": "file", "multiple": True})
        self.assertEqual(se.update_field(t, "attachments", label="Documents")["fields"][-1]["label"], "Documents")
        self.assertTrue(se.set_hidden(t, "attachments", True)["fields"][-1]["hidden"])
        self.assertEqual(self.valid(t, old=t), [])

    def test_move_and_hide(self):
        t = copy.deepcopy(BASE)
        self.assertEqual([f["key"] for f in se.move_field(t, "note", "up")["fields"]], ["name", "kind", "note", "tags"])
        self.assertEqual([f["key"] for f in se.move_field(t, "kind", "down")["fields"]], ["name", "tags", "kind", "note"])
        self.assertEqual([f["key"] for f in se.move_field(t, "kind", "up")["fields"]], ["name", "kind", "tags", "note"])   # at the top already: unchanged
        self.assertEqual([f["key"] for f in se.move_field(t, "note", "down")["fields"]], ["name", "kind", "tags", "note"])
        for bad in (("name", "down"), ("ghost", "up"), ("kind", "sideways")):
            with self.assertRaises(se.SchemaError):
                se.move_field(t, *bad)
        t["list_columns"] = ["id", "name", "kind", "created_by"]
        h = se.set_hidden(t, "kind", True)
        self.assertTrue(h["fields"][1]["hidden"])
        self.assertEqual(h["list_columns"], ["id", "name", "created_by"])
        self.assertNotIn("hidden", se.set_hidden(h, "kind", False)["fields"][1])
        with self.assertRaises(se.SchemaError):
            se.set_hidden(t, "name", True)

    def test_settings(self):
        t = copy.deepcopy(BASE)
        s = se.update_settings(t, name=" Lipids ", color="gold", list_columns=["note", "created_at", "kind", "bogus", "name"])
        self.assertEqual((s["name"], s["color"]), ("Lipids", "gold"))
        self.assertEqual(s["list_columns"], ["id", "name", "kind", "note", "created_at"])   # normalised order, unknown dropped
        self.assertNotIn("color", se.update_settings(s, color=""))
        with self.assertRaises(se.SchemaError):
            se.update_settings(t, name=" ")
        s = se.update_settings(t, permissions={"read": "admin", "create": "", "edit": "owner"})
        self.assertEqual(s["permissions"], {"read": "admin", "edit": "owner"})
        self.assertNotIn("permissions", se.update_settings(s, permissions={"read": "", "edit": ""}))

    def test_validation_reports_problems(self):
        def problems(mutate, **kw):
            t = copy.deepcopy(BASE)
            mutate(t)
            return " | ".join(self.valid(t, **kw))
        self.assertEqual(problems(lambda t: None), "")
        self.assertIn("two capital letters", problems(lambda t: t.update(prefix="L")))
        self.assertIn("two capital letters", problems(lambda t: t.update(prefix="lp")))
        self.assertIn("already used", problems(lambda t: t.update(prefix="ST")))
        self.assertIn("cannot be changed", problems(lambda t: t.update(prefix="XX"), old=BASE))
        self.assertIn("needs a name", problems(lambda t: t.update(name="")))
        self.assertIn("unknown colour", problems(lambda t: t.update(color="#fff")))
        self.assertIn("unknown permission", problems(lambda t: t.update(permissions={"read": "nobody"})))
        self.assertIn("title field", problems(lambda t: t["fields"].reverse()))
        self.assertIn("same key", problems(lambda t: t["fields"].append(dict(t["fields"][1]))))
        self.assertIn("needs options", problems(lambda t: t["fields"][1].update(options=[])))
        self.assertIn("unknown type", problems(lambda t: t["fields"][3].update(type="blob")))
        self.assertIn("unknown type 'ghost'", problems(lambda t: t["fields"].append({"key": "r", "label": "R", "type": "ref", "to": "ghost"})))
        self.assertIn("at least one column", problems(lambda t: t.update(list_columns=[])))
        self.assertIn("not a field", problems(lambda t: t.update(list_columns=["id", "nope"])))
        self.assertIn("hidden field", problems(lambda t: (t["fields"][3].update(hidden=True), t.update(list_columns=["id", "note"]))))
        self.assertIn("unknown name 'nope'", problems(lambda t: t["fields"].append({"key": "c", "label": "C", "type": "computed", "expr": "nope + 1"})))
        self.assertIn("unknown function", problems(lambda t: t["fields"].append({"key": "c", "label": "C", "type": "computed", "expr": "evil(1)"})))
        self.assertIn("reserved", problems(lambda t: t["fields"].append({"key": "owner", "label": "Owner", "type": "text"})))
        self.assertEqual(problems(lambda t: t["fields"].append({"key": "r", "label": "Self", "type": "ref", "to": "lipid"})), "")   # a type may link to itself


class TestStoreTypes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(self.repo, "config", "types"))
        os.makedirs(os.path.join(self.repo, "data"))
        with open(os.path.join(self.repo, "config", "types", "strain.yaml"), "w") as fh:
            json.dump(OTHERS["strain"], fh)
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True)
        subprocess.run(["git", "-C", self.repo, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"], check=True)
        self.store = Store(self.repo, users_lookup=lambda n: {"alice": ALICE, "root": ROOT}.get(n))

    def tearDown(self):
        self.tmp.cleanup()

    def commits(self):
        return int(self.store.git("rev-list", "--count", "HEAD"))

    def make_lipid(self):
        self.store.save_schema("lipid", copy.deepcopy(BASE), ROOT, create=True)

    def test_create_a_type_then_use_it(self):
        before = self.commits()
        self.make_lipid()
        self.assertEqual(self.commits(), before + 1)
        self.assertEqual(self.store.git("log", "-1", "--format=%an").strip(), "root")   # the user ID only
        self.assertEqual(self.store.git("status", "--porcelain").strip(), "")
        self.assertEqual(self.store.prefix_type["LP"], "lipid")
        rid = self.store.create("lipid", ALICE, "DOPC", {"kind": "a", "tags": ["x"]})
        self.assertEqual(rid, "LP-1")
        fresh = Store(self.repo)                                                             # survives a restart
        self.assertEqual((fresh.schemas["lipid"], fresh.records[rid]["fields"]["kind"]), (BASE, "a"))
        self.assertEqual(fresh.schema_warnings, [])
        with open(os.path.join(self.repo, "config", "types", "lipid.yaml")) as fh:
            self.assertTrue(fh.read().endswith("}\n"))

    def test_only_admins_and_only_valid_definitions(self):
        with self.assertRaises(Forbidden):
            self.store.save_schema("lipid", copy.deepcopy(BASE), ALICE, create=True)
        bad = copy.deepcopy(BASE)
        bad["prefix"] = "ST"
        before = self.commits()
        with self.assertRaises(ValidationError) as cm:
            self.store.save_schema("lipid", bad, ROOT, create=True)
        self.assertIn("already used", str(cm.exception))
        with self.assertRaises(ValidationError):
            self.store.save_schema("Bad Slug!", copy.deepcopy(BASE), ROOT, create=True)
        self.assertEqual(self.commits(), before)
        self.assertNotIn("lipid", self.store.schemas)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "config", "types", "lipid.yaml")))
        self.make_lipid()
        with self.assertRaises(ValidationError):
            self.store.save_schema("lipid", copy.deepcopy(BASE), ROOT, create=True)         # already exists
        with self.assertRaises(NotFound):
            self.store.save_schema("ghost", copy.deepcopy(BASE), ROOT)

    def test_changing_a_type_keeps_the_records(self):
        self.make_lipid()
        rid = self.store.create("lipid", ALICE, "DOPC", {"kind": "a", "note": "n"})
        schema = se.add_field(self.store.schemas["lipid"], "Mass", "number")
        schema = se.update_field(schema, "kind", add_options="d")
        schema = se.update_settings(schema, color="pink", list_columns=["kind", "mass"])
        before = self.commits()
        self.store.save_schema("lipid", schema, ROOT)
        self.assertEqual(self.commits(), before + 1)
        self.assertEqual(self.store.records[rid]["fields"], {"kind": "a", "note": "n"})
        rid2 = self.store.create("lipid", ALICE, "POPC", {"kind": "d", "mass": "760.1"})
        self.assertEqual(self.store.records[rid2]["fields"]["mass"], 760.1)
        self.assertEqual(self.store.schemas["lipid"]["list_columns"], ["id", "name", "kind", "mass"])
        self.assertEqual(self.store.used_options("lipid", "kind"), {"a", "d"})

    def test_hidden_fields_are_not_edited_validated_or_searched(self):
        self.make_lipid()
        rid = self.store.create("lipid", ALICE, "DOPC", {"note": "findablesecret"})
        idx = Index(self.store, lambda n: None)
        self.assertEqual([d["id"] for d in idx.search("findablesecret")], [rid])
        schema = se.set_hidden(self.store.schemas["lipid"], "note", True)
        schema = se.update_field(schema, "note", required=True)
        self.store.save_schema("lipid", schema, ROOT)
        self.assertEqual(idx.search("findablesecret"), [])                                   # hidden data is not searchable
        self.store.update(rid, ALICE, "DOPC", {"note": "changed"}, "", self.store.records[rid]["_version"])
        self.assertEqual(self.store.records[rid]["fields"]["note"], "findablesecret")        # hidden fields cannot be edited through forms
        self.assertTrue(self.store.create("lipid", ALICE, "no note", {}))                    # and a hidden "required" field is not enforced
        self.store.save_schema("lipid", se.set_hidden(self.store.schemas["lipid"], "note", False), ROOT)
        self.assertEqual([d["id"] for d in idx.search("findablesecret")], [rid])             # showing it again brings everything back

    def test_remove_a_type(self):
        self.make_lipid()
        rid = self.store.create("lipid", ALICE, "DOPC", {})
        with self.assertRaises(ValidationError) as cm:
            self.store.delete_type("lipid", ROOT)
        self.assertIn("1 record(s)", str(cm.exception))
        self.assertIn("never deleted", str(cm.exception))
        purge(self.store, rid, ALICE)
        linker = copy.deepcopy(OTHERS["strain"])
        linker["fields"].append({"key": "l", "label": "Lipid", "type": "ref", "to": "lipid"})
        self.store.save_schema("strain", linker, ROOT)
        with self.assertRaises(ValidationError) as cm:
            self.store.delete_type("lipid", ROOT)
        self.assertIn("links to it", str(cm.exception))
        self.store.save_schema("strain", copy.deepcopy(OTHERS["strain"]), ROOT)
        with self.assertRaises(Forbidden):
            self.store.delete_type("lipid", ALICE)
        before = self.commits()
        self.store.delete_type("lipid", ROOT)
        self.assertEqual(self.commits(), before + 1)
        self.assertNotIn("lipid", self.store.schemas)
        self.assertNotIn("LP", self.store.prefix_type)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "config", "types", "lipid.yaml")))
        self.assertNotIn("lipid", Store(self.repo).schemas)
        with self.assertRaises(NotFound):
            self.store.delete_type("lipid", ROOT)

    def test_the_prefix_of_an_existing_type_is_fixed_and_unique(self):
        self.make_lipid()
        changed = copy.deepcopy(self.store.schemas["lipid"])
        changed["prefix"] = "ZZ"
        with self.assertRaises(ValidationError):
            self.store.save_schema("lipid", changed, ROOT)
        second = copy.deepcopy(BASE)
        with self.assertRaises(ValidationError):
            self.store.save_schema("lipid2", second, ROOT, create=True)                      # same prefix LP as the first
        second["prefix"] = "L2"
        with self.assertRaises(ValidationError):
            self.store.save_schema("lipid2", second, ROOT, create=True)                      # digits are not allowed in a prefix
        second["prefix"] = "LQ"
        self.store.save_schema("lipid2", second, ROOT, create=True)
        self.assertEqual(self.store.create("lipid2", ALICE, "x", {}), "LQ-1")


if __name__ == "__main__":
    unittest.main()

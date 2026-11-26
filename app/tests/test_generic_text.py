"""The shared text of the product names no group's types or history: every page and the assistant's text read right for any instance.

Pages are rendered for a made-up instance whose types are Books and Tools; none may mention strains, plasmids or the system the first
instance came from, nor show example IDs with another instance's prefixes. A group's own wording belongs in its data repository
(type definitions, config/checks/<type>.yaml, config/help.md), never in the code."""
import ast, json, pathlib, re

from shelfblazer.recordfile import parse_record, serialize_record
from test_app import Base

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORDS = ("strain", "plasmid", "primer", "genotype", "glycerol", "reagent", "labcollector", "old database")
FOREIGN_IDS = re.compile(r"\b(?:ST|BS|PL|PR|CH|CS|MM|MP|DC)-P?\d")         # prefixes of other instances (here: BK and TL)
BOOK = {"name": "Book", "prefix": "BK", "list_columns": ["id", "name", "author"], "scan_fields": ["code"], "permissions": {"personal": "all"},
        "fields": [{"key": "name", "label": "Name", "type": "text", "required": True},
                   {"key": "author", "label": "Author", "type": "text"},
                   {"key": "code", "label": "Code", "type": "text"},
                   {"key": "shelf", "label": "Shelf", "type": "select", "options": ["A", "B"]}]}
TOOL = {"name": "Tool", "prefix": "TL", "list_columns": ["id", "name", "manual"],
        "fields": [{"key": "name", "label": "Name", "type": "text", "required": True},
                   {"key": "manual", "label": "Manual", "type": "ref", "to": "book", "multiple": True},
                   {"key": "legacy_owner", "label": "Keeper before the move", "type": "text"}]}


def problems(text):
    low = text.lower()
    return [w for w in WORDS if w in low] + FOREIGN_IDS.findall(text)


class Pages(Base):
    def setUp(self):
        super().setUp()
        root = self.users.get("root")
        for slug in ("strain", "plasmid"):
            self.store.delete_type(slug, root)
        self.store.save_schema("book", BOOK, root, create=True)
        self.store.save_schema("tool", dict(TOOL, fields=[f for f in TOOL["fields"] if f["key"] != "legacy_owner"]), root, create=True)
        path = pathlib.Path(self.repo) / "config" / "types" / "tool.yaml"             # an imported type carries the system field,
        path.write_text(json.dumps(TOOL), encoding="utf-8")                              # which only an import (not the app) can add
        self.store.commit([str(path)], "import: tool type", root)
        self.store.reload()
        self.book = self.create("alice", "book", name="A field guide", code="4006381333931")
        self.tool = self.create("alice", "tool", name="Ladder", manual=self.book)
        rec_path = pathlib.Path(self.store.path_of(self.tool, "tool"))                # the keeper too comes only from an import
        rec = parse_record(rec_path.read_text(encoding="utf-8"))
        rec["fields"]["legacy_owner"] = "someone"
        rec_path.write_text(serialize_record(rec, [f["key"] for f in TOOL["fields"]]), encoding="utf-8")
        self.store.commit([str(rec_path)], "import: keeper", root)
        self.store.reload()

    def test_no_page_names_another_groups_types_or_history(self):
        pages = ["/", "/?q=guide", "/t/book", "/t/book?c=mine", "/t/book/new", "/t/tool/new", f"/r/{self.book}", f"/r/{self.tool}",
                 f"/r/{self.tool}/edit", "/t/book/import", "/scan?c=123", "/help", "/me",
                 "/admin/types", "/admin/types/book", "/admin/types/tool", "/admin/users", "/admin/help"]
        root = self.client("root")
        found = {}
        for path in pages:
            r = root.get(path)
            self.assertEqual(r.status_code, 200, path)
            text = re.sub(r"<script.*?</script>|<style.*?</style>", "", r.text, flags=re.S)
            if problems(text):
                found[path] = problems(text)
        self.assertEqual(found, {})

    def test_the_previous_owner_label_comes_from_the_type_definition(self):
        page = self.client("root").get(f"/r/{self.tool}").text
        self.assertIn("(Keeper before the move: someone)", page)

    def test_examples_use_this_instances_prefixes_and_types(self):
        root = self.client("root")
        self.assertIn("e.g. BK-142, BK-12", root.get("/t/tool/new").text)
        admin = root.get("/admin/types/book").text
        self.assertIn("count(referenced_by('tool'))", admin)                     # a type that links to books
        self.assertIn("values(refs('manual'), 'author')", root.get("/admin/types/tool").text)


class AssistantText(Base):
    def test_the_assistants_instructions_and_tool_descriptions_are_generic(self):
        from shelfblazer_app import mcp_server
        texts = list(mcp_server.INSTRUCTIONS.values())
        tree = ast.parse((ROOT / "app" / "shelfblazer_app" / "mcp_tools.py").read_text(encoding="utf-8"))
        texts += [ast.get_docstring(n) or "" for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.Module))]
        self.assertEqual([p for t in texts for p in problems(t)], [])


class Code(Base):
    def test_the_code_does_not_name_the_system_the_first_instance_came_from(self):
        bad = [str(p.relative_to(ROOT)) for folder in ("app/shelfblazer_app", "packages/shelfblazer")
               for p in (ROOT / folder).rglob("*") if p.is_file() and p.suffix in (".py", ".html", ".js", ".css")
               and re.search("labcollector|old database", p.read_text(encoding="utf-8", errors="ignore"), re.I)]
        self.assertEqual(bad, [])

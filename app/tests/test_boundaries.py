"""Which module may import which: the boundaries that keep features apart.

- The core (``packages/shelfblazer``) knows nothing of the web: no ``shelfblazer_app``, no web framework, no MCP library.
- In the core, imports made when a module loads form no cycle. Imports made inside a function (to break a cycle) are listed in
  ``CORE_LAZY``, so a new one is a decision, not an accident.
- In the web app, a feature (a module that registers pages or tools) imports the core and the shared helpers, not another feature.
  The exceptions of today are listed in ``FEATURE_LINKS``; a new link fails here until it is listed with its reason, and a link that
  is gone must be taken off the list.

Each failure message says which module and which import."""
import ast, pathlib, unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CORE, APP = ROOT / "packages" / "shelfblazer", ROOT / "app" / "shelfblazer_app"

WEB_LIBRARIES = {"fastapi", "starlette", "jinja2", "mcp", "uvicorn", "shelfblazer_app"}
HELPERS = {"__init__", "views", "cells", "format", "idlabels"}         # shared by every page
ENTRY = {"web", "cli", "__main__"}                                      # build the app from all features, so they import them all

# (importer, imported): why the feature needs the other one
FEATURE_LINKS = {
    ("lists", "scan"): "the list search finds records by a scanned code",
    ("records", "scan"): "a new-record form arriving from a scan has the code filled in",
    ("records", "files"): "the record form shares the file-upload limits",
    ("middleware", "files"): "the body-size limit allows uploads up to the file limits",
    ("mcp_server", "mcp_tools"): "the endpoint serves the tools",
}
# (importer, imported): imports inside a function in the core, each breaking a cycle
CORE_LAZY = {
    ("store", "maintenance"): "maintenance imports store's constants",
    ("store", "tableimport"): "tableimport works on the store",
    ("schema_edit", "store"): "store validates schemas with schema_edit",
}


def imports(path, package, own):
    """[(name, lazy)]: the modules of ``own`` (or outside packages) that ``path`` imports; lazy = inside a function."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lazy_nodes = {id(n) for f in ast.walk(tree) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(f)}
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(a.name, id(node) in lazy_nodes) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            lazy = id(node) in lazy_nodes
            if node.level:                                              # from . import x / from .x import y
                if node.module:
                    out.append((f"{own}.{node.module}", lazy))
                else:
                    out += [(f"{own}.{a.name}", lazy) for a in node.names]
            elif node.module == package:                                # from shelfblazer import ids, perms
                out += [(f"{package}.{a.name}", lazy) for a in node.names]
            else:
                out.append((node.module, lazy))
    return out


def modules(folder):
    return {p.stem: p for p in sorted(folder.glob("*.py"))}


class Core(unittest.TestCase):
    def test_the_core_imports_nothing_of_the_web(self):
        bad = [f"{name}.py imports {mod}" for name, path in modules(CORE).items()
               for mod, _ in imports(path, "shelfblazer", "shelfblazer") if mod.split(".")[0] in WEB_LIBRARIES]
        self.assertEqual(bad, [])

    def test_imports_at_load_time_form_no_cycle(self):
        graph = {name: {m.split(".")[1] for m, lazy in imports(path, "shelfblazer", "shelfblazer")
                        if not lazy and m.startswith("shelfblazer.") and m.split(".")[1] != name}
                 for name, path in modules(CORE).items()}
        state, cycle = {}, []

        def visit(n, trail):
            state[n] = "open"
            for m in sorted(graph.get(n, ())):
                if state.get(m) == "open":
                    cycle.append(" -> ".join(trail + [n, m]))
                elif m in graph and m not in state:
                    visit(m, trail + [n])
            state[n] = "done"
        for n in graph:
            if n not in state:
                visit(n, [])
        self.assertEqual(cycle, [])

    def test_imports_inside_functions_are_the_listed_ones(self):
        found = {(name, m.split(".")[1]) for name, path in modules(CORE).items()
                 for m, lazy in imports(path, "shelfblazer", "shelfblazer") if lazy and m.startswith("shelfblazer.")}
        self.assertEqual(sorted(found - CORE_LAZY.keys()), [], "a new import inside a function: list it in CORE_LAZY with its reason")
        self.assertEqual(sorted(CORE_LAZY.keys() - found), [], "this import is gone: take it off CORE_LAZY")


class App(unittest.TestCase):
    def test_features_import_only_the_core_the_helpers_and_the_listed_features(self):
        names = set(modules(APP))
        found = set()
        for name, path in modules(APP).items():
            if name in ENTRY or name in HELPERS:
                continue
            for m, _ in imports(path, "shelfblazer_app", "shelfblazer_app"):
                other = m.split(".")[1] if m.startswith("shelfblazer_app.") else None
                if other and other in names and other not in HELPERS and other != name:
                    found.add((name, other))
        self.assertEqual(sorted(found - FEATURE_LINKS.keys()), [],
                         "a feature imports another feature: move what it needs to the core or a helper, or list it in FEATURE_LINKS")
        self.assertEqual(sorted(FEATURE_LINKS.keys() - found), [], "this link is gone: take it off FEATURE_LINKS")

    def test_helpers_import_no_feature(self):
        names = set(modules(APP))
        bad = [f"{name}.py imports {m}" for name in sorted(HELPERS) for m, _ in imports(APP / f"{name}.py", "shelfblazer_app", "shelfblazer_app")
               if m.startswith("shelfblazer_app.") and m.split(".")[1] in names - HELPERS]
        self.assertEqual(bad, [])

    def test_only_the_entry_points_build_the_app(self):
        bad = [f"{name}.py imports web" for name, path in modules(APP).items() if name not in ENTRY
               for m, _ in imports(path, "shelfblazer_app", "shelfblazer_app") if m == "shelfblazer_app.web"]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()

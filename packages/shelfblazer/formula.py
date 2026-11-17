"""Computed fields: a tiny, safe formula language.

A formula is a single expression such as  count(referenced_by('strain'))  or  join(', ', unique(flatten(values(refs('plasmid'), 'resistance')))).
It is parsed with ``ast`` but NEVER compiled or passed to ``eval``: this module walks the syntax tree itself and only accepts a short whitelist
of constructs. There is no attribute access, no indexing, no names starting with an underscore, no lambdas, comprehensions, f-strings,
imports or assignments, no exponent operator, and size, depth and result length are bounded. Any runtime problem (wrong type, division by
zero, unknown function) gives an empty result, never an exception that could reach a page.

Names in a formula are the record's own field keys (empty when the record has no value) plus: id, name, type, owner, created, updated, archived.
Functions: see FUNCTIONS below.
"""
import ast, datetime, functools, operator

MAX_EXPR_CHARS = 500
MAX_NODES = 120
MAX_DEPTH = 24
MAX_STRING = 5000
MAX_LIST = 2000
BUILTIN_NAMES = ("id", "name", "type", "owner", "created", "updated", "archived")


class FormulaError(Exception):
    pass


_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
        ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}
_ALLOWED = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.BinOp, ast.UnaryOp, ast.USub, ast.UAdd, ast.Not, ast.Compare, ast.IfExp,
            ast.Constant, ast.Name, ast.Load, ast.Call, ast.List) + tuple(_BIN) + tuple(_CMP)


def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _date(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------------------------- functions
# Each takes (env, *args). env is the evaluation environment (see evaluate()).
def _f_count(env, x):
    return len(x) if isinstance(x, (list, str)) else (0 if x is None else 1)

def _f_sum(env, x):
    return sum(v for v in (x or []) if _is_number(v))

def _f_min(env, x):
    nums = [v for v in (x or []) if _is_number(v)]
    return min(nums) if nums else None

def _f_max(env, x):
    nums = [v for v in (x or []) if _is_number(v)]
    return max(nums) if nums else None

def _f_round(env, x, n=0):
    if not _is_number(x) or not isinstance(n, int) or not 0 <= n <= 6:
        raise FormulaError("round needs a number and 0-6 digits")
    r = round(x, n)
    return int(r) if n == 0 else r

def _f_abs(env, x):
    if not _is_number(x):
        raise FormulaError("abs needs a number")
    return abs(x)

def _f_str(env, x):
    return format_value(x)

def _f_upper(env, x):
    return str(x).upper() if x is not None else None

def _f_lower(env, x):
    return str(x).lower() if x is not None else None

def _f_strip(env, x):
    return str(x).strip() if x is not None else None

def _f_join(env, sep, items):
    if not isinstance(sep, str):
        raise FormulaError("join needs a text separator")
    return sep.join(format_value(i) for i in (items or []) if i not in (None, ""))

def _f_concat(env, *parts):
    return "".join(format_value(p) for p in parts if p is not None)

def _f_default(env, x, fallback):
    return fallback if x in (None, "", []) else x

def _f_unique(env, items):
    out = []
    for i in (items or []):
        if i not in out:
            out.append(i)
    return out

def _f_flatten(env, items):
    out = []
    for i in (items or []):
        out.extend(i if isinstance(i, list) else [i])
    return out

def _f_contains(env, haystack, needle):
    if haystack is None:
        return False
    return needle in haystack

def _f_values(env, records, key):
    if not isinstance(key, str):
        raise FormulaError("values needs a field key")
    return [r["fields"].get(key) for r in (records or []) if isinstance(r, dict) and r["fields"].get(key) not in (None, "", [])]

def _f_names(env, records):
    return [r["name"] for r in (records or []) if isinstance(r, dict)]

def _f_ids(env, records):
    return [r["id"] for r in (records or []) if isinstance(r, dict)]

def _f_refs(env, key):
    """The records this record points to through one of its reference fields (only those the viewer may read)."""
    rec, store = env["rec"], env["store"]
    out = []
    for rid in rec["fields"].get(key, []) if isinstance(key, str) else []:
        target = store.records.get(rid)
        if target is not None and env["visible"](target):
            out.append(target)
    return out

def _f_referenced_by(env, type_slug=None, key=None):
    """Records (of one type, optionally through one field) that point to this record."""
    out = []
    for label, r in env["store"].referenced_by(env["rec"]["id"]):
        if type_slug and r["type"] != type_slug:
            continue
        if key and not any(f["key"] == key and f["label"] == label for f in env["store"].schemas[r["type"]]["fields"]):
            continue
        if env["visible"](r) and r not in out:
            out.append(r)
    return out

def _f_age_days(env):
    d = _date(env["rec"].get("created_at"))
    return (env["today"] - d).days if d else None

def _f_updated_days(env):
    d = _date(env["rec"].get("updated_at")) or _date(env["rec"].get("created_at"))
    return (env["today"] - d).days if d else None

def _f_days_since(env, when):
    d = _date(when)
    return (env["today"] - d).days if d else None


FUNCTIONS = {
    # name: (function, min args, max args)
    "count": (_f_count, 1, 1), "sum": (_f_sum, 1, 1), "min": (_f_min, 1, 1), "max": (_f_max, 1, 1), "round": (_f_round, 1, 2), "abs": (_f_abs, 1, 1),
    "str": (_f_str, 1, 1), "upper": (_f_upper, 1, 1), "lower": (_f_lower, 1, 1), "strip": (_f_strip, 1, 1),
    "join": (_f_join, 2, 2), "concat": (_f_concat, 1, 12), "default": (_f_default, 2, 2), "unique": (_f_unique, 1, 1), "flatten": (_f_flatten, 1, 1),
    "contains": (_f_contains, 2, 2), "values": (_f_values, 2, 2), "names": (_f_names, 1, 1), "ids": (_f_ids, 1, 1),
    "refs": (_f_refs, 1, 1), "referenced_by": (_f_referenced_by, 0, 2),
    "age_days": (_f_age_days, 0, 0), "updated_days": (_f_updated_days, 0, 0), "days_since": (_f_days_since, 1, 1),
}


# ---------------------------------------------------------------------------------------------- parsing and checking
@functools.lru_cache(maxsize=512)
def compile_expr(expr):
    """Parse and check an expression. Returns (tree, None) or (None, error message). Cached."""
    if not isinstance(expr, str) or not expr.strip():
        return None, "the formula is empty"
    if len(expr) > MAX_EXPR_CHARS:
        return None, f"the formula is longer than {MAX_EXPR_CHARS} characters"
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None, "the formula is not valid (check brackets, quotes and commas)"
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        return None, "the formula is too complicated"
    for n in nodes:
        if not isinstance(n, _ALLOWED):
            return None, f"'{type(n).__name__}' is not allowed in a formula"
        if isinstance(n, ast.Constant) and not (n.value is None or isinstance(n.value, (bool, int, float, str))):
            return None, "only text, numbers, true/false and none can be written in a formula"
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) > 200:
            return None, "a text in the formula is too long"
        if isinstance(n, ast.Constant) and _is_number(n.value) and abs(n.value) > 1e12:
            return None, "a number in the formula is too large"
        if isinstance(n, ast.Name) and (n.id.startswith("_") or "__" in n.id):
            return None, f"the name '{n.id}' is not allowed"
        if isinstance(n, ast.Call):
            if not isinstance(n.func, ast.Name) or n.func.id not in FUNCTIONS:
                return None, "unknown function" + (f" '{n.func.id}'" if isinstance(n.func, ast.Name) else "")
            if n.keywords:
                return None, "named arguments are not supported"
            lo, hi = FUNCTIONS[n.func.id][1:]
            if not lo <= len(n.args) <= hi:
                return None, f"{n.func.id}() takes {lo if lo == hi else f'{lo} to {hi}'} argument(s)"
            if any(isinstance(a, ast.Starred) for a in n.args):
                return None, "starred arguments are not supported"
    if _depth(tree) > MAX_DEPTH:
        return None, "the formula is nested too deeply"
    return tree, None


def _depth(node):
    return 1 + max((_depth(c) for c in ast.iter_child_nodes(node)), default=0)


def validate(expr, field_keys=None):
    """None when the formula is acceptable, else a sentence for a person. With field_keys, unknown names are reported as typos."""
    tree, err = compile_expr(expr)
    if err:
        return err
    if field_keys is not None:
        called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call)}
        allowed = set(field_keys) | set(BUILTIN_NAMES)
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and n.id not in called and n.id not in allowed and n.id not in ("True", "False", "None"):
                return f"unknown name '{n.id}' (fields of this type: {', '.join(sorted(field_keys)) or 'none'})"
    return None


# ---------------------------------------------------------------------------------------------- evaluation
def evaluate(expr, rec, store, visible=None, today=None):
    """-> (value, error). value is None, bool, number, text or a list of those; records in a result are shown as their IDs."""
    tree, err = compile_expr(expr)
    if err:
        return None, err
    env = {"rec": rec, "store": store, "visible": visible or (lambda r: True), "today": today or datetime.date.today(), "steps": 0,
           "vars": {**rec["fields"], "id": rec["id"], "name": rec["name"], "type": rec["type"], "owner": store.person_name(rec["created_by"]) if hasattr(store, "person_name") else rec["created_by"],
                    "created": rec.get("created_at"), "updated": rec.get("updated_at"), "archived": bool(rec.get("archived"))}}
    try:
        return _out(_ev(tree.body, env, 0)), None
    except FormulaError as e:
        return None, str(e)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError, KeyError, RecursionError, AttributeError, IndexError):
        return None, "the formula could not be calculated for this record"


def _out(v):
    if isinstance(v, dict) and "id" in v:
        return v["id"]
    if isinstance(v, list):
        return [_out(i) for i in v[:MAX_LIST]]
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    raise FormulaError("the result is not a value that can be shown")


def _ev(node, env, depth):
    env["steps"] += 1
    if env["steps"] > 5000 or depth > 60:
        raise FormulaError("the formula is too expensive")
    t = type(node)
    if t is ast.Constant:
        return node.value
    if t is ast.Name:
        if node.id in ("True", "False", "None"):
            return {"True": True, "False": False, "None": None}[node.id]
        return env["vars"].get(node.id)
    if t is ast.BoolOp:
        result = None
        for v in node.values:
            result = _ev(v, env, depth + 1)
            if (isinstance(node.op, ast.And) and not result) or (isinstance(node.op, ast.Or) and result):
                return result
        return result
    if t is ast.UnaryOp:
        v = _ev(node.operand, env, depth + 1)
        if isinstance(node.op, ast.Not):
            return not v
        if not _is_number(v):
            raise FormulaError("a sign needs a number")
        return -v if isinstance(node.op, ast.USub) else +v
    if t is ast.BinOp:
        a, b = _ev(node.left, env, depth + 1), _ev(node.right, env, depth + 1)
        op = type(node.op)
        if op is ast.Add and isinstance(a, str) and isinstance(b, str):
            if len(a) + len(b) > MAX_STRING:
                raise FormulaError("the text becomes too long")
            return a + b
        if op is ast.Add and isinstance(a, list) and isinstance(b, list):
            return (a + b)[:MAX_LIST]
        if not (_is_number(a) and _is_number(b)):
            raise FormulaError("arithmetic needs numbers (or text with +)")
        r = _BIN[op](a, b)
        if _is_number(r) and abs(r) > 1e15:
            raise FormulaError("the number becomes too large")
        return r
    if t is ast.Compare:
        left = _ev(node.left, env, depth + 1)
        for op, comp in zip(node.ops, node.comparators):
            right = _ev(comp, env, depth + 1)
            if not _CMP[type(op)](left, right):
                return False
            left = right
        return True
    if t is ast.IfExp:
        return _ev(node.body if _ev(node.test, env, depth + 1) else node.orelse, env, depth + 1)
    if t is ast.List:
        return [_ev(e, env, depth + 1) for e in node.elts][:MAX_LIST]
    if t is ast.Call:
        fn, _, _ = FUNCTIONS[node.func.id]
        return fn(env, *[_ev(a, env, depth + 1) for a in node.args])
    raise FormulaError("not allowed in a formula")


# ---------------------------------------------------------------------------------------------- display
def format_value(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    if isinstance(v, list):
        return ", ".join(format_value(i) for i in v)
    return str(v)


def sort_key(v):
    """Comparable key for list sorting: numbers before text, empty last handled by the caller (returns '')."""
    if v is None or v == "" or v == []:
        return ""
    if _is_number(v):
        return (0, float(v))
    return (1, format_value(v).lower())

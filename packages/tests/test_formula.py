"""Computed fields: the safe formula language (security first), its functions, and the store integration."""
import datetime, json, os, subprocess, tempfile, time, unittest

from helpers import ALICE, purge
from shelfblazer import formula
from shelfblazer.store import Store

PLASMID = {"name": "Plasmid", "prefix": "PL", "list_columns": ["id", "name"], "fields": [
    {"key": "name", "label": "Name", "type": "text", "required": True},
    {"key": "resistance", "label": "Resistance", "type": "multiselect", "options": ["Kan", "Amp", "Cm"]},
    {"key": "size", "label": "Size", "type": "number"},
    {"key": "n_strains", "label": "Strains", "type": "computed", "expr": "count(referenced_by('strain'))"}]}
STRAIN = {"name": "Strain", "prefix": "ST", "list_columns": ["id", "name"], "fields": [
    {"key": "name", "label": "Name", "type": "text", "required": True},
    {"key": "genotype", "label": "Genotype", "type": "text"},
    {"key": "plasmid", "label": "Plasmid", "type": "ref", "to": "plasmid", "multiple": True},
    {"key": "resist", "label": "Resistance", "type": "computed", "expr": "join(', ', unique(flatten(values(refs('plasmid'), 'resistance'))))"},
    {"key": "age", "label": "Age (days)", "type": "computed", "expr": "age_days()"}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(self.repo, "config", "types"))
        os.makedirs(os.path.join(self.repo, "data"))
        for slug, sch in (("plasmid", PLASMID), ("strain", STRAIN)):
            with open(os.path.join(self.repo, "config", "types", slug + ".yaml"), "w") as fh:
                json.dump(sch, fh)
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True)
        subprocess.run(["git", "-C", self.repo, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"], check=True)
        self.store = Store(self.repo)
        self.store.today = lambda: datetime.date(2026, 11, 10)

    def tearDown(self):
        self.tmp.cleanup()

    def ev(self, expr, rec=None):
        rec = rec or {"id": "ST-1", "type": "strain", "name": "n", "created_by": "alice", "created_at": "2026-11-05T10:00:00", "updated_at": "2026-11-08T10:00:00",
                      "archived": False, "legacy_id": 1, "fields": {"genotype": "wt", "size": 5}, "notes": ""}
        return formula.evaluate(expr, rec, self.store, today=datetime.date(2026, 11, 10))


class TestSecurity(Base):
    HOSTILE = [
        "().__class__", "[].__len__()", "''.join([])", "__import__('os').system('echo hi')", "open('/etc/passwd')", "eval('1')", "exec('x=1')",
        "compile('1','a','eval')", "globals()", "locals()", "getattr(1,'real')", "type(1)", "print(1)", "input()", "lambda: 1", "[x for x in [1]]",
        "{1: 2}", "{1}", "(1, 2)", "f'{1}'", "(x := 1)", "a.b", "a[0]", "a[0:1]", "'x' if 1 else open('f')", "1 ** 2", "2 ** 100000", "b'bytes'", "...", "1j",
        "await x", "x if", "count(", "count(*[1])", "round(1, n=1)", "_private", "__class__", "a__b", "name.__class__", "count.__call__",
        "[__import__('os')][0]", "not_a_function(1)", "ids ()", "import os", "x = 1", "del x", "pass", "yield 1", "assert 1",
        "1; 2", "1\n2", "@x", "x @ y", "~1", "1 << 2", "1 & 2", "1 | 2", "1 ^ 2", "1 is 1", "1 is not 2",
    ]

    def test_hostile_expressions_are_rejected_by_validation_and_never_run(self):
        for expr in self.HOSTILE:
            self.assertIsNotNone(formula.validate(expr), expr)
            value, err = self.ev(expr)
            self.assertIsNone(value, expr)
            self.assertIsNotNone(err, expr)

    def test_size_and_depth_limits(self):
        self.assertIsNotNone(formula.validate("1+" * 300 + "1"))
        self.assertIsNotNone(formula.validate("-" * 100 + "1"))                                      # 100 nested signs
        self.assertIsNone(formula.validate("(" * 200 + "1" + ")" * 200))                           # brackets alone add no depth and are harmless
        self.assertIsNotNone(formula.validate("x" * 600))
        self.assertIsNotNone(formula.validate("'" + "a" * 300 + "'"))
        self.assertIsNotNone(formula.validate("1e13"))
        self.assertIsNotNone(formula.validate("0" * 5000))
        deep = "count(" * 30 + "1" + ")" * 30
        self.assertIsNotNone(formula.validate(deep))

    def test_runtime_problems_give_an_empty_result_not_an_exception(self):
        for expr in ("1 / 0", "1 // 0", "1 % 0", "'a' % 3", "'a' + 1", "'a' * 3", "[1] * 2", "-'a'", "abs('x')", "round('x')", "round(1.5, 99)", "1 < 'a'",
                     "99999999999 * 99999999999 * 99999999999", "unknown_field + 1", "min('x')", "join(1, [])", "values(5, 'x')"):
            value, err = self.ev(expr)
            self.assertIsNone(value, expr)

    def test_no_side_effects_on_the_record(self):
        rec = {"id": "ST-1", "type": "strain", "name": "n", "created_by": "a", "created_at": None, "updated_at": None, "archived": False, "legacy_id": 1,
               "fields": {"tags": ["a"]}, "notes": ""}
        before = json.dumps(rec, sort_keys=True)
        for expr in ("unique(tags)", "flatten([tags, tags])", "tags + ['b']", "join('-', tags)"):
            self.ev(expr, rec)
        self.assertEqual(json.dumps(rec, sort_keys=True), before)

    def test_string_growth_is_bounded(self):
        value, _ = self.ev("'" + "a" * 190 + "'" + " + '" + "b" * 190 + "'")
        self.assertEqual(len(value), 380)
        big = "(" + " + ".join(["'" + "z" * 190 + "'"] * 40) + ")"          # 40 x 190 = 7600 > the limit
        value, err = self.ev(big)
        self.assertIsNone(value)

    def test_names_cannot_reach_python(self):
        for name in ("__builtins__", "__name__", "_x", "x__y"):
            self.assertIsNotNone(formula.validate(name), name)


class TestLanguage(Base):
    def test_arithmetic_comparison_logic_and_text(self):
        self.assertEqual(self.ev("1 + 2 * 3")[0], 7)
        self.assertEqual(self.ev("(1 + 2) * 3 - 4 / 2")[0], 7.0)
        self.assertEqual(self.ev("7 // 2")[0], 3)
        self.assertEqual(self.ev("7 % 4")[0], 3)
        self.assertEqual(self.ev("-size")[0], -5)
        self.assertEqual(self.ev("size > 3 and size < 10")[0], True)
        self.assertEqual(self.ev("not (size > 3)")[0], False)
        self.assertEqual(self.ev("1 < size < 10")[0], True)
        self.assertEqual(self.ev("'big' if size > 3 else 'small'")[0], "big")
        self.assertEqual(self.ev("genotype + '!'")[0], "wt!")
        self.assertEqual(self.ev("genotype == 'wt'")[0], True)
        self.assertEqual(self.ev("'t' in genotype")[0], True)
        self.assertEqual(self.ev("missing or 'fallback'")[0], "fallback")
        self.assertIsNone(self.ev("None")[0])
        self.assertEqual(self.ev("[1, 2] + [3]")[0], [1, 2, 3])

    def test_builtin_names_and_text_helpers(self):
        self.assertEqual(self.ev("id")[0], "ST-1")
        self.assertEqual(self.ev("owner")[0], "alice")
        self.assertEqual(self.ev("concat(id, ' / ', name)")[0], "ST-1 / n")
        self.assertEqual(self.ev("upper(genotype)")[0], "WT")
        self.assertEqual(self.ev("lower('AbC')")[0], "abc")
        self.assertEqual(self.ev("strip('  x ')")[0], "x")
        self.assertEqual(self.ev("default(missing, 'n/a')")[0], "n/a")
        self.assertEqual(self.ev("default(genotype, 'n/a')")[0], "wt")
        self.assertEqual(self.ev("round(2.567, 1)")[0], 2.6)
        self.assertEqual(self.ev("round(2.5)")[0], 2)
        self.assertEqual(self.ev("abs(-3)")[0], 3)
        self.assertEqual((self.ev("sum([1, 2, 3])")[0], self.ev("min([4, 2])")[0], self.ev("max([4, 2])")[0], self.ev("count([1, 2])")[0]), (6, 2, 4, 2))
        self.assertEqual(self.ev("contains([1, 2], 2)")[0], True)
        self.assertEqual(self.ev("str(3.5)")[0], "3.5")

    def test_dates(self):
        self.assertEqual(self.ev("age_days()")[0], 5)
        self.assertEqual(self.ev("updated_days()")[0], 2)
        self.assertEqual(self.ev("days_since('2026-11-01')")[0], 9)
        self.assertIsNone(self.ev("days_since('garbage')")[0])
        self.assertEqual(self.ev("age_days() > 3")[0], True)

    def test_validate_flags_typos_with_the_type_fields(self):
        self.assertIsNone(formula.validate("size + 1", ["size"]))
        self.assertIsNone(formula.validate("count(x)", ["x"]))
        self.assertIn("unknown name 'sizee'", formula.validate("sizee + 1", ["size"]))
        self.assertIsNone(formula.validate("id + name + owner", []))                                # built-in names are always fine
        self.assertIsNone(formula.validate("size + 1"))                                              # without keys: syntax only
        self.assertIn("unknown function", formula.validate("sizeof(1)"))
        self.assertIn("takes 2", formula.validate("join(',')"))

    def test_formatting_and_sorting_helpers(self):
        self.assertEqual([formula.format_value(v) for v in (None, True, False, 3, 2.5, 2.0, 1 / 3, ["a", 2], "x")], ["", "yes", "no", "3", "2.5", "2", "0.33", "a, 2", "x"])
        keys = [formula.sort_key(v) for v in (None, 10, 2, "b", "A", [], "")]
        self.assertEqual(keys[0], "")
        self.assertEqual(sorted([k for k in keys if k != ""]), [(0, 2.0), (0, 10.0), (1, "a"), (1, "b")])   # numbers before text, numerically


class TestWithRecords(Base):
    def setUp(self):
        super().setUp()
        self.p1 = self.store.create("plasmid", ALICE, "pUA66", {"resistance": ["Kan"], "size": "4000"})
        self.p2 = self.store.create("plasmid", ALICE, "pZA21", {"resistance": ["Amp", "Kan"]})
        self.p3 = self.store.create("plasmid", ALICE, "pLone", {})
        self.s1 = self.store.create("strain", ALICE, "s1", {"plasmid": [self.p1, self.p2]})
        self.s2 = self.store.create("strain", ALICE, "s2", {"plasmid": [self.p1]})
        self.s3 = self.store.create("strain", ALICE, "s3", {})

    def field(self, rid, key):
        rec = self.store.records[rid]
        fdef = next(f for f in self.store.schemas[rec["type"]]["fields"] if f["key"] == key)
        return self.store.compute(rec, fdef)

    def test_example_number_of_strains_carrying_a_plasmid(self):
        self.assertEqual(self.field(self.p1, "n_strains"), (2, None))
        self.assertEqual(self.field(self.p2, "n_strains"), (1, None))
        self.assertEqual(self.field(self.p3, "n_strains"), (0, None))

    def test_example_resistance_pulled_from_linked_plasmids(self):
        self.assertEqual(self.field(self.s1, "resist"), ("Kan, Amp", None))
        self.assertEqual(self.field(self.s2, "resist"), ("Kan", None))
        self.assertEqual(self.field(self.s3, "resist"), ("", None))

    def test_example_age_in_days_uses_the_clock_of_the_store(self):
        self.store.records[self.s1]["created_at"] = "2026-11-01T08:00:00"
        self.assertEqual(self.field(self.s1, "age"), (9, None))

    def test_list_results_become_ids_and_helpers_work(self):
        ev = lambda expr, rid: self.store.compute(self.store.records[rid], {"expr": expr})
        self.assertEqual(ev("referenced_by('strain')", self.p1)[0], [self.s1, self.s2])
        self.assertEqual(ev("ids(refs('plasmid'))", self.s1)[0], [self.p1, self.p2])
        self.assertEqual(ev("names(refs('plasmid'))", self.s1)[0], ["pUA66", "pZA21"])
        self.assertEqual(ev("refs('plasmid')", self.s1)[0], [self.p1, self.p2])
        self.assertEqual(ev("referenced_by('plasmid')", self.p1)[0], [])                       # nothing of that type points here
        self.assertEqual(ev("referenced_by('strain', 'plasmid')", self.p1)[0], [self.s1, self.s2])
        self.assertEqual(ev("referenced_by('strain', 'nofield')", self.p1)[0], [])
        self.assertEqual(ev("sum(values(refs('plasmid'), 'size'))", self.s1)[0], 4000)
        self.assertEqual(ev("count(refs('plasmid')) > 1", self.s1)[0], True)

    def test_reverse_index_follows_changes(self):
        self.assertEqual(self.field(self.p3, "n_strains")[0], 0)
        self.store.update(self.s3, ALICE, "s3", {"plasmid": self.p3}, "", self.store.records[self.s3]["_version"])
        self.assertEqual(self.field(self.p3, "n_strains")[0], 1)
        purge(self.store, self.s3, ALICE)
        self.assertEqual(self.field(self.p3, "n_strains")[0], 0)

    def test_computed_fields_are_not_stored_or_editable(self):
        with open(self.store.path_of(self.s1, "strain")) as fh:
            text = fh.read()
        self.assertNotIn("resist", text.replace("resistance", ""))
        self.assertNotIn("age", [k for k in self.store.records[self.s1]["fields"]])
        self.store.update(self.s1, ALICE, "s1", {"resist": "hacked", "age": "999"}, "", self.store.records[self.s1]["_version"])
        self.assertNotIn("resist", self.store.records[self.s1]["fields"])
        self.assertEqual(self.field(self.s1, "resist"), ("Kan, Amp", None))

    def test_invalid_formulas_in_a_type_definition_warn_and_yield_nothing(self):
        bad = json.loads(json.dumps(STRAIN))
        bad["fields"].append({"key": "oops", "label": "Oops", "type": "computed", "expr": "__import__('os')"})
        bad["fields"].append({"key": "typo", "label": "Typo", "type": "computed", "expr": "genotypo + 1"})
        bad["fields"].append({"key": "none", "label": "None", "type": "computed"})
        with open(os.path.join(self.repo, "config", "types", "strain.yaml"), "w") as fh:
            json.dump(bad, fh)
        store = Store(self.repo)
        text = " | ".join(store.schema_warnings)
        self.assertIn("field 'oops'", text)
        self.assertIn("unknown name 'genotypo'", text)
        self.assertIn("field 'none'", text)
        self.assertEqual(len(store.schema_warnings), 3)
        fdef = next(f for f in bad["fields"] if f["key"] == "oops")
        self.assertIsNone(store.compute(store.records[self.s1], fdef)[0])
        self.assertEqual(Store(self.repo).schema_warnings, store.schema_warnings)
        for ok in (PLASMID, STRAIN):
            with open(os.path.join(self.repo, "config", "types", ok["name"].lower() + ".yaml"), "w") as fh:
                json.dump(ok, fh)
        self.assertEqual(Store(self.repo).schema_warnings, [])

    def test_computing_a_whole_list_is_fast(self):
        base_p, base_s = self.store.records[self.p1], self.store.records[self.s1]
        for i in range(400):                                   # many records, injected directly (no git commits needed for a speed check)
            self.store.records[f"PL-{100 + i}"] = dict(base_p, id=f"PL-{100 + i}", legacy_id=100 + i, fields={"resistance": ["Kan"]})
        for i in range(600):
            self.store.records[f"ST-{100 + i}"] = dict(base_s, id=f"ST-{100 + i}", legacy_id=100 + i, fields={"plasmid": [f"PL-{100 + (i % 400)}"]})
        self.store._notify(None)
        fdef = next(f for f in PLASMID["fields"] if f["key"] == "n_strains")
        start = time.time()
        counts = [self.store.compute(r, fdef)[0] for r in self.store.records.values() if r["type"] == "plasmid" and r["legacy_id"] >= 100]
        self.assertEqual(sum(counts), 600)
        self.assertLess(time.time() - start, 3.0)


if __name__ == "__main__":
    unittest.main()

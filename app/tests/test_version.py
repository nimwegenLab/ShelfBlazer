"""The version is declared in two pyproject files and the changelog; they must agree, so a release tag can be checked against them."""
import pathlib, re, unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def declared(path):
    m = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / path).read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


class VersionTests(unittest.TestCase):
    def test_both_packages_carry_the_same_semantic_version(self):
        app, core = declared("app/pyproject.toml"), declared("packages/pyproject.toml")
        self.assertRegex(app, SEMVER)
        self.assertEqual(app, core, "app/pyproject.toml and packages/pyproject.toml must carry the same version")

    def test_changelog_has_a_section_for_the_current_version(self):
        v = declared("app/pyproject.toml")
        log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertRegex(log, rf"(?m)^## \[{re.escape(v)}\]", f"CHANGELOG.md needs a '## [{v}]' section")

    def test_the_package_exposes_a_version(self):
        import shelfblazer_app
        self.assertRegex(shelfblazer_app.__version__, r"^\d+\.\d+\.\d+|^0\+unknown$")


if __name__ == "__main__":
    unittest.main()

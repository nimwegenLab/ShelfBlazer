#!/usr/bin/env python3
"""Run both test suites with one process per test file, several at a time. Same tests, same flags as the two
`unittest discover` commands in the README, only faster on a machine with several cores.

    .venv/bin/python run_tests.py              # everything, one process per core
    .venv/bin/python run_tests.py -j 4         # at most 4 at a time
    .venv/bin/python run_tests.py import look  # only test files whose name contains "import" or "look"

Every test builds its own temporary folder and git repository, so files do not share state and can run side by side.
Exit status 0 only when every file passed. The output of a failing file is printed in full at the end."""
import argparse, os, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.abspath(__file__))
SUITES = ("packages/tests", "app/tests")


def test_files(words):
    out = []
    for suite in SUITES:
        for name in sorted(os.listdir(os.path.join(ROOT, suite))):
            if name.startswith("test_") and name.endswith(".py") and (not words or any(w in name for w in words)):
                out.append((suite, name))
    # the biggest files first, so a long one does not start last and hold everything up
    return sorted(out, key=lambda sf: -os.path.getsize(os.path.join(ROOT, *sf)))


def run_one(suite, name):
    start = time.monotonic()
    p = subprocess.run([sys.executable, "-W", "error::ResourceWarning", "-m", "unittest", "discover", "-s", suite, "-p", name],
                       cwd=ROOT, capture_output=True, text=True)
    text = p.stdout + p.stderr
    m = re.search(r"^Ran (\d+) tests?", text, re.M)
    return dict(path=f"{suite}/{name}", ok=p.returncode == 0, n=int(m.group(1)) if m else 0, seconds=time.monotonic() - start, output=text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 2, help="test files run at the same time (default: number of cores)")
    ap.add_argument("words", nargs="*", help="only test files whose name contains one of these words")
    a = ap.parse_args(argv)
    files = test_files(a.words)
    if not files:
        print("no test file matches", file=sys.stderr)
        return 2
    start, results = time.monotonic(), []
    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
        for fut in as_completed([pool.submit(run_one, s, n) for s, n in files]):
            r = fut.result()
            results.append(r)
            print(f"{'ok  ' if r['ok'] else 'FAIL'}  {r['path']:<45} {r['n']:>4} tests  {r['seconds']:6.1f} s", flush=True)
    failed = [r for r in results if not r["ok"]]
    for r in failed:
        print(f"\n{'=' * 70}\n{r['path']}\n{'=' * 70}\n{r['output'].rstrip()}")
    total = sum(r["n"] for r in results)
    print(f"\n{total} tests in {len(results)} files, {time.monotonic() - start:.1f} s with {a.jobs} at a time: "
          + ("all passed" if not failed else f"{len(failed)} file(s) FAILED: " + ", ".join(r["path"] for r in failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Run the image-sync-operator test suite against the KMock fake Kubernetes API.

Examples:
    ./run_tests.py                          # everything except slow tests
    ./run_tests.py --slow                   # everything, including slow timer tests
    ./run_tests.py -c job -c retry          # whole categories
    ./run_tests.py test_job.py::test_job_object_is_rendered_from_spec
    ./run_tests.py -k "secret and not cron" # pytest keyword expression
    ./run_tests.py --list                   # show categories and tests
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

from categories import CATEGORIES

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tests", nargs="*", help="test files, node ids or test-name substrings")
    parser.add_argument(
        "-c", "--category", action="append", default=[], choices=sorted(CATEGORIES), help="run a category (repeatable)"
    )
    parser.add_argument("-k", dest="keyword", help="pytest -k expression")
    parser.add_argument("--slow", action="store_true", help="include tests that wait on the operator's long timers")
    parser.add_argument("--only-slow", action="store_true", help="run only the slow tests")
    parser.add_argument("-j", "--jobs", default="auto", help="parallel workers (default: auto, 1 disables)")
    parser.add_argument("--no-build", action="store_true", help="do not run cargo build first")
    parser.add_argument("--release", action="store_true", help="use the release binary")
    parser.add_argument("--list", action="store_true", help="list categories and tests, then exit")
    parser.add_argument("-x", "--exitfirst", action="store_true", help="stop at first failure")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def build(release: bool) -> pathlib.Path:
    cmd = ["cargo", "build"] + (["--release"] if release else [])
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=REPO, check=True)
    return REPO / "target" / ("release" if release else "debug") / "image-sync-operator"


def main() -> int:
    args = parse_args()
    env = dict(os.environ)
    if not args.list and "IMAGE_SYNC_OPERATOR_BIN" not in env:
        profile = "release" if args.release else "debug"
        binary = REPO / "target" / profile / "image-sync-operator"
        if not args.no_build:
            binary = build(args.release)
        env["IMAGE_SYNC_OPERATOR_BIN"] = str(binary)
    pytest = ["uv", "run", "pytest"]
    if args.list:
        print("Categories (test_<category>.py):")
        for name, text in CATEGORIES.items():
            print(f"  {name:<11} {text}")
        print()
        return subprocess.call([*pytest, "--collect-only", "-q", "-p", "no:xdist"], cwd=HERE)

    cmd = list(pytest)
    if args.jobs != "1":
        cmd += ["-n", args.jobs]
    if args.verbose:
        cmd.append("-v")
    if args.exitfirst:
        cmd.append("-x")
    marks = []
    if args.category:
        marks.append("(" + " or ".join(args.category) + ")")
    if args.only_slow:
        marks.append("slow")
    elif not args.slow:
        marks.append("not slow")
    cmd += ["-m", " and ".join(marks)]
    keywords: list[str] = []
    for item in args.tests:
        if "::" in item or item.endswith(".py") or (HERE / item).exists():
            cmd.append(item)
        else:
            keywords.append(item)
    expressions = [f"({' or '.join(keywords)})"] if keywords else []
    if args.keyword:
        expressions.append(f"({args.keyword})")
    if expressions:
        cmd += ["-k", " and ".join(expressions)]
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=HERE, env=env)


if __name__ == "__main__":
    sys.exit(main())

"""Selective-smoke helper: compute which PUBLIC VIEWS the current diff can affect.

Usage:
    python tests/affected_views.py <base-ref>     # e.g. origin/main

Prints to stdout one of:
    ALL            -> run the full smoke tier (global file touched, or the diff is
                      empty/unavailable — fail-open)
    NONE           -> no model file changed (docs-only diff): every smoke may be skipped
    v1,v2,v3,...   -> comma-separated view names whose smoke tests must run

Selection rules (regex-based on purpose — several model files embed Jinja, so
yaml.safe_load can't parse them):
  - a changed file under model/views/  -> that view is affected;
  - a changed file under model/cubes/  -> every view whose `join_path` references that
    cube, OR references a cube that JOINS it (filtered measures may cross the join,
    e.g. base_sales filters on base_reps.rep_status), is affected;
  - model/globals.py, model/macros.jinja, cube.py, Dockerfile, entrypoint, compose or
    shared test-harness changes -> ALL (they can change any generated SQL or the harness);
  - rbac.py alone does NOT force ALL: it only affects access control, which the
    contract tier (always full) asserts; smoke runs as the admin fallback.

The CONTRACT and FANOUT tiers are never filtered — only `-m smoke` tests read this
(via the SMOKE_ONLY_VIEWS env var; see tests/conftest.py).
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

# Any change here can alter arbitrary generated SQL or the test harness: run everything.
# NOTE: individual test files are NOT global — a new area smoke file runs anyway because
# its views' model files are in the same diff; only the shared harness forces ALL.
GLOBAL_PREFIXES = (
    "model/globals.py",
    "model/macros.jinja",
    "cube.py",
    "Dockerfile",
    "docker-entrypoint",
    "docker-compose",
    "tests/cube_client.py",
    "tests/conftest.py",
    "tests/affected_views.py",
    "tests/requirements.txt",
)


def _changed_files(base_ref: str) -> list[str] | None:
    """Return the repo-relative changed paths versus a base ref.

    Args:
        base_ref (str): The git ref to diff against (e.g. "origin/main").

    Returns:
        list[str] | None: The changed paths, or None when git is unavailable or the diff fails.
    """
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
            cwd=REPO, capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return [line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()]


def _first_member_name(text: str) -> str | None:
    """Return the first `- name: x` in a model file — the cube/view name, which always comes first.

    Args:
        text (str): The YAML file contents.

    Returns:
        str | None: The declared name, or None when the file declares none.
    """
    m = re.search(r"^\s*-\s+name:\s*([A-Za-z0-9_]+)", text, re.M)
    return m.group(1) if m else None


def _join_path_cubes(text: str) -> set[str]:
    """Return every cube a view reaches through its `join_path` entries.

    Args:
        text (str): The view YAML contents.

    Returns:
        set[str]: Each cube named in a dot-separated join path.
    """
    cubes: set[str] = set()
    for path in re.findall(r"join_path:\s*([A-Za-z0-9_.]+)", text):
        cubes.update(path.split("."))
    return cubes


def _cube_joins(text: str) -> set[str]:
    """Return the cubes a cube declares under its `joins:` block.

    Args:
        text (str): The cube YAML contents.

    Returns:
        set[str]: The names of the joined cubes.
    """
    block = re.search(r"^\s{4}joins:\n((?:\s{6,}.*\n)+)", text, re.M)
    if not block:
        return set()
    return set(re.findall(r"-\s+name:\s*([A-Za-z0-9_]+)", block.group(1)))


def affected_views(base_ref: str) -> str:
    """Resolve which views a diff touches, for the selective smoke tier.

    Fails OPEN: when no diff is available (a run on main, or git missing) it returns "ALL" rather
    than silently narrowing the suite: a tier that quietly stops running is worse than a slow one.

    Args:
        base_ref (str): The git ref to diff against.

    Returns:
        str: "ALL", "NONE", or a comma-separated list of view names.
    """
    changed = _changed_files(base_ref)
    if changed is None or not changed:
        return "ALL"  # fail-open: no diff available (e.g. run on main) -> full suite

    if any(f.startswith(GLOBAL_PREFIXES) for f in changed):
        return "ALL"

    changed_views: set[str] = set()
    changed_cubes: set[str] = set()
    for f in changed:
        p = REPO / f
        if not p.exists() or not f.endswith(".yml"):
            continue
        text = p.read_text(encoding="utf-8")
        name = _first_member_name(text)
        if not name:
            continue
        if f.startswith("model/views/"):
            changed_views.add(name)
        elif f.startswith("model/cubes/"):
            changed_cubes.add(name)

    if not changed_views and not changed_cubes:
        return "NONE"  # docs/config-only diff: contract still runs in full

    # A view is affected when its join_path cubes — or their direct joins — changed.
    for view_file in (REPO / "model" / "views").rglob("*.yml"):
        text = view_file.read_text(encoding="utf-8")
        view = _first_member_name(text)
        if not view or view in changed_views:
            continue
        reach = set(_join_path_cubes(text))
        for cube_file in (REPO / "model" / "cubes").rglob("*.yml"):
            cube_text = cube_file.read_text(encoding="utf-8")
            cube = _first_member_name(cube_text)
            if cube in reach:
                reach.update(_cube_joins(cube_text))
        if reach & changed_cubes:
            changed_views.add(view)

    return ",".join(sorted(changed_views))


if __name__ == "__main__":
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    print(affected_views(base))

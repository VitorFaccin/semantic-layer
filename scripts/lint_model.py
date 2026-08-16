"""Lint the Cube model the way Cube itself would read it.

Generic YAML linters are useless here: every model file is a Jinja TEMPLATE, so `yamllint` dies on
`{% import %}` before it can check anything. Worse, the failure mode that actually bites this repo
is a DUPLICATED KEY — which PyYAML silently accepts (last one wins) and Cube rejects at boot with
`duplicated mapping key`, taking the whole model down with an HTTP 500 on /meta.

So this renders each file through Jinja exactly as Cube does, then parses it with a loader that
REFUSES duplicate keys. It needs no running container and no warehouse, which is what makes it the
first thing CI runs.

    python scripts/lint_model.py
"""
from __future__ import annotations

import json
import pathlib
import sys

import yaml
from jinja2 import Environment, FileSystemLoader, TemplateError

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODEL = ROOT / "model"


class StrictLoader(yaml.SafeLoader):
    """A SafeLoader that treats a duplicated mapping key as an error rather than an overwrite."""


def _no_duplicate_keys(loader: StrictLoader, node: yaml.MappingNode) -> dict:
    """Construct a mapping, raising when a key repeats.

    Args:
        loader (StrictLoader): The active loader.
        node (yaml.MappingNode): The mapping being constructed.

    Returns:
        dict: The constructed mapping.

    Raises:
        yaml.constructor.ConstructorError: If any key appears twice.
    """
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"found duplicated key {key!r}", key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicate_keys)


def _jinja_env() -> Environment:
    """Build a Jinja environment that renders like Cube's.

    Cube JSON-quotes every interpolated SCALAR, which is why a macro emitting
    `sql: {CUBE}.status = 'won'` parses there and would not here. Multi-line values are
    macro-expanded YAML blocks and must pass through untouched.

    Kept self-contained (no import from warehouse/) so the lint job needs nothing but PyYAML and
    Jinja2 — it has to stay the cheapest step in CI.

    Returns:
        Environment: The configured environment.
    """
    def quote_like_cube(value: object) -> object:
        """JSON-quote an interpolated scalar, mirroring Cube's own renderer."""
        if isinstance(value, str) and "\n" not in value:
            return json.dumps(value)
        return value

    env = Environment(loader=FileSystemLoader(str(MODEL)), keep_trailing_newline=True,
                      finalize=quote_like_cube)
    env.globals["table"] = lambda dataset, name: f"{dataset}.{name}"
    env.globals["valid_rows"] = lambda t, date_col="created_at": f"{t}.{date_col} IS NOT NULL"
    env.globals["canonical_order_filter"] = (
        lambda t: f"{t}.parent_order_id IS NULL AND {t}.customer_id != {t}.rep_id"
    )
    return env


def lint() -> list[str]:
    """Render and strict-parse every model file.

    Returns:
        list[str]: One message per problem found (empty when the model is clean).
    """
    env = _jinja_env()
    problems: list[str] = []
    files = sorted(MODEL.rglob("*.yml"))
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        try:
            rendered = env.get_template(path.relative_to(MODEL).as_posix()).render()
        except TemplateError as exc:
            problems.append(f"{rel}: erro de Jinja — {exc}")
            continue
        try:
            yaml.load(rendered, Loader=StrictLoader)
        except yaml.YAMLError as exc:
            detail = str(exc).replace("\n", " ")
            problems.append(f"{rel}: YAML inválido — {detail}")
    print(f"{len(files)} arquivos do modelo verificados")
    return problems


if __name__ == "__main__":
    found = lint()
    for problem in found:
        print(f"  {problem}")
    print("modelo OK" if not found else f"{len(found)} problema(s)")
    sys.exit(1 if found else 0)

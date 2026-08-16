"""Contract tier — description/metadata quality, the thing the agent actually reads.

A measure with no description is a measure the agent will misuse. These checks are the automated
half of docs/description-style-guide.md: the style guide governs the prose, this file governs the
presence and the structure.
"""
from __future__ import annotations

import cube_client
import pytest
from test_contract_core import EXPECTED_VIEWS

pytestmark = pytest.mark.contract

# PII lives on exactly one view per subject, behind the level-40 floor. Fact views must never
# carry it — that is what lets the gate be applied once, where the data lives.
PII_MEMBERS = {"email", "phone", "tax_id", "payer_name", "payer_tax_id"}
VIEWS_ALLOWED_TO_CARRY_PII = {"reps", "subscription"}


@pytest.mark.parametrize("view", sorted(EXPECTED_VIEWS))
def test_view_members_are_documented(meta: dict, view: str) -> None:
    """Every public member carries a description, and the view itself does too."""
    issues = cube_client.view_quality_issues(meta, view, require_descriptions=True)
    assert not issues, f"{view}: {issues}"


@pytest.mark.parametrize("view", sorted(EXPECTED_VIEWS))
def test_pii_is_confined_to_its_owning_view(meta: dict, view: str) -> None:
    """PII must not spread across fact views through the shared rep attribute block."""
    if view in VIEWS_ALLOWED_TO_CARRY_PII:
        return
    names = set(cube_client.short_names(cube_client.dimensions(meta, view)))
    leaked = {n for n in names if any(n == p or n.endswith(f"_{p}") for p in PII_MEMBERS)}
    assert not leaked, f"{view} exposes PII outside its owning view: {sorted(leaked)}"


@pytest.mark.parametrize("view", sorted(EXPECTED_VIEWS))
def test_non_additive_measures_are_flagged(meta: dict, view: str) -> None:
    """Ratios and averages must declare `meta.additive: false`.

    An unflagged ratio is the classic agent error: it sums percentages across a breakdown and
    reports a number that cannot exist.
    """
    unflagged = []
    for m in cube_client.measures(meta, view):
        agg = (m.get("aggType") or m.get("type") or "").lower()
        if agg not in {"number", "avg", "runningtotal"}:
            continue
        if (m.get("meta") or {}).get("additive") is not False:
            unflagged.append(m["name"].split(".", 1)[-1])
    assert not unflagged, f"{view}: non-additive measures without meta.additive=false: {unflagged}"


@pytest.mark.parametrize("view", sorted(EXPECTED_VIEWS))
def test_view_is_organised_into_folders(meta: dict, view: str) -> None:
    """Folders are the agent's map of a view. A flat 30-member view is a discovery problem."""
    v = cube_client.get(meta, view)
    assert v.get("folders"), f"{view}: no folders declared"

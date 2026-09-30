"""Running one published conformance case against this reader.

Kept separate from the command-line tool so that another harness — the SREF
implementation conformance runner, for one — can drive the same logic.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from . import bundle, jsonio, package, recipe, registry, snapshot
from .errors import Report

if TYPE_CHECKING:
    import pathlib

#: How a case's declared capability selects what this reader is asked to do.
_UNSUPPORTED = "unsupported"


def run_case(root: pathlib.Path, case: dict[str, Any]) -> tuple[str, str]:
    """Return an outcome and a human-readable detail for one manifest case.

    Outcomes are `pass`, `wrong-verdict` (accepted what must be rejected, or
    the reverse), `wrong-category`, `wrong-requirement`, and `unsupported` (a
    capability this reader does not claim).
    """
    fixture = root / case["fixture"]
    if not fixture.exists():
        return "missing-fixture", str(fixture)

    capabilities = set(case["capabilities"])
    if "registry" in capabilities:
        report = _validate_registry(fixture)
    elif capabilities <= {"json-reader", "json-writer", "round-trip"}:
        _, report = recipe.validate(fixture.read_bytes())
    elif "unit-conversion" in capabilities:
        report = _run_conversion_corpus(fixture)
    elif "bundle-reader" in capabilities:
        _, report = bundle.validate(fixture.read_bytes())
    elif "package-reader" in capabilities:
        _, report = package.validate(fixture.read_bytes())
    else:
        return _UNSUPPORTED, ", ".join(sorted(capabilities))

    expected_valid = case["expected"] == "valid"
    if expected_valid and report.valid:
        return "pass", ""
    if expected_valid:
        return "wrong-verdict", f"rejected a valid fixture: {report.codes}"
    if report.valid:
        return "wrong-verdict", "accepted an invalid fixture"

    # Judge the reported category, as the published runner does, not whether
    # the right one appears anywhere in the list.
    wanted_category = case.get("error_category")
    if wanted_category and report.category != wanted_category:
        return (
            "wrong-category",
            f"want {wanted_category}, reported {report.category} (all: {report.categories})",
        )
    wanted_requirement = case.get("requirement_id")
    if wanted_requirement and report.requirement_id != wanted_requirement:
        return (
            "wrong-requirement",
            f"want {wanted_requirement}, reported {report.requirement_id} "
            f"(all: {report.requirement_ids})",
        )
    return "pass", ""


def _validate_registry(fixture: pathlib.Path) -> Report:
    report = Report()
    try:
        document = jsonio.loads(fixture.read_bytes())
    except jsonio.JSONRejectedError as exc:
        report.add(exc.code, str(exc))
        return report
    registry.load(document, report)
    return report


def _run_conversion_corpus(fixture: pathlib.Path) -> Report:
    """Run every case in the published unit-conversion corpus.

    The corpus is one conformance case carrying many conversions, so a single
    wrong answer fails it. Values are compared as canonical strings rather than
    as floats: the whole point of exact rationals is that `473176473/2000000`
    is not "about 236.6".
    """
    report = Report()
    corpus = json.loads(fixture.read_text(encoding="utf-8"))
    registry_report = Report()
    catalog = registry.load(snapshot.units(), registry_report)
    if catalog is None:
        report.add("malformed-registry", "the pinned registry did not load")
        return report

    for case in corpus["cases"]:
        expected = case["expected"]
        try:
            actual = catalog.convert_amount(case["input"], case["source_unit"], case["target_unit"])
        except registry.UnknownUnitError:
            _compare_error(case, expected, "unknown-unit", report)
            continue
        except registry.IncompatibleUnitsError as exc:
            _compare_error(case, expected, exc.code, report)
            continue
        if "error" in expected:
            report.add(
                "conversion-mismatch",
                f"{case['id']}: expected {expected['error']}, converted anyway",
            )
            continue
        if actual != _normalize(expected["amount"], approximate=expected.get("approximate", False)):
            report.add(
                "conversion-mismatch",
                f"{case['id']}: expected {expected['amount']}, got {actual}",
            )
    return report


def _normalize(amount: dict[str, Any], *, approximate: bool) -> dict[str, Any]:
    wanted = dict(amount)
    if approximate:
        wanted["approximate"] = True
    else:
        wanted.pop("approximate", None)
    return wanted


def _compare_error(
    case: dict[str, Any],
    expected: dict[str, Any],
    code: str,
    report: Report,
) -> None:
    if expected.get("error") != code:
        report.add("conversion-mismatch", f"{case['id']}: expected {expected}, refused as {code}")

#!/usr/bin/env python3
"""Conformance adapter for sref-reader.

Implements the command-line contract in the SREF specification's
`docs/conformance-runner.md`, so the published runner can test this library
without knowing anything about it.

    tools/adapter.py capabilities
    tools/adapter.py check json-reader path/to/fixture.recipe.json

The adapter only translates this library's report into the runner's
vocabulary; it does no validating of its own.
"""

from __future__ import annotations

import json
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import sref_reader
from sref_reader import bundle, jsonio, package, recipe, registry
from sref_reader.errors import Report

#: How a runner capability maps onto this library's entry points. Capabilities
#: absent from this table are ones the library does not claim.
_CHECKS = {
    "json-reader": lambda data: recipe.validate(data)[1],
    "package-reader": lambda data: package.validate(data)[1],
    "bundle-reader": lambda data: bundle.validate(data)[1],
}

LOGGER = logging.getLogger(__name__)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not argv:
        return _fail("no command")
    command, arguments = argv[0], argv[1:]

    if command == "capabilities":
        return _emit(
            {
                "implementation": "sref-reader",
                "version": sref_reader.__version__,
                "sref_version": sref_reader.SUPPORTED_FORMAT,
                "unit_registry_version": sref_reader.SUPPORTED_REGISTRY,
                "capabilities": list(sref_reader.CAPABILITIES),
            },
        )

    if command == "check" and len(arguments) == 2:
        return _check(arguments[0], pathlib.Path(arguments[1]))

    if command == "rewrite":
        # This library does not write, so it cannot round-trip alone.
        return _emit({"outcome": "unsupported", "detail": "sref-reader does not write SREF"})

    return _fail(f"unknown command {command!r}")


def _check(capability: str, fixture: pathlib.Path) -> int:
    if capability == "registry":
        report = _registry(fixture)
    elif capability == "unit-conversion":
        report = _conversions(fixture)
    elif capability in _CHECKS:
        report = _CHECKS[capability](fixture.read_bytes())
    else:
        return _emit({"outcome": "unsupported", "detail": f"{capability} is not claimed"})

    if report.valid:
        return _emit({"outcome": "accepted"})
    # `error_category` is required and portable; `requirement_id` lets the
    # runner confirm which rule was enforced.
    return _emit(
        {
            "outcome": "rejected",
            "error_category": report.category,
            "requirement_id": report.primary,
            "detail": "; ".join(str(violation) for violation in report.violations[:5]),
        },
    )


def _registry(fixture: pathlib.Path) -> Report:
    report = Report()
    try:
        document = jsonio.loads(fixture.read_bytes())
    except jsonio.JSONRejectedError as exc:
        report.add(exc.code, str(exc))
        return report
    registry.load(document, report)
    return report


def _conversions(fixture: pathlib.Path) -> Report:
    """Run a whole conversion corpus, which is one case carrying many sums."""
    report = Report()
    corpus = json.loads(fixture.read_text(encoding="utf-8"))
    catalog = sref_reader.unit_registry()
    for case in corpus["cases"]:
        expected = case["expected"]
        try:
            actual = catalog.convert_amount(case["input"], case["source_unit"], case["target_unit"])
        except registry.UnknownUnitError:
            _expect_error(case, expected, "unknown-unit", report)
            continue
        except registry.IncompatibleUnitsError as exc:
            _expect_error(case, expected, exc.code, report)
            continue
        if "error" in expected:
            report.add(
                "conversion-mismatch",
                f"{case['id']}: expected {expected['error']}, converted anyway",
            )
            continue
        wanted = dict(expected["amount"])
        if expected.get("approximate"):
            wanted["approximate"] = True
        else:
            wanted.pop("approximate", None)
        if actual != wanted:
            report.add("conversion-mismatch", f"{case['id']}: expected {wanted}, got {actual}")
    return report


def _expect_error(case: dict, expected: dict, code: str, report: Report) -> None:
    if expected.get("error") != code:
        report.add("conversion-mismatch", f"{case['id']}: expected {expected}, refused as {code}")


def _emit(answer: dict) -> int:
    json.dump(answer, sys.stdout)
    sys.stdout.write("\n")
    return 0


def _fail(message: str) -> int:
    LOGGER.error("%s", message)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

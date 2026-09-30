#!/usr/bin/env python3
"""Run the published SREF conformance corpus against this reader.

The corpus lives in the SREF repository; point this at a checkout.

    python3 tools/conformance.py --sref ../sref [--capability json-reader]

A case declares the capability it exercises, whether its fixture is valid, and
for an invalid fixture both its normative category and precise requirement. An
unrelated failure does not satisfy a case, so category and requirement failures
are reported separately from a wrong verdict.
"""

from __future__ import annotations

import argparse
import collections
import json
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sref_reader import conformance

CLAIMED = {"registry", "json-reader", "package-reader", "bundle-reader", "unit-conversion"}

LOGGER = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sref", default="../sref", help="path to a savorum/sref checkout")
    parser.add_argument("--capability", action="append", help="limit to one or more capabilities")
    parser.add_argument("--verbose", action="store_true", help="list every failing case")
    arguments = parser.parse_args()

    root = pathlib.Path(arguments.sref).resolve()
    if not (root / "conformance" / "manifest.json").is_file():
        parser.error(f"no conformance manifest under {root}; pass --sref")
    stale = stale_snapshot(root)
    if stale:
        # An out-of-date vendored snapshot still verifies against its own
        # manifest, so compare it with the checkout.
        LOGGER.error("the vendored snapshot is not the one in this checkout:")
        for name in stale:
            LOGGER.error("  %s", name)
        LOGGER.error("re-vendor from snapshots/ before trusting these results")
        return 2
    manifest = json.loads((root / "conformance" / "manifest.json").read_text())
    wanted = set(arguments.capability) if arguments.capability else CLAIMED

    outcomes: collections.Counter[str] = collections.Counter()
    failures: list[str] = []
    for case in manifest["cases"]:
        capabilities = set(case["capabilities"])
        if not capabilities & wanted:
            continue
        outcome, detail = conformance.run_case(root, case)
        outcomes[outcome] += 1
        if outcome != "pass":
            failures.append(f"  {case['id']:44s} {outcome:14s} {detail}")

    total = sum(outcomes.values())
    LOGGER.info(
        "SREF %s / registry %s",
        manifest["sref_version"],
        manifest["unit_registry_version"],
    )
    LOGGER.info("capabilities: %s", ", ".join(sorted(wanted)))
    LOGGER.info("%s/%s cases pass", outcomes["pass"], total)
    for outcome, count in sorted(outcomes.items()):
        if outcome != "pass":
            LOGGER.info("  %s: %s", outcome, count)
    if failures and (arguments.verbose or len(failures) <= 40):
        LOGGER.info("")
        LOGGER.info("\n".join(failures))
    return 0 if outcomes["pass"] == total else 1


def stale_snapshot(root: pathlib.Path) -> list[str]:
    """Names where the vendored release snapshot differs from the checkout."""
    import hashlib

    from sref_reader import snapshot

    published = root / "snapshots" / snapshot.manifest()["sref_version"]
    if not published.is_dir():
        return []
    differing = []
    for name in snapshot.manifest()["files"]:
        source = published / name
        if not source.is_file():
            differing.append(f"{name} is not in the checkout")
            continue
        vendored = hashlib.sha256(snapshot.read(name)).hexdigest()
        if hashlib.sha256(source.read_bytes()).hexdigest() != vendored:
            differing.append(name)
    return differing


if __name__ == "__main__":
    raise SystemExit(main())

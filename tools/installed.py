#!/usr/bin/env python3
"""Exercise sref-reader as an installed distribution rather than a checkout.

A checkout resolves every path whether or not it was packaged, so run this
against an installed sref-reader, from a directory that is not the checkout:

    cd "$(mktemp -d)"
    python3 /path/to/sref-reader/tools/installed.py --sref /path/to/sref

It refuses to run if it imports the checkout instead of the installed
distribution.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib

#: One valid case per capability is enough here. This is a smoke test that the
#: installed artifact works at all; `tools/conformance.py` runs the corpus.
_SMOKE = ("registry", "json-reader", "package-reader", "bundle-reader", "unit-conversion")

LOGGER = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sref", default="../sref", help="path to a savorum/sref checkout")
    arguments = parser.parse_args()

    root = pathlib.Path(arguments.sref).resolve()
    if not (root / "conformance" / "manifest.json").is_file():
        parser.error(f"no conformance manifest under {root}; pass --sref")

    import sref_reader
    from sref_reader import conformance, snapshot

    # The source tree itself, not merely anything under the checkout: CI
    # builds its virtual environment inside the working copy, and a package
    # installed into it is a real installed distribution.
    source = pathlib.Path(__file__).resolve().parent.parent / "sref_reader"
    installed = pathlib.Path(sref_reader.__file__).resolve()
    if installed.parent == source:
        LOGGER.error("imported the checkout at %s, not an installed distribution", installed)
        return 2
    LOGGER.info("sref_reader %s from %s", sref_reader.__version__, installed.parent)

    # The snapshot is the part a wheel loses, so read all of it, not just the
    # first file. Each load verifies its own digest.
    for name in snapshot.manifest()["files"]:
        snapshot.load(name)
    version = snapshot.manifest()["sref_version"]
    LOGGER.info(
        "snapshot %s / registry %s: %s files",
        version,
        snapshot.manifest()["unit_registry_version"],
        len(snapshot.manifest()["files"]),
    )
    if version != sref_reader.SUPPORTED_FORMAT:
        LOGGER.error(
            "the shipped snapshot is %s, but this build implements %s",
            version,
            sref_reader.SUPPORTED_FORMAT,
        )
        return 1

    # A conversion is the shortest path that touches the registry end to end:
    # it loads the shipped units, validates them, and does exact arithmetic.
    converted = sref_reader.unit_registry().convert_amount(
        {"value": "1"},
        "mass.kilogram",
        "mass.gram",
    )
    LOGGER.info("1 kg converts to %s g", converted["value"])
    if converted["value"] != "1000":
        LOGGER.error("the shipped registry converted 1 kg to %s", converted)
        return 1

    manifest = json.loads((root / "conformance" / "manifest.json").read_text())
    failures = 0
    for capability in _SMOKE:
        case = _first_valid(manifest, capability)
        if case is None:
            LOGGER.error("  %-16s no valid case in the corpus", capability)
            failures += 1
            continue
        outcome, detail = conformance.run_case(root, case)
        LOGGER.info("  %-16s %-44s %s %s", capability, case["id"], outcome, detail.rstrip())
        failures += outcome != "pass"
    if failures:
        LOGGER.error("%s smoke case(s) did not pass from the installed artifact", failures)
        return 1
    LOGGER.info("the installed reader reads SREF")
    return 0


def _first_valid(manifest: dict, capability: str) -> dict | None:
    for case in manifest["cases"]:
        if case["expected"] == "valid" and capability in case["capabilities"]:
            return case
    return None


if __name__ == "__main__":
    raise SystemExit(main())

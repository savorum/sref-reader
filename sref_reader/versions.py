"""Version support and forward compatibility (specification section 5).

Two rules do the work here. A reader MUST NOT reinterpret a document from a
version line it does not implement, and a reader MUST open a newer version in
the same line, read the subset it understands, and preserve the rest without
guessing at it.

"Same line" means the same major component. Within a line, a newer minor or
patch version is compatible-newer: this build reads it, and everything it does
not recognize is unknown data to be carried, not noise to be dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

SUPPORTED_FORMAT = "0.4.0"
SUPPORTED_REGISTRY = "0.2.0"


@dataclass(frozen=True)
class Compatibility:
    supported: bool
    newer: bool


def parse(version: object) -> tuple[int, int, int] | None:
    """Parse a `major.minor.patch` string, rejecting non-canonical spellings."""
    if not isinstance(version, str):
        return None
    parts = version.split(".")
    if len(parts) != 3:
        return None
    numbers = []
    for part in parts:
        if not part.isdigit() or (len(part) > 1 and part.startswith("0")):
            return None
        numbers.append(int(part))
    return numbers[0], numbers[1], numbers[2]


def compare(declared: object, supported: str) -> Compatibility:
    """Report whether a declared version is readable, and whether it is newer.

    A version this build cannot parse is unsupported rather than assumed old:
    `0.1` and `0.1.0-rc1` are not versions SREF defines, and treating either as
    `0.1.0` would be exactly the silent reinterpretation section 5 forbids.
    """
    got = parse(declared)
    want = parse(supported)
    if got is None or want is None or got[0] != want[0]:
        return Compatibility(supported=False, newer=False)
    if got == want:
        return Compatibility(supported=True, newer=False)
    return Compatibility(supported=True, newer=got > want)

"""Coverage-guided fuzz target for the readers.

Every input is treated as a recipe document, a package, or a bundle, chosen by
its first byte. The reader promises to raise nothing but `SrefError`, so any
other exception is a finding.
"""

import contextlib
import sys

import atheris

with atheris.instrument_imports():
    import sref_reader

READERS = (
    sref_reader.read_recipe,
    sref_reader.read_package,
    sref_reader.read_bundle,
)


def test_one_input(data: bytes) -> None:
    if not data:
        return
    reader = READERS[data[0] % len(READERS)]
    with contextlib.suppress(sref_reader.SrefError):
        reader(data[1:])


def main() -> None:
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()

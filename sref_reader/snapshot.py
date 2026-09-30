"""The pinned SREF release this reader implements.

The pinned 0.4.0 released snapshot (schemas and unit registry) is vendored
under `_vendor/` inside the package and read through `importlib.resources`,
so an installed wheel can find it.

The snapshot is verified against its manifest's digests on load, so a
snapshot edited in place is a load error rather than a quiet change in what
this reader enforces.
"""

from __future__ import annotations

import functools
import hashlib
import json
from importlib import resources
from typing import Any

#: Where the snapshot sits inside this package. One release, named by its
#: version, so a later one arrives beside it rather than overwriting it.
VENDOR = "_vendor/sref-0.4.0"


class SnapshotCorruptError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class SnapshotMissingError(SnapshotCorruptError):
    """The snapshot is not there at all, which is a packaging fault."""

    def __init__(self) -> None:
        super().__init__(
            "the pinned release snapshot was not installed with this package; "
            "the build did not include sref_reader/_vendor",
        )


class SnapshotMissingFileError(SnapshotCorruptError):
    """A referenced snapshot file is not in the manifest."""

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} is not part of the pinned snapshot")


class SnapshotMismatchError(SnapshotCorruptError):
    """The snapshot file checksum does not match its manifest entry."""

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} does not match the digest the snapshot recorded")


def _resource(name: str):
    """Address one file in the snapshot.

    Joined a segment at a time because a resource is not a filesystem path,
    and the single-segment form is the one every loader a package can be
    installed under implements.
    """
    resource = resources.files(__package__)
    for segment in f"{VENDOR}/{name}".split("/"):
        resource = resource / segment
    return resource


@functools.lru_cache(maxsize=1)
def manifest() -> dict[str, Any]:
    try:
        text = _resource("manifest.json").read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        # A build that shipped the modules without the data.
        raise SnapshotMissingError from exc
    return json.loads(text)


@functools.cache
def read(name: str) -> bytes:
    """The bytes of one snapshot file, verified against the snapshot manifest."""
    declared = manifest()["files"].get(name)
    if declared is None:
        raise SnapshotMissingFileError(name)
    data = _resource(name).read_bytes()
    if hashlib.sha256(data).hexdigest() != declared:
        raise SnapshotMismatchError(name)
    return data


@functools.cache
def load(name: str) -> Any:
    """Load one snapshot file, verifying it against the snapshot manifest."""
    return json.loads(read(name).decode("utf-8"))


def recipe_schema() -> Any:
    return load("schema/recipe.schema.json")


def manifest_schema() -> Any:
    return load("schema/manifest.schema.json")


def bundle_manifest_schema() -> Any:
    return load("schema/bundle-manifest.schema.json")


@functools.lru_cache(maxsize=1)
def units() -> Any:
    return load("registry/units.json")

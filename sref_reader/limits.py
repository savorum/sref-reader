"""Resource policy for untrusted archives.

The specification requires finite limits and leaves the values to the reader
(sections 15 and 19). These are defaults, not definitions: a server importing
uploads and a desktop application opening a file the user chose have different
risk, and both are conforming.

Every limit here exists because a hostile archive can otherwise turn a small
download into unbounded work. The compression ratio is the classic one — a few
kilobytes that expand to gigabytes — but an archive with a million tiny entries
or a path a megabyte long costs a reader just as much.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    archive_bytes: int = 64 << 20
    expanded_bytes: int = 512 << 20
    bytes_per_entry: int = 128 << 20
    entries: int = 10_002
    path_length: int = 512
    compression_ratio: int = 200
    json_bytes: int = 16 << 20
    #: How deeply arrays and objects may nest. A few thousand levels fit in a
    #: small file and exhaust the interpreter's stack, so nesting is measured
    #: before anything recursive reads the document. No recipe needs more than a
    #: dozen levels; extensions get the rest.
    json_depth: int = 128
    #: Bundles are archives of archives, so they carry their own ceilings for
    #: what arrives. What comes out is bounded by the limits above, which a
    #: bundle and its members spend together rather than each in turn — see
    #: `budget`, which is what one operation has left of them.
    bundle_bytes: int = 512 << 20
    members: int = 10_000


DEFAULT = Limits()

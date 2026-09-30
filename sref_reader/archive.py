"""Safe ZIP handling shared by packages and bundles (specification section 15).

Everything here runs before a single byte is extracted. A package is data from
somewhere else, and the ZIP format offers several ways to make a reader write
outside where it meant to: absolute paths, `..` segments, backslashes that only
some platforms treat as separators, drive letters, symbolic links, and entries
whose declared size bears no relation to what they expand to.

The rule this module keeps is that an archive path is never joined to a
destination path until it has been proven safe, and that digests are computed
from the bytes actually read rather than trusted from archive metadata. A CRC
in a ZIP header is written by whoever built the archive.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import zipfile
import zlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .budget import Budget, ExhaustedError
from .limits import DEFAULT, Limits

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .errors import Report

_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")
#: The external-attributes bits that say an entry is not a regular file. ZIP
#: stores Unix mode in the high sixteen bits; anything but a regular file is a
#: symlink, device, FIFO or socket, and none of those belong in a package.
_UNIX_MODE_SHIFT = 16
_S_IFMT = 0o170000
_S_IFREG = 0o100000

#: The same unsafe path is reported under a different category depending on the
#: container, because a bundle's conformance cases name the member rather than
#: the archive. The rule being enforced is identical.
_UNSAFE_PATH = "unsafe-archive-path"
_UNSAFE_PATH_BUNDLE = "unsafe-bundle-member-path"


@dataclass
class Entry:
    name: str
    size: int
    compressed_size: int


def open_archive(
    data: bytes,
    report: Report,
    limits: Limits = DEFAULT,
    *,
    bundle: bool = False,
) -> zipfile.ZipFile | None:
    """Open an archive after checking everything that does not need its bytes."""
    ceiling = limits.bundle_bytes if bundle else limits.archive_bytes
    invalid = "invalid-zip-bundle" if bundle else "invalid-zip"
    if len(data) > ceiling:
        # A reader policy limit, so `resource-limit` rather than `invalid-zip`.
        report.add("archive-bytes-limit", "archive exceeds the reader's size limit")
        return None
    try:
        archive = zipfile.ZipFile(_BytesReader(data))
    except zipfile.BadZipFile as exc:
        report.add(invalid, f"not a readable ZIP archive: {exc}")
        return None
    return archive


def check_entries(
    archive: zipfile.ZipFile,
    report: Report,
    budget: Budget,
    *,
    bundle: bool = False,
) -> list[Entry] | None:
    """Validate the central directory. Returns the regular-file entries.

    Entry counts and expanded bytes are charged to the operation's budget
    rather than counted per archive, so a bundle's members draw down one
    allowance between them instead of receiving a fresh one each.
    """
    limits = budget.limits
    infos = archive.infolist()
    budget.spend_entries(len(infos))

    entries: list[Entry] = []
    seen: set[str] = set()
    for info in infos:
        name = info.filename
        if len(name) > limits.path_length:
            report.add(
                _UNSAFE_PATH_BUNDLE if bundle else _UNSAFE_PATH,
                f"archive path is longer than {limits.path_length} characters",
            )
            return None
        if info.flag_bits & 0x1:
            report.add("nonregular-archive-entry", f"{name!r} is encrypted")
            return None
        if not _check_path(name, report, bundle=bundle):
            return None
        if name.endswith("/"):
            # An explicit directory entry carries no content. Readers may
            # ignore them; writers must not emit them.
            continue
        if not _regular_file(info):
            report.add("nonregular-archive-entry", f"{name!r} is not a regular file")
            return None

        normalized = name.casefold()
        if normalized in seen:
            # Compared case-insensitively because two entries differing only in
            # case collide on the filesystems most readers extract onto.
            report.add("duplicate-archive-entry", f"{name!r} appears more than once")
            return None
        seen.add(normalized)

        if info.file_size > limits.bytes_per_entry:
            report.add("entry-bytes-limit", f"{name!r} expands beyond the per-entry limit")
            return None
        budget.spend_expanded(info.file_size, name)
        if (
            info.compress_size > 0
            and info.file_size / info.compress_size > limits.compression_ratio
        ):
            report.add(
                "compression-ratio-limit",
                f"{name!r} expands more than {limits.compression_ratio}:1",
            )
            return None
        entries.append(Entry(name=name, size=info.file_size, compressed_size=info.compress_size))
    return entries


def read_entry(
    archive: zipfile.ZipFile,
    name: str,
    limit: int,
    budget: Budget | None = None,
    *,
    code: str = "entry-bytes-limit",
) -> tuple[bytes, str]:
    """Read one entry, computing its digest from the bytes actually read.

    The declared size is checked against what was read rather than believed, so
    an entry whose header understates its content cannot smuggle bytes past a
    limit.

    `code` names the limit being enforced, which only the caller knows.
    """
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    with _unreadable(name):
        stream = archive.open(name)
    with stream:
        while True:
            with _unreadable(name):
                chunk = stream.read(64 << 10)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                # What was actually read counts, not the header's claim.
                raise ArchiveTooLargeError(budget.where(name) if budget is not None else name, code)
            digest.update(chunk)
            chunks.append(chunk)
    return b"".join(chunks), digest.hexdigest()


#: What `zipfile` raises for an entry whose bytes cannot be decoded: a bad
#: header or CRC, corrupt compressed data, truncation, an unsupported method,
#: or encryption.
_DECODE_FAILURES = (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError)


class UnreadableEntryError(Exception):
    """An entry's bytes could not be decoded, so the archive is not readable ZIP."""

    def __init__(self, name: str, cause: BaseException):
        super().__init__(f"{name!r} cannot be read: {cause}")
        self.name = name


@contextlib.contextmanager
def _unreadable(name: str) -> Iterator[None]:
    """Turns a decode failure while reading one entry into `UnreadableEntryError`."""
    try:
        yield
    except _DECODE_FAILURES as exc:
        raise UnreadableEntryError(name, exc) from exc


class ArchiveTooLargeError(ExhaustedError):
    """One entry alone exceeded what the caller allowed.

    An `ExhaustedError`, because every way of running out of allowance has to reach
    a caller as a violation rather than as an exception escaping `validate_*`.
    """

    def __init__(self, name: str, code: str = "entry-bytes-limit") -> None:
        self.name = name
        super().__init__(code, f"{name!r} exceeds the reader's limit for it")


def _check_path(name: str, report: Report, *, bundle: bool = False) -> bool:
    unsafe = _UNSAFE_PATH_BUNDLE if bundle else _UNSAFE_PATH
    if name.startswith("/"):
        report.add("absolute-archive-path", f"{name!r} is absolute")
        return False
    if "\\" in name:
        report.add("backslash-archive-path", f"{name!r} contains a backslash")
        return False
    if _DRIVE_LETTER.match(name):
        report.add("drive-letter-archive-path", f"{name!r} names a drive letter")
        return False
    if any(character < " " or character == "\x7f" for character in name):
        report.add("control-character-archive-path", f"{name!r} contains a control character")
        return False
    segments = name.rstrip("/").split("/")
    for segment in segments:
        if segment == "":
            report.add("empty-archive-path-segment", f"{name!r} has an empty path segment")
            return False
        if segment in (".", ".."):
            report.add(unsafe, f"{name!r} contains a {segment!r} segment")
            return False
    return True


def _regular_file(info: zipfile.ZipInfo) -> bool:
    """Reject links and devices without rejecting archives that record no mode.

    Many writers, including Python's own, store permission bits and leave the
    file-type field zero. Absence of a type is not evidence of a link, so only
    an explicitly non-regular type is refused.
    """
    mode = info.external_attr >> _UNIX_MODE_SHIFT
    file_type = mode & _S_IFMT
    return file_type in (0, _S_IFREG)


class _BytesReader:
    """A minimal seekable reader so zipfile never sees a filesystem path."""

    def __init__(self, data: bytes) -> None:
        import io

        self._stream = io.BytesIO(data)

    def __getattr__(self, name: str):  # pragma: no cover - delegation
        return getattr(self._stream, name)


def entry_names(entries: Iterator[Entry]) -> set[str]:
    return {entry.name for entry in entries}

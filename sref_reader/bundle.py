"""Reading a `.srefbundle` (specification sections 17 to 19).

A bundle is a container, not a second recipe model. The property it exists to
protect is that pulling one member out and deleting the rest leaves a package
that works on its own, so this reader validates every member as a package in
its own right rather than trusting the bundle manifest's word for it.

Verifying a member's digest establishes that the bytes are the ones the
manifest declared. It establishes nothing about whether those bytes are a valid
package, which is a separate question and asked separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import archive as archive_module
from . import budget as budget_module
from . import jsonio
from . import package as package_module
from . import schema as schema_module
from .errors import Report, SrefError
from .limits import DEFAULT, Limits
from .recipe import PORTABLE_ID

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .budget import Budget

_MANIFEST_JSON_CODES = {
    "duplicate-json-member": "duplicate-bundle-manifest-member",
    "trailing-json-value": "trailing-bundle-manifest-value",
    "json-depth-limit": "json-depth-limit",
}

#: Section 18 forbids a generation timestamp so that a writer given the same
#: members can produce the same bytes. Any member that would record when the
#: bundle was made is refused rather than ignored.
_TIMESTAMP_MEMBERS = {"generated_at", "created_at", "timestamp", "generated", "written_at"}


@dataclass
class Member:
    member_id: str
    recipe_id: str
    path: str
    package: package_module.Package


@dataclass
class Bundle:
    members: list[Member] = field(default_factory=list)
    manifest: dict[str, Any] = field(default_factory=dict)


def read(data: bytes, limits: Limits = DEFAULT) -> Bundle:
    bundle, report = validate(data, limits)
    if bundle is None or not report.valid:
        raise SrefError(report.violations)
    return bundle


def validate(
    data: bytes,
    limits: Limits = DEFAULT,
    *,
    budget: Budget | None = None,
) -> tuple[Bundle | None, Report]:
    report = Report()
    if budget is None:
        budget = budget_module.for_operation(limits)
    try:
        return _validate(data, budget, report)
    except budget_module.ExhaustedError as exhausted:
        report.add(exhausted.code, str(exhausted))
        return None, report
    except archive_module.UnreadableEntryError as unreadable:
        report.add("invalid-zip-bundle", f"not a readable ZIP archive: {unreadable}")
        return None, report


def members(data: bytes, limits: Limits = DEFAULT) -> Iterator[Member]:
    """Yield each member in turn, holding one at a time.

    This validates exactly what `read` validates, in the same order, and raises
    `SrefError` at the first violation. The one difference is the whole-bundle
    check that no entry rides along undeclared, which cannot be answered until
    every member has been seen. A caller that consumes the whole iterator gets
    it; a caller that stops early has chosen not to ask.

        for member in bundle.members(data):
            store(member.recipe_id, member.package)

    Nothing is retained between iterations, so the peak cost is the largest
    single member rather than the sum of them.
    """
    report = Report()
    budget = budget_module.for_operation(limits)
    try:
        opened = _open(data, budget, report)
        if opened is None:
            raise SrefError(report.violations)
        archive, _manifest, declared, names = opened

        seen_ids: set[str] = set()
        declared_paths: set[str] = set()
        for index, entry in enumerate(declared):
            path = f"manifest.json:recipes[{index}]"
            if not isinstance(entry, dict):
                continue
            member = _read_member(
                archive,
                entry,
                names,
                seen_ids,
                declared_paths,
                report,
                path,
                budget,
            )
            if report.violations:
                raise SrefError(report.violations)
            if member is not None:
                yield member

        _check_undeclared(names, declared_paths, report)
        if report.violations:
            raise SrefError(report.violations)
    except budget_module.ExhaustedError as exhausted:
        report.add(exhausted.code, str(exhausted))
        raise SrefError(report.violations) from exhausted
    except archive_module.UnreadableEntryError as unreadable:
        report.add("invalid-zip-bundle", f"not a readable ZIP archive: {unreadable}")
        raise SrefError(report.violations) from unreadable


def _open(
    data: bytes,
    budget: Budget,
    report: Report,
) -> tuple[Any, dict[str, Any], list[Any], set[str]] | None:
    """Everything a bundle has to establish before its first member is read."""
    limits = budget.limits
    archive = archive_module.open_archive(data, report, limits, bundle=True)
    if archive is None:
        return None
    entries = archive_module.check_entries(archive, report, budget, bundle=True)
    if entries is None:
        return None

    names = {entry.name for entry in entries}
    if "manifest.json" not in names:
        report.add("missing-bundle-manifest", "a bundle carries exactly one root manifest.json")
        return None

    manifest = _read_manifest(archive, report, budget)
    if manifest is None:
        return None

    declared = manifest.get("recipes")
    if not isinstance(declared, list) or not declared:
        report.add("empty-bundle", "a bundle carries at least one recipe", "manifest.json")
        return None
    budget.spend_members(len(declared))
    return archive, manifest, declared, names


def _validate(data: bytes, budget: Budget, report: Report) -> tuple[Bundle | None, Report]:
    opened = _open(data, budget, report)
    if opened is None:
        return None, report
    archive, manifest, declared, names = opened

    collected: list[Member] = []
    seen_ids: set[str] = set()
    declared_paths: set[str] = set()
    for index, entry in enumerate(declared):
        path = f"manifest.json:recipes[{index}]"
        if not isinstance(entry, dict):
            continue
        member = _read_member(archive, entry, names, seen_ids, declared_paths, report, path, budget)
        if member is not None:
            collected.append(member)

    _check_undeclared(names, declared_paths, report)
    if report.violations:
        return None, report
    return Bundle(members=collected, manifest=manifest), report


def _read_manifest(archive, report: Report, budget: Budget) -> dict[str, Any] | None:
    data, _ = archive_module.read_entry(
        archive, "manifest.json", budget.limits.json_bytes, budget, code="json-bytes-limit"
    )
    try:
        manifest = jsonio.loads(
            data, max_bytes=budget.limits.json_bytes, max_depth=budget.limits.json_depth
        )
    except jsonio.JSONRejectedError as exc:
        report.add(
            _MANIFEST_JSON_CODES.get(exc.code, "malformed-package-json"),
            str(exc),
            "manifest.json",
        )
        return None
    if not isinstance(manifest, dict):
        report.add("malformed-package-json", "a bundle manifest is a JSON object", "manifest.json")
        return None

    if "version" in manifest and manifest.get("version") != 1:
        report.add(
            "unsupported-bundle-version",
            f"bundle declares unsupported version {manifest.get('version')!r}",
            "manifest.json.version",
        )

    # Before the member walk below, for the same reason the package reader
    # checks its manifest first: that walk reads `recipes` and trusts the rest.
    schema_module.check(
        manifest,
        "bundle-manifest",
        report,
        "manifest.json",
        code="malformed-package-json",
    )

    # Checked separately from the closed member set so the violation names
    # `bundle-manifest-timestamp`.
    for member in _TIMESTAMP_MEMBERS & manifest.keys():
        report.add(
            "bundle-manifest-timestamp",
            f"a bundle manifest records no clock, and carries {member!r}",
            "manifest.json",
        )
    return manifest


def _read_member(
    archive,
    entry: dict[str, Any],
    names: set[str],
    seen_ids: set[str],
    declared_paths: set[str],
    report: Report,
    path: str,
    budget: Budget,
) -> Member | None:
    member_id = entry.get("member_id")
    if not isinstance(member_id, str) or not PORTABLE_ID.match(member_id):
        report.add("bundle-member-id-required", "a member declares a portable member id", path)
        return None
    if member_id in seen_ids:
        report.add("duplicate-bundle-member-id", f"member id {member_id!r} is declared twice", path)
        return None
    seen_ids.add(member_id)

    declared_path = entry.get("path")
    expected = f"recipes/{member_id}.sref"
    if declared_path != expected:
        # The path is derived from the member id rather than chosen, so a
        # mismatch is the manifest disagreeing with itself.
        code = (
            "unsafe-bundle-member-path" if _unsafe(declared_path) else "bundle-member-path-mismatch"
        )
        report.add(
            code,
            f"member {member_id!r} declares path {declared_path!r}, not {expected!r}",
            path,
        )
        return None
    declared_paths.add(declared_path)

    if declared_path not in names:
        report.add("missing-bundle-member", f"{declared_path!r} is declared but not carried", path)
        return None

    data, digest = archive_module.read_entry(
        archive,
        declared_path,
        budget.limits.bytes_per_entry,
        budget,
    )
    if entry.get("size") != len(data):
        report.add(
            "bundle-member-size-mismatch",
            f"{declared_path!r} is not the declared size",
            path,
        )
        return None
    if entry.get("sha256") != digest:
        report.add(
            "bundle-member-digest-mismatch",
            f"{declared_path!r} does not match its digest",
            path,
        )
        return None

    # Verified bytes are not a valid package, so validate the member, on this
    # operation's remaining allowance.
    opened, member_report = package_module.validate_within(
        data,
        budget.entered(declared_path),
        Report(),
    )
    if opened is None or not member_report.valid:
        # The bundle layer's requirement, carrying the member's own category.
        report.add(
            "invalid-bundle-member-package",
            f"{declared_path!r} is not a valid package: {member_report.codes}",
            path,
            category=member_report.category,
        )
        return None

    recipe_id = opened.recipe.get("id")
    if entry.get("recipe_id") != recipe_id:
        report.add(
            "bundle-recipe-id-disagreement",
            f"the manifest says {entry.get('recipe_id')!r} and the member carries {recipe_id!r}",
            path,
        )
        return None
    return Member(member_id=member_id, recipe_id=recipe_id, path=declared_path, package=opened)


def _unsafe(path: Any) -> bool:
    if not isinstance(path, str):
        return True
    return (
        path.startswith("/")
        or "\\" in path
        or ".." in path.split("/")
        or not path.startswith("recipes/")
    )


def _check_undeclared(names: set[str], declared_paths: set[str], report: Report) -> None:
    for name in names:
        if name == "manifest.json" or name in declared_paths:
            continue
        if not name.startswith("recipes/"):
            report.add("bundle-entry-outside-recipes", f"{name!r} is outside recipes/")
        else:
            report.add("undeclared-bundle-entry", f"{name!r} is carried but not declared")

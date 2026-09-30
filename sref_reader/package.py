"""Reading a `.sref` package (specification sections 14 to 16).

A package is a recipe plus every file it names. The point of the format is that
it is self-contained: a recipe that references a photograph and does not carry
it is broken wherever it lands, so the manifest and the recipe must agree about
every asset, and every declared byte must verify.

Nothing is committed anywhere until all of that has passed. The reader returns
bytes in memory; where they end up is the caller's decision, made after the
package has been proven rather than during.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import archive as archive_module
from . import budget as budget_module
from . import jsonio
from . import recipe as recipe_module
from . import schema as schema_module
from .errors import Report, SrefError
from .limits import DEFAULT, Limits

if TYPE_CHECKING:
    from .budget import Budget

#: Reading a package's own JSON reports different categories from reading a
#: bare recipe document, because a package failure names which file inside the
#: archive was wrong.
_MANIFEST_JSON_CODES = {
    "duplicate-json-member": "duplicate-package-manifest-member",
    "trailing-json-value": "trailing-package-manifest-value",
    "json-depth-limit": "json-depth-limit",
}


@dataclass
class Package:
    recipe: dict[str, Any]
    assets: dict[str, bytes] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)


def read(data: bytes, limits: Limits = DEFAULT) -> Package:
    package, report = validate(data, limits)
    if package is None or not report.valid:
        raise SrefError(report.violations)
    return package


def validate(data: bytes, limits: Limits = DEFAULT) -> tuple[Package | None, Report]:
    """Validate a package and return it with everything that was wrong."""
    report = Report()
    budget = budget_module.for_operation(limits)
    try:
        return validate_within(data, budget, report)
    except budget_module.ExhaustedError as exhausted:
        # Every way of running out of allowance arrives here, so `validate_*`
        # returns violations for all of them rather than for most of them.
        report.add(exhausted.code, str(exhausted))
        return None, report


def validate_within(data: bytes, budget: Budget, report: Report) -> tuple[Package | None, Report]:
    """Validate a package on an operation's existing allowance.

    Exhaustion propagates rather than becoming a violation here, since running
    out of budget does not show the member is invalid. An entry whose bytes
    cannot be decoded does show it, so it becomes `invalid-zip`.
    """
    try:
        return _validate_within(data, budget, report)
    except archive_module.UnreadableEntryError as unreadable:
        report.add("invalid-zip", f"not a readable ZIP archive: {unreadable}")
        return None, report


def _validate_within(data: bytes, budget: Budget, report: Report) -> tuple[Package | None, Report]:
    limits = budget.limits
    archive = archive_module.open_archive(data, report, limits)
    if archive is None:
        return None, report
    entries = archive_module.check_entries(archive, report, budget)
    if entries is None:
        return None, report

    names = {entry.name: entry for entry in entries}
    if "manifest.json" not in names:
        report.add("missing-manifest", "a package carries exactly one root manifest.json")
        return None, report
    if "recipe.json" not in names:
        report.add("missing-recipe", "a package carries exactly one root recipe.json")
        return None, report

    manifest = _read_manifest(archive, report, budget)
    if manifest is None:
        return None, report

    declared_recipe = manifest.get("recipe") or {}
    recipe_bytes, recipe_digest = archive_module.read_entry(
        archive,
        "recipe.json",
        limits.json_bytes,
        budget,
        code="json-bytes-limit",
    )
    if declared_recipe.get("sha256") != recipe_digest:
        report.add(
            "recipe-digest-mismatch",
            "recipe.json does not match the digest the manifest records",
        )
    if declared_recipe.get("size") != len(recipe_bytes):
        report.add("recipe-digest-mismatch", "recipe.json is not the size the manifest records")

    document, recipe_report = recipe_module.validate(recipe_bytes, max_depth=limits.json_depth)
    report.violations.extend(recipe_report.violations)
    if document is None:
        return None, report

    assets = _check_assets(archive, document, manifest, names, report, budget)
    _check_undeclared(names, document, report)
    if report.violations:
        return None, report
    return Package(recipe=document, assets=assets, manifest=manifest), report


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
        report.add("malformed-package-json", "a package manifest is a JSON object", "manifest.json")
        return None

    package_header = manifest.get("sref_package")
    if isinstance(package_header, dict) and package_header.get("version") != "0.1.0":
        report.add(
            "unsupported-package-version",
            f"package declares unsupported version {package_header.get('version')!r}",
            "manifest.json.sref_package.version",
        )

    # Schema first: the agreement checks below assume the members it requires.
    schema_module.check(
        manifest,
        "package-manifest",
        report,
        "manifest.json",
        code="malformed-package-json",
    )
    return manifest


def _check_assets(
    archive,
    document: dict[str, Any],
    manifest: dict[str, Any],
    names: dict[str, archive_module.Entry],
    report: Report,
    budget: Budget,
) -> dict[str, bytes]:
    """Check that the recipe, the manifest, and the archive all agree.

    An asset present in only one of the three is invalid. Agreement has to be
    exact on every field, because a manifest that records a different digest
    from the recipe leaves undefined which one a consumer should believe.
    """
    declared = {
        asset.get("id"): asset for asset in document.get("assets") or [] if isinstance(asset, dict)
    }
    manifest_assets: dict[str, dict[str, Any]] = {}
    for entry in manifest.get("assets") or []:
        if not isinstance(entry, dict):
            continue
        asset_id = entry.get("id")
        if asset_id in manifest_assets:
            report.add(
                "duplicate-manifest-asset",
                f"manifest declares {asset_id!r} twice",
                "manifest.json",
            )
            continue
        manifest_assets[asset_id] = entry

    for asset_id in manifest_assets.keys() - declared.keys():
        report.add(
            "recipe-manifest-disagreement",
            f"manifest declares {asset_id!r}, the recipe does not",
        )
    for asset_id in declared.keys() - manifest_assets.keys():
        report.add(
            "recipe-manifest-disagreement",
            f"the recipe declares {asset_id!r}, the manifest does not",
        )

    contents: dict[str, bytes] = {}
    for asset_id, asset in declared.items():
        entry = manifest_assets.get(asset_id)
        if entry is None:
            continue
        for member in ("path", "media_type", "sha256", "size"):
            if asset.get(member) != entry.get(member):
                report.add(
                    "recipe-manifest-disagreement",
                    f"{asset_id!r} disagrees on {member!r} between the recipe and the manifest",
                )
        path = asset.get("path")
        if path not in names:
            report.add("missing-asset-entry", f"{path!r} is declared but not carried")
            continue
        data, digest = archive_module.read_entry(
            archive,
            path,
            budget.limits.bytes_per_entry,
            budget,
        )
        if len(data) != asset.get("size"):
            report.add("asset-size-mismatch", f"{path!r} is not the size the recipe records")
        if digest != asset.get("sha256"):
            report.add("asset-digest-mismatch", f"{path!r} does not match its declared digest")
        contents[asset_id] = data
    return contents


def _check_undeclared(
    names: dict[str, archive_module.Entry],
    document: dict[str, Any],
    report: Report,
) -> None:
    """Nothing rides along that the recipe did not declare."""
    allowed = {"manifest.json", "recipe.json"}
    allowed |= {
        asset.get("path")
        for asset in document.get("assets") or []
        if isinstance(asset, dict) and isinstance(asset.get("path"), str)
    }
    for name in names:
        if name not in allowed:
            report.add(
                "undeclared-archive-entry",
                f"{name!r} is not declared by the recipe or manifest",
            )

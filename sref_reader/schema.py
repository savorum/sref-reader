"""Running the pinned JSON Schemas as a second gate.

Every artifact SREF defines has a published schema, and this reader checks the
recipe, the package manifest and the bundle manifest against theirs. The
schemas are not the primary check: almost everything they say is also checked
semantically, with a specific conformance category and a message that names the
requirement rather than the keyword that failed.

They are here for the rest: the fixed members, the exact versions, the
constant paths and media types, the closed member sets. A schema-only failure
gets the generic code, and `Report.primary` prefers a specific one.
"""

from __future__ import annotations

import functools
from typing import Any

from . import snapshot
from .errors import GENERIC_STRUCTURAL, Report

#: The pinned schemas, by the name a caller asks for.
_SCHEMAS = {
    "recipe": snapshot.recipe_schema,
    "package-manifest": snapshot.manifest_schema,
    "bundle-manifest": snapshot.bundle_manifest_schema,
}


def check(
    document: Any,
    name: str,
    report: Report,
    prefix: str = "",
    *,
    code: str = GENERIC_STRUCTURAL,
) -> None:
    """Check one document against a pinned schema, reporting what fails.

    `code` is the category to report under. A recipe gets the generic one: the
    schema is a second gate there, behind checks that name their own
    requirements. A manifest gets `malformed-package-json`, which is the
    published category for a package whose own JSON is invalid, and a manifest
    that does not satisfy its schema is exactly that.
    """
    validator = _validator(name)
    if validator is None:
        return
    for error in validator.iter_errors(document):
        path = ".".join(str(part) for part in error.absolute_path)
        if prefix:
            path = f"{prefix}.{path}" if path else prefix
        report.add(code, error.message, path)


@functools.cache
def _validator(name: str):
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - optional hardening
        return None
    return jsonschema.Draft202012Validator(_SCHEMAS[name]())


#: The category for a member the declared version does not define. Named for
#: what is wrong rather than for what the member might have been: in a document
#: declaring the version this build implements, an unrecognized non-`x-` member
#: is not established as standard at all.
VERSION_SCOPED_MEMBER = "member-not-defined-by-version"


def check_version_scoped_members(
    document: Any, report: Report, *, allowed: frozenset[str] = frozenset()
) -> None:
    """Refuse members the document's own declared version does not define.

    Section 13 permits unknown standard members only when reading a newer
    compatible version, so in a document declaring this build's version an
    unrecognized non-`x-` member is a mistake. It is checked against a closed
    copy of the pinned schema. Callers skip this for compatible-newer
    documents.
    """
    validator = _closed_recipe_validator()
    if validator is None:
        return
    for error in validator.iter_errors(document):
        if error.validator != "additionalProperties":
            # The open schema reports everything else.
            continue
        path = ".".join(str(part) for part in error.absolute_path)
        if not path and isinstance(document, dict):
            unexpected = set(document) - set(_closed_recipe_properties())
            if unexpected and unexpected <= allowed:
                continue
        report.add(VERSION_SCOPED_MEMBER, error.message, path)


@functools.cache
def _closed_recipe_validator():
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - optional hardening
        return None
    return jsonschema.Draft202012Validator(_closed(snapshot.recipe_schema()))


def _closed(node: Any) -> Any:
    if isinstance(node, dict):
        closed = {name: _closed(value) for name, value in node.items()}
        if closed.get("additionalProperties") is True:
            closed["additionalProperties"] = False
        return closed
    if isinstance(node, list):
        return [_closed(item) for item in node]
    return node


def _closed_recipe_properties() -> list[str]:
    return list(snapshot.recipe_schema().get("properties", {}))

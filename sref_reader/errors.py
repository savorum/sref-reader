"""Violations a reader reports, and the exception that carries them.

The specification asks validators to "report all independent failures they can
identify safely, rather than stop after the first field error" (section 23), so
validation collects violations rather than raising on the first one. The
exception exists for callers who would rather not check a result object.

Every violation carries two identifiers, because SREF publishes two and they
answer different questions (specification section 22.1).

`category` is one of seven normative failure categories, the portable one a
caller branches on.

`code` is this library's requirement identifier, drawn from the conformance
manifest's `requirement_id` values. It names the exact rule that was broken,
but is not SREF's portable vocabulary.

Human-readable messages are ours and may change. Categories may not; codes
follow the published conformance snapshot that defines them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Reported when the published schema rejects something no prose rule here
#: covers. It is deliberately the least specific code this reader emits.
GENERIC_STRUCTURAL = "schema-violation"

#: The same role for a package or bundle manifest whose own JSON or schema is
#: wrong and no prose rule here names the fault.
GENERIC_MANIFEST = "malformed-package-json"

#: Codes that answer "this is structurally wrong" without naming a rule. They
#: lose to any code that names one.
_LEAST_SPECIFIC = frozenset({GENERIC_STRUCTURAL, GENERIC_MANIFEST})


#: Specification section 22.1. `invalid-artifact` is the fallback, so the table
#: below lists only the codes that mean something more specific; everything
#: else is the fallback by definition rather than by omission.
INVALID_ARTIFACT = "invalid-artifact"

FAILURE_CATEGORIES = (
    INVALID_ARTIFACT,
    "unsupported-format-version",
    "unsupported-registry-version",
    "unknown-unit",
    "integrity-failure",
    "unsafe-archive",
    "resource-limit",
)

_CATEGORY_BY_CODE = {
    "unsupported-format-version": "unsupported-format-version",
    "unsupported-registry-version": "unsupported-registry-version",
    "unsupported-package-version": "unsupported-format-version",
    "unsupported-bundle-version": "unsupported-format-version",
    "unknown-unit": "unknown-unit",
    # A declaration disagreeing with the bytes or members it describes. Not the
    # same thing as a declaration that is wrong on its own terms, which stays
    # the fallback: a manifest naming the same asset twice is malformed, and
    # nothing about the archive contradicts it.
    "asset-digest-mismatch": "integrity-failure",
    "asset-size-mismatch": "integrity-failure",
    "recipe-digest-mismatch": "integrity-failure",
    "recipe-manifest-disagreement": "integrity-failure",
    "missing-asset-entry": "integrity-failure",
    "undeclared-archive-entry": "integrity-failure",
    "bundle-member-digest-mismatch": "integrity-failure",
    "bundle-member-size-mismatch": "integrity-failure",
    "bundle-recipe-id-disagreement": "integrity-failure",
    "missing-bundle-member": "integrity-failure",
    "undeclared-bundle-entry": "integrity-failure",
    # The archive rules of sections 15 and 19, which are applied before
    # anything is read out of the archive. An entry they reject is unsafe
    # whether or not a manifest would have declared it.
    "absolute-archive-path": "unsafe-archive",
    "backslash-archive-path": "unsafe-archive",
    "control-character-archive-path": "unsafe-archive",
    "drive-letter-archive-path": "unsafe-archive",
    "empty-archive-path-segment": "unsafe-archive",
    "unsafe-archive-path": "unsafe-archive",
    "unsafe-bundle-member-path": "unsafe-archive",
    "bundle-entry-outside-recipes": "unsafe-archive",
    "duplicate-archive-entry": "unsafe-archive",
    "nonregular-archive-entry": "unsafe-archive",
    # This reader's ceilings, which are policy rather than a statement that the
    # artifact is invalid.
    "compression-ratio-limit": "resource-limit",
    "entry-bytes-limit": "resource-limit",
    "entry-count-limit": "resource-limit",
    "expanded-bytes-limit": "resource-limit",
    "json-bytes-limit": "resource-limit",
    "json-depth-limit": "resource-limit",
    "archive-bytes-limit": "resource-limit",
    "member-count-limit": "resource-limit",
}


def category_for(code: str) -> str:
    """The normative failure category a requirement code reports under."""
    return _CATEGORY_BY_CODE.get(code, INVALID_ARTIFACT)


@dataclass(frozen=True)
class Violation:
    """One independent failure, addressed by a JSON pointer-ish path."""

    code: str
    message: str
    path: str = ""
    #: Set only where the category cannot be read off the code: an invalid
    #: bundle member keeps its own category under the bundle's requirement.
    reported_category: str | None = None

    @property
    def category(self) -> str:
        """The normative category of specification section 22.1."""
        return self.reported_category or category_for(self.code)

    @property
    def requirement_id(self) -> str:
        """The snapshot-scoped conformance requirement this finding names."""
        return self.code

    def __str__(self) -> str:
        where = f" at {self.path}" if self.path else ""
        return f"{self.code}{where}: {self.message}"


class SrefError(Exception):
    """Raised by the convenience readers when a document is not acceptable."""

    def __init__(self, violations: list[Violation]) -> None:
        self.violations = list(violations)
        super().__init__("; ".join(str(v) for v in self.violations) or "invalid SREF document")

    @property
    def codes(self) -> list[str]:
        """Compatibility alias for :attr:`requirement_ids`."""
        return [violation.code for violation in self.violations]

    @property
    def requirement_ids(self) -> list[str]:
        return [violation.requirement_id for violation in self.violations]

    @property
    def categories(self) -> list[str]:
        return [violation.category for violation in self.violations]

    @property
    def category(self) -> str | None:
        """The portable answer to "why was this rejected?".

        A caller that branches on one category gets the one belonging to the
        violation this library considers primary. A caller handling several
        failures reads `violations` and takes each one's own category, which is
        what section 22.2 describes.
        """
        return Report(self.violations).category

    @property
    def requirement_id(self) -> str | None:
        """The primary snapshot-scoped requirement, when one was identified."""
        return Report(self.violations).requirement_id


class UnsupportedVersionError(SrefError):
    """A document from a version line this build does not implement.

    Kept distinct because section 5 requires a reader to report the unsupported
    version rather than reinterpret the document, and a caller usually wants to
    tell those two situations apart.
    """


@dataclass
class Report:
    """The result of validating one artifact."""

    violations: list[Violation] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.violations

    @property
    def codes(self) -> list[str]:
        """Compatibility alias for :attr:`requirement_ids`."""
        return [violation.code for violation in self.violations]

    @property
    def requirement_ids(self) -> list[str]:
        return [violation.requirement_id for violation in self.violations]

    @property
    def categories(self) -> list[str]:
        return [violation.category for violation in self.violations]

    @property
    def category(self) -> str | None:
        """The category of the primary violation, or None when valid."""
        violation = self.primary_violation
        return violation.category if violation is not None else None

    @property
    def primary(self) -> str | None:
        """The code that best answers "why was this rejected?", preferring a
        specific requirement over the `schema-violation` and
        `malformed-package-json` catch-alls.
        """
        violation = self.primary_violation
        return violation.code if violation is not None else None

    @property
    def requirement_id(self) -> str | None:
        """The primary snapshot-scoped requirement, when one was identified."""
        return self.primary

    @property
    def primary_violation(self) -> Violation | None:
        """The violation `primary` and `category` both answer from.

        Kept as the violation rather than its code, because a code does not
        always determine the category: a bundle member's failure is reported
        under the bundle layer's requirement and keeps the member's category.
        """
        for violation in self.violations:
            if violation.code not in _LEAST_SPECIFIC:
                return violation
        return self.violations[0] if self.violations else None

    def add(self, code: str, message: str, path: str = "", category: str | None = None) -> None:
        self.violations.append(
            Violation(code=code, message=message, path=path, reported_category=category)
        )

    def extend(self, other: Report, prefix: str = "") -> None:
        for violation in other.violations:
            path = violation.path
            if prefix:
                path = f"{prefix}{path}" if path.startswith("[") or not path else f"{prefix}.{path}"
            self.violations.append(
                Violation(
                    violation.code,
                    violation.message,
                    path or prefix,
                    violation.reported_category,
                )
            )

    def raise_if_invalid(self) -> None:
        if self.violations:
            raise SrefError(self.violations)

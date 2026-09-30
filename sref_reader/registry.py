"""The unit registry: loading, semantic validation, lookup, and conversion.

A unit ID is the whole of a unit's identity in SREF (section 9). `cup` is a
spelling; `volume.cup.us.customary` and `volume.cup.au` are different units.

The registry is versioned independently of the recipe specification, so a
document names both. This module validates a registry snapshot against the
rules JSON Schema cannot express, and answers the two questions a recipe
reader asks of it: does this unit ID exist, and what does it convert to.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from fractions import Fraction
from typing import TYPE_CHECKING, Any

from . import rational
from .language import well_formed_bcp47

if TYPE_CHECKING:
    from .errors import Report

#: Dimensions whose units may carry an affine definition. An affine conversion
#: has an offset, and an offset is meaningful only where zero is a convention
#: rather than an absence. A "gram" with an offset would not be a mass.
AFFINE_DIMENSIONS = {"temperature"}


@dataclass(frozen=True)
class Unit:
    id: str
    kind: str
    dimension: str | None
    definition: dict[str, Any] | None
    status: str
    replacement: str | None = None

    @property
    def physical(self) -> bool:
        return self.kind == "physical"


@dataclass
class Registry:
    version: str
    units: dict[str, Unit] = field(default_factory=dict)

    def __contains__(self, unit_id: object) -> bool:
        return unit_id in self.units

    def get(self, unit_id: str) -> Unit | None:
        return self.units.get(unit_id)

    def dimension_of(self, unit_id: str) -> str | None:
        unit = self.units.get(unit_id)
        return unit.dimension if unit else None

    def convert(self, value: Fraction, source: str, target: str) -> tuple[Fraction, bool]:
        """Convert an exact value between compatible physical units.

        Returns the converted value and whether the result must be reported as
        approximate, which it is as soon as any traversed definition says it is
        not exact. Mass-to-volume is not a conversion and is refused: it needs
        the density of a specific ingredient, which SREF does not carry.
        """
        first, second = self.units.get(source), self.units.get(target)
        if first is None or second is None:
            raise UnknownUnitError(source if first is None else target)
        if not (first.physical and second.physical):
            raise NonphysicalUnitError(source, target)
        if first.dimension != second.dimension:
            raise IncompatibleDimensionError(source, target)

        (source_multiplier, source_offset), exact_in = self._to_base(first)
        (target_multiplier, target_offset), exact_out = self._to_base(second)
        base_value = value * source_multiplier + source_offset
        # Inverting the target's own definition is what makes an affine unit
        # work in both directions without a second table of reverse factors.
        converted = (base_value - target_offset) / target_multiplier
        return converted, not (exact_in and exact_out)

    def convert_amount(self, amount: dict[str, Any], source: str, target: str) -> dict[str, Any]:
        """Convert an exact or range amount, propagating approximation.

        Ranges convert endpoint by endpoint. Registry multipliers are positive,
        so a converted range keeps its order and never needs re-sorting; if it
        did, the definition that produced it would already be invalid.
        """
        result: dict[str, Any] = {}
        approximate = bool(amount.get("approximate", False))
        for member in ("value", "min", "max"):
            if member not in amount:
                continue
            converted, inexact = self.convert(
                rational.parse(amount[member], signed=True),
                source,
                target,
            )
            result[member] = rational.to_string(converted)
            approximate = approximate or inexact
        if approximate:
            result["approximate"] = True
        return result

    def _to_base(self, unit: Unit) -> tuple[tuple[Fraction, Fraction], bool]:
        """Collapse a unit's definition chain into one affine step to its base."""
        multiplier, offset, exact = Fraction(1), Fraction(0), True
        current = unit
        while current.definition and current.definition["kind"] != "base":
            definition = current.definition
            step_multiplier = rational.parse(definition["multiplier"], signed=True)
            step_offset = rational.parse(definition.get("offset", "0"), signed=True)
            multiplier = multiplier * step_multiplier
            offset = offset * step_multiplier + step_offset
            exact = exact and bool(definition.get("exact", False))
            current = self.units[definition["base_unit"]]
        return (multiplier, offset), exact


class UnknownUnitError(LookupError):
    def __init__(self, unit_id: str) -> None:
        self.unit_id = unit_id
        super().__init__(f"unknown unit {unit_id!r}")


class IncompatibleUnitsError(ValueError):
    """A conversion the registry cannot perform.

    The two subclasses are the two different reasons, and the corpus names them
    separately because they mean different things to a caller: one is a
    question SREF declines to answer, the other is a question that has no
    answer at all.
    """

    code = "incompatible-unit"

    def __init__(self, source: str, target: str, reason: str) -> None:
        self.source, self.target = source, target
        super().__init__(f"cannot convert {source!r} to {target!r}: {reason}")


class NonphysicalUnitError(IncompatibleUnitsError):
    """A count or container unit has no physical size to convert.

    Two cloves of garlic weigh whatever those two cloves weigh. Answering would
    mean inventing a size the recipe never stated.
    """

    code = "nonphysical-unit"

    def __init__(self, source: str, target: str) -> None:
        super().__init__(source, target, "count and container units have no physical size")


class IncompatibleDimensionError(IncompatibleUnitsError):
    """Mass to volume is not a unit conversion (section 9.3).

    It needs the density of a specific ingredient, which is knowledge about
    food rather than about units, and SREF deliberately does not carry it.
    """

    code = "incompatible-dimension"

    def __init__(self, source: str, target: str) -> None:
        super().__init__(source, target, "units measure different dimensions")


def load(document: Any, report: Report) -> Registry | None:
    """Validate a registry snapshot and return it when it is usable.

    Structural validation belongs to the registry schema; this is the semantic
    layer.
    """
    if not isinstance(document, dict) or not isinstance(document.get("units"), list):
        report.add("malformed-registry", "a registry has a version and a units array")
        return None

    units: dict[str, Unit] = {}
    bases: dict[str, str] = {}
    normalized_aliases: dict[tuple[str, str], str] = {}

    for index, raw in enumerate(document["units"]):
        path = f"units[{index}]"
        unit_id = raw.get("id")
        if unit_id in units:
            report.add("duplicate-unit-id", f"unit {unit_id!r} is declared more than once", path)
            continue

        definition = raw.get("definition")
        status = raw.get("status", "active")
        unit = Unit(
            id=unit_id,
            kind=raw.get("kind", ""),
            dimension=raw.get("dimension"),
            definition=definition,
            status=status,
            replacement=raw.get("replacement"),
        )
        units[unit_id] = unit

        _check_lifecycle(raw, unit, report, path)
        _check_definition(raw, unit, definition, bases, report, path)
        _check_labels_and_aliases(raw, unit_id, normalized_aliases, report, path)

    _check_replacements(units, report)
    _check_base_references(units, report)
    if report.violations:
        return None
    return Registry(version=document.get("version", ""), units=units)


def _check_lifecycle(raw: dict[str, Any], unit: Unit, report: Report, path: str) -> None:
    # A retired unit still has to be readable, so it must say what replaced it;
    # an active one must not, because a replacement is how a reader recognizes
    # that an identity has been superseded.
    if unit.status == "active":
        if raw.get("replacement"):
            report.add(
                "active-unit-replacement",
                "an active unit does not declare a replacement",
                path,
            )
        if not raw.get("aliases"):
            report.add(
                "active-unit-without-alias",
                "an active unit needs at least one alias set",
                path,
            )
    elif not raw.get("replacement"):
        report.add(
            "missing-unit-replacement",
            f"retired unit {unit.id!r} names no replacement",
            path,
        )

    if unit.physical and not raw.get("references"):
        report.add(
            "physical-unit-reference-required",
            "a physical unit cites the standard that defines it",
            path,
        )


def _check_definition(
    _raw: dict[str, Any],
    unit: Unit,
    definition: dict[str, Any] | None,
    bases: dict[str, str],
    report: Report,
    path: str,
) -> None:
    if definition is None:
        return
    kind = definition.get("kind")
    if kind == "base":
        dimension = unit.dimension or ""
        if dimension in bases:
            report.add(
                "multiple-dimension-bases",
                f"{dimension} already has base unit {bases[dimension]!r}",
                path,
            )
        else:
            bases[dimension] = unit.id
        return

    if kind == "affine" and unit.dimension not in AFFINE_DIMENSIONS:
        report.add(
            "affine-nontemperature-unit",
            "an affine definition is only meaningful for temperature",
            path,
        )

    try:
        multiplier = rational.parse(definition.get("multiplier"), signed=True)
    except rational.NotCanonicalError as exc:
        report.add(exc.code, str(exc), f"{path}.definition.multiplier")
        return
    if multiplier <= 0:
        report.add(
            "nonpositive-unit-multiplier",
            "a conversion multiplier is positive, so conversion preserves order",
            f"{path}.definition.multiplier",
        )


def _check_labels_and_aliases(
    raw: dict[str, Any],
    unit_id: str,
    normalized_aliases: dict[tuple[str, str], str],
    report: Report,
    path: str,
) -> None:
    # An alias set is identified by its language, locale and case sensitivity,
    # and one unit may declare each of those only once. Two sets for `en` with
    # the same sensitivity would leave which one applies undefined, while `mL`
    # case-sensitively and `ml` case-insensitively are legitimately two sets.
    # Language tags compare case-insensitively, so `en` and `EN` are one scope.
    alias_sets: set[tuple[str, str, bool]] = set()
    for language in raw.get("labels") or {}:
        if not well_formed_bcp47(language):
            report.add(
                "invalid-language-tag",
                f"{language!r} is not a BCP 47 tag",
                f"{path}.labels",
            )

    for index, alias in enumerate(raw.get("aliases") or []):
        alias_path = f"{path}.aliases[{index}]"
        language = alias.get("language", "")
        locale = alias.get("locale", "")
        if not well_formed_bcp47(language):
            report.add("invalid-language-tag", f"{language!r} is not a BCP 47 tag", alias_path)
        if locale and not well_formed_bcp47(locale):
            report.add("invalid-language-tag", f"{locale!r} is not a BCP 47 tag", alias_path)

        case_sensitive = bool(alias.get("case_sensitive", False))
        scope = (language.casefold(), locale.casefold(), case_sensitive)
        if scope in alias_sets:
            report.add(
                "duplicate-unit-alias-set",
                "one unit declares two alias sets for the same language, locale and case rule",
                alias_path,
            )
        alias_sets.add(scope)

        # A form that normalizes onto another is a form whose unit is
        # undecidable, whether the collision is inside one set or across units.
        for form in alias.get("forms") or []:
            key = (
                locale.casefold() or language.casefold(),
                _normalize_alias(form, case_sensitive=case_sensitive),
            )
            owner = normalized_aliases.get(key)
            if owner is not None:
                report.add(
                    "duplicate-normalized-unit-alias",
                    f"{form!r} already identifies {owner!r} in the same scope",
                    alias_path,
                )
            normalized_aliases[key] = unit_id


def _check_replacements(units: dict[str, Unit], report: Report) -> None:
    """A retired unit must point somewhere a reader can actually go.

    Naming a replacement that is not in the registry is the same failure as
    naming none: a document using the retired identity has nowhere to be read
    forward to, and the schema's presence rule cannot see it.
    """
    for unit in units.values():
        if unit.status == "active" or not unit.replacement:
            continue
        if unit.replacement not in units:
            report.add(
                "missing-unit-replacement",
                f"retired unit {unit.id!r} names unknown replacement {unit.replacement!r}",
            )


def _check_base_references(units: dict[str, Unit], report: Report) -> None:
    for unit in units.values():
        definition = unit.definition
        if not definition or definition.get("kind") == "base":
            continue
        base = definition.get("base_unit")
        if base not in units:
            report.add("unknown-unit-base", f"{unit.id!r} converts through unknown unit {base!r}")
            continue
        # A cycle would make conversion nonterminating, and a registry that can
        # hang a reader is a denial of service in every implementation at once.
        seen = {unit.id}
        current = units[base]
        while current.definition and current.definition.get("kind") != "base":
            if current.id in seen:
                report.add("unit-conversion-cycle", f"{unit.id!r} converts in a cycle")
                break
            seen.add(current.id)
            following = current.definition.get("base_unit")
            if following not in units:
                break
            current = units[following]


def _normalize_alias(form: str, *, case_sensitive: bool) -> str:
    normalized = unicodedata.normalize("NFC", form).strip()
    return normalized if case_sensitive else normalized.casefold()

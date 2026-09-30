"""Quantity expressions and temperatures (specification sections 7, 8 and 11).

The quantity grammar is deliberately shallow: an alternative option may not
itself be an alternative or opaque, and a sum term is always simple.

Temperatures live outside that grammar. A temperature unit in an ingredient,
equipment, yield or package-size quantity is rejected rather than converted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import rational

if TYPE_CHECKING:
    from .errors import Report
    from .registry import Registry

TEMPERATURE_PURPOSES = {"oven", "internal", "oil", "water", "surface", "other"}


def check_amount(
    amount: Any,
    report: Report,
    path: str,
    *,
    signed: bool = False,
) -> None:
    """Check one amount's canonical form and range ordering."""
    if not isinstance(amount, dict):
        return
    for member in ("value", "min", "max"):
        if member not in amount:
            continue
        try:
            rational.parse(amount[member], signed=signed)
        except rational.NotCanonicalError as exc:
            report.add(exc.code, str(exc), f"{path}.{member}")

    # `min <= max` is beyond JSON Schema, and a reversed range is not a
    # cosmetic error: it silently inverts what the recipe says.
    if "min" in amount and "max" in amount:
        try:
            low = rational.parse(amount["min"], signed=signed)
            high = rational.parse(amount["max"], signed=signed)
        except rational.NotCanonicalError:
            return
        if low > high:
            report.add("range-order", "a range minimum exceeds its maximum", path)


def check_quantity(
    quantity: Any,
    report: Report,
    path: str,
    registry: Registry | None,
    *,
    newer_registry: bool,
    depth: int = 0,
) -> None:
    """Check a quantity expression and every unit it names."""
    if not isinstance(quantity, dict):
        return
    kind = quantity.get("kind")

    if kind == "opaque":
        return

    if kind == "simple":
        check_amount(quantity.get("amount"), report, f"{path}.amount")
        _check_unit(
            quantity.get("unit"),
            report,
            f"{path}.unit",
            registry,
            newer_registry=newer_registry,
            temperature=False,
        )
        return

    if kind == "sum":
        for index, term in enumerate(quantity.get("terms") or []):
            term_path = f"{path}.terms[{index}]"
            if isinstance(term, dict) and term.get("kind") in {"alternatives", "opaque"}:
                report.add(
                    "alternative-inside-sum",
                    "a sum term is a simple quantity, so the grammar stays shallow",
                    term_path,
                )
                continue
            check_quantity(
                term,
                report,
                term_path,
                registry,
                newer_registry=newer_registry,
                depth=depth + 1,
            )
        return

    if kind == "alternatives":
        for index, option in enumerate(quantity.get("options") or []):
            option_path = f"{path}.options[{index}]"
            option_kind = option.get("kind") if isinstance(option, dict) else None
            if option_kind in {"alternatives", "opaque"}:
                report.add(
                    "nested-quantity-alternative",
                    "an alternative option is a simple or sum quantity, never another alternative",
                    option_path,
                )
                continue
            check_quantity(
                option,
                report,
                option_path,
                registry,
                newer_registry=newer_registry,
                depth=depth + 1,
            )


def check_package_size(
    package_size: Any,
    report: Report,
    path: str,
    registry: Registry | None,
    *,
    newer_registry: bool,
) -> None:
    """A package size describes each counted container, so it is physical.

    Two 14-ounce cans are `container.can` for the quantity and 14 ounces of
    mass here. A count unit would be circular and a temperature unit is
    meaningless.
    """
    if not isinstance(package_size, dict):
        return
    check_amount(package_size.get("amount"), report, f"{path}.amount")
    unit_id = package_size.get("unit")
    if not isinstance(unit_id, str) or registry is None:
        return
    unit = registry.get(unit_id)
    if unit is None:
        if not newer_registry:
            report.add("unknown-unit", f"unknown unit {unit_id!r}", f"{path}.unit")
        return
    if unit.dimension == "temperature":
        report.add(
            "temperature-package-size",
            "a package size is not a temperature",
            f"{path}.unit",
        )
    elif not unit.physical:
        report.add(
            "nonphysical-package-size",
            "a package size uses a registered mass, volume or length unit",
            f"{path}.unit",
        )


def check_temperature(
    temperature: Any,
    report: Report,
    path: str,
    registry: Registry | None,
    *,
    newer_registry: bool,
) -> None:
    """Check an authored temperature and its optional purpose."""
    if not isinstance(temperature, dict):
        return
    if "text" in temperature:
        if "amount" in temperature or "unit" in temperature:
            report.add(
                "opaque-temperature-conflict",
                "opaque temperature text has no numeric amount or unit",
                path,
            )
        return
    check_amount(temperature.get("amount"), report, f"{path}.amount", signed=True)

    unit_id = temperature.get("unit")
    if isinstance(unit_id, str) and registry is not None:
        unit = registry.get(unit_id)
        if unit is None:
            if not newer_registry:
                report.add("unknown-unit", f"unknown unit {unit_id!r}", f"{path}.unit")
        elif unit.dimension != "temperature":
            report.add(
                "invalid-temperature-unit",
                f"{unit_id!r} does not measure temperature",
                f"{path}.unit",
            )

    purpose = temperature.get("purpose")
    if purpose is None:
        return
    if not isinstance(purpose, dict) or "kind" not in purpose:
        report.add(
            "temperature-purpose-kind-required",
            "a purpose states its kind",
            f"{path}.purpose",
        )
        return
    kind = purpose.get("kind")
    if kind not in TEMPERATURE_PURPOSES:
        report.add(
            "temperature-purpose-unknown-kind",
            f"{kind!r} is not a standardized temperature purpose",
            f"{path}.purpose.kind",
        )
    elif kind == "other" and not (purpose.get("label") or "").strip():
        report.add(
            "temperature-purpose-other-label-required",
            "`other` carries the source's own wording as a label",
            f"{path}.purpose",
        )


def _check_unit(
    unit_id: Any,
    report: Report,
    path: str,
    registry: Registry | None,
    *,
    newer_registry: bool,
    temperature: bool,
) -> None:
    if unit_id is None or registry is None:
        return
    if not isinstance(unit_id, str):
        return
    unit = registry.get(unit_id)
    if unit is None:
        # A syntactically valid ID from a compatible newer registry is
        # preserved rather than rejected (section 5). Under the exact registry
        # the document names, an unknown ID is an error.
        if not newer_registry:
            report.add("unknown-unit", f"unknown unit {unit_id!r}", path)
        return
    if not temperature and unit.dimension == "temperature":
        report.add(
            "temperature-as-quantity",
            "a temperature unit belongs in a temperature field, not a quantity",
            path,
        )

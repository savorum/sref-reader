"""Reading and validating a SREF recipe document.

Structural rules come from the published JSON Schema; the rules the schema
cannot express come from here. The specification is explicit that both are
required (section 1), and section 23 lists what a semantic validator must
check on top of the schema.

Failures are collected rather than raised, and every check reports a code from
the conformance manifest.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import jsonio, rational, snapshot, versions
from . import schema as schema_module
from .errors import Report, SrefError, UnsupportedVersionError
from .language import well_formed_bcp47
from .limits import DEFAULT
from .quantity import check_amount, check_package_size, check_quantity, check_temperature
from .registry import Registry
from .registry import load as load_registry

#: Section 6.1. Deliberately permissive about leading digits so a UUID needs no
#: rewriting, and deliberately strict about everything that could become a path
#: separator or a dot segment.
PORTABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

#: Section 13. Three labels minimum: at least two for a reversed domain, then
#: at least one the publisher chooses.
EXTENSION_NAME = re.compile(
    r"^x-[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?){2,}$",
)

TIMING_KINDS = {
    "prep",
    "soak",
    "brine",
    "marinate",
    "cure",
    "proof",
    "ferment",
    "chill",
    "freeze",
    "drain",
    "cook",
    "rest",
    "cool",
    "other",
}
AUTHOR_KINDS = {"person", "organization", "other"}
ASSET_ROLES = {"image", "source", "other"}
NORMALIZATION_METHODS = {"authored", "extracted", "inferred", "unresolved"}
#: Legacy asset roles refused by the current specification in favor of `image_refs`.
LEGACY_ASSET_ROLES = {
    "primary-image": "legacy-primary-image-role",
    "step-image": "legacy-step-image-role",
}

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")
_DURATION = re.compile(
    r"^P(?!$)(\d+Y)?(\d+M)?(\d+W)?(\d+D)?(T(?!$)(\d+H)?(\d+M)?(\d+(\.\d+)?S)?)?$",
)


def read(data: bytes | str) -> dict[str, Any]:
    """Read one recipe document, raising if it is not acceptable."""
    document, report = validate(data)
    if not report.valid:
        if any(code.startswith("unsupported-") for code in report.codes):
            raise UnsupportedVersionError(report.violations)
        raise SrefError(report.violations)
    return document


def validate(
    data: bytes | str | dict[str, Any],
    *,
    max_depth: int = DEFAULT.json_depth,
) -> tuple[dict[str, Any] | None, Report]:
    """Validate a recipe document and return it alongside every failure found."""
    report = Report()
    if isinstance(data, (bytes, str)):
        try:
            document = jsonio.loads(data, max_depth=max_depth)
        except jsonio.JSONRejectedError as exc:
            report.add(exc.code, str(exc))
            return None, report
    else:
        document = data
        if jsonio.value_depth(document, max_depth) > max_depth:
            report.add("json-depth-limit", jsonio.depth_message(max_depth))
            return None, report

    if not isinstance(document, dict):
        report.add("malformed-package-json", "a recipe document is a JSON object")
        return None, report

    jsonio.check_control_characters(document, report)

    header = document.get("sref")
    if not isinstance(header, dict) or "version" not in header:
        report.add("missing-format-version", "every recipe declares its SREF version", "sref")
        return document, report

    format_compatibility = versions.compare(header.get("version"), versions.SUPPORTED_FORMAT)
    registry_compatibility = versions.compare(
        header.get("unit_registry"),
        versions.SUPPORTED_REGISTRY,
    )
    if not format_compatibility.supported:
        report.add(
            "unsupported-format-version",
            f"this build implements SREF {versions.SUPPORTED_FORMAT}, not {header.get('version')!r}",
            "sref.version",
        )
    if not registry_compatibility.supported:
        report.add(
            "unsupported-registry-version",
            f"this build implements registry {versions.SUPPORTED_REGISTRY},"
            f" not {header.get('unit_registry')!r}",
            "sref.unit_registry",
        )
    if report.violations:
        # Reading further would mean interpreting a document under a version it
        # does not claim, which section 5 forbids.
        return document, report

    registry = _registry(report)
    context = _Context(
        report=report,
        registry=registry,
        newer_registry=registry_compatibility.newer,
        newer_format=format_compatibility.newer,
        format_version=header.get("version"),
    )
    context.recipe_id = document.get("id")
    # The prose checks run first so that a specific requirement is the first
    # violation reported. The schema is a second gate for structural problems
    # no rule here covers, and its findings belong after them.
    _check_document(document, context)
    schema_module.check(document, "recipe", report)
    if not format_compatibility.newer:
        # Only a document declaring a newer version may carry members this
        # build does not define. One declaring the version this build
        # implements has made no such claim (section 13).
        # A singular `yield` is valid in 0.2 and reported by its own
        # requirement in later versions.
        schema_module.check_version_scoped_members(
            document,
            report,
            allowed=frozenset({"yield"}),
        )
    return document, report


class _Context:
    def __init__(
        self,
        report: Report,
        registry: Registry | None,
        *,
        newer_registry: bool,
        newer_format: bool,
        format_version: object,
    ) -> None:
        self.report = report
        self.registry = registry
        self.newer_registry = newer_registry
        self.newer_format = newer_format
        self.format_version = versions.parse(format_version)
        self.assets: dict[str, dict[str, Any]] = {}
        self.ingredient_ids: set[str] = set()
        self.choice_ids: set[str] = set()
        self.timing_ids: set[str] = set()
        self.recipe_id: Any = None


def _registry(report: Report) -> Registry | None:
    registry_report = Report()
    registry = load_registry(snapshot.units(), registry_report)
    if registry is None:
        report.add("malformed-registry", "the pinned unit registry did not load")
    return registry


def _check_document(document: dict[str, Any], context: _Context) -> None:
    report = context.report

    collections = ("ingredient_sections", "instruction_sections")
    if not any(member in document for member in collections):
        report.add("recipe-content-required", "a recipe needs ingredients or instructions", "")
    for member in collections:
        if member in document and (not isinstance(document[member], list) or not document[member]):
            report.add(
                "recipe-collection-nonempty",
                "a present collection must be a nonempty array",
                member,
            )
    _check_id(document.get("id"), report, "id")
    for member in ("language", "source_locale"):
        value = document.get(member)
        if value is not None and not well_formed_bcp47(value):
            report.add("invalid-language-tag", f"{value!r} is not a BCP 47 tag", member)

    _check_extension_names(document, report, "")
    _check_core_not_faked_by_extension(document, report)
    _check_authors(document.get("authors"), report)
    _check_source(document.get("source"), report)
    _check_chronology(document, report)
    _check_string_sets(document, report)
    _check_variants(document.get("variants"), report)
    _check_equipment(document.get("equipment"), context)
    _check_translations(document, context)
    _check_members_added_in_0_3(document, context)
    _check_claims(document, context)
    _check_nutrition(document.get("nutrition"), context)
    _check_assets(document.get("assets"), context)
    _check_yield(document.get("yield"), context, "yield")
    for index, assertion in enumerate(document.get("yields") or []):
        _check_yield(assertion, context, f"yields[{index}]")
    _check_times(document.get("times"), context)
    _check_ingredients(document.get("ingredient_sections"), context)
    _check_instructions(document.get("instruction_sections"), context)
    _check_recipe_images(document, context)


def _check_id(value: Any, report: Report, path: str) -> None:
    if isinstance(value, str) and not PORTABLE_ID.match(value):
        report.add("invalid-portable-id", f"{value!r} is not a portable identifier", path)


def _check_extension_names(node: Any, report: Report, path: str) -> None:
    """Extension names are checked wherever an object may carry one.

    The name rule applies to members of SREF-defined objects, not to whatever
    is inside an extension's own value, so the walk stops at an extension.
    """
    if isinstance(node, dict):
        for name, value in node.items():
            member_path = f"{path}.{name}" if path else name
            if name.startswith("x-"):
                if not EXTENSION_NAME.match(name):
                    report.add(
                        "invalid-extension-name",
                        f"{name!r} is not `x-<reversed-domain>.<local-name>`",
                        member_path,
                    )
                continue
            if name == "extensions":
                for extension_name in value if isinstance(value, dict) else {}:
                    if not EXTENSION_NAME.match(extension_name):
                        report.add(
                            "invalid-extension-name",
                            f"{extension_name!r} is not a valid extension name",
                            f"{member_path}.{extension_name}",
                        )
                continue
            _check_extension_names(value, report, member_path)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _check_extension_names(item, report, f"{path}[{index}]")


def _check_core_not_faked_by_extension(document: dict[str, Any], report: Report) -> None:
    """An extension may not stand in for a required standard member.

    Section 13: an unknown member MUST NOT satisfy a required standard field.
    A document with `x-org.example.title` and no `title` is missing a title.
    """
    for required in ("title", "id"):
        if required in document:
            continue
        if any(name.startswith("x-") and name.endswith(f".{required}") for name in document):
            report.add(
                "extension-cannot-satisfy-core",
                f"an extension cannot supply the required {required!r}",
                required,
            )


def _check_authors(authors: Any, report: Report) -> None:
    if not isinstance(authors, list):
        return
    for index, author in enumerate(authors):
        path = f"authors[{index}]"
        if not isinstance(author, dict):
            # A bare string cannot say whether it names a person or an
            # organization (section 6.4).
            report.add("legacy-string-author", "an author is an object with a name", path)
            continue
        if not (author.get("name") or "").strip():
            report.add("author-name-required", "an author credit needs a name", path)
        kind = author.get("kind")
        if kind is not None and kind not in AUTHOR_KINDS:
            report.add("unknown-author-kind", f"{kind!r} is not an author kind", f"{path}.kind")
        if author.get("kind_label") is not None and kind != "other":
            report.add(
                "author-kind-label-requires-other",
                "a kind label describes an `other` kind",
                f"{path}.kind_label",
            )
        url = author.get("url")
        if isinstance(url, str) and not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", url):
            report.add("absolute-author-url-required", "an author URL is absolute", f"{path}.url")


def _check_source(source: Any, report: Report) -> None:
    if not isinstance(source, dict):
        return
    identity = any(
        (source.get(member) or "").strip()
        for member in ("name", "url", "application", "attribution")
        if isinstance(source.get(member), str)
    )
    if not identity:
        report.add(
            "source-identity-required",
            "a source names itself through name, url, application or attribution",
            "source",
        )
    if source.get("source_id") and not (source.get("application") or "").strip():
        report.add(
            "source-application-required",
            "a source-local identifier needs the application that issued it",
            "source.source_id",
        )


def _check_chronology(document: dict[str, Any], report: Report) -> None:
    """Chronology fields are checked for shape, then for calendar reality.

    Prose (`March 10, 2024`) and an impossible day (`2024-02-30`) are separate
    failures. Ordering of `published` and `modified` is not checked, as
    section 6.7 does not require it.
    """
    for member in ("published", "modified"):
        value = document.get(member)
        if value is None:
            continue
        if not isinstance(value, str) or not (_DATE.match(value) or _DATE_TIME.match(value)):
            report.add(
                "invalid-publication-chronology",
                f"{member} is an RFC 3339 full date or date-time, not localized prose",
                member,
            )
            continue
        if not _plausible_date(value[:10]):
            report.add("invalid-publication-date", f"{value!r} is not a real calendar date", member)


def _plausible_date(text: str) -> bool:
    import datetime

    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _check_string_sets(document: dict[str, Any], report: Report) -> None:
    for member in ("categories", "courses", "keywords", "cuisines", "cooking_methods"):
        values = document.get(member)
        if not isinstance(values, list):
            continue
        seen: set[str] = set()
        for index, value in enumerate(values):
            if not isinstance(value, str):
                continue
            if value in seen:
                report.add(
                    "duplicate-authored-category",
                    f"{value!r} is repeated",
                    f"{member}[{index}]",
                )
            seen.add(value)


def _check_variants(variants: Any, report: Report) -> None:
    if not isinstance(variants, list):
        return
    for index, variant in enumerate(variants):
        path = f"variants[{index}]"
        if not isinstance(variant, dict):
            continue
        if "variants" in variant:
            report.add("variant-nesting", "variants do not nest or compose", path)
        # A variant describes a modification. Content that restructures the
        # recipe is a recipe, whatever the prose calls it.
        for member in (
            "ingredient_sections",
            "instruction_sections",
            "yield",
            "yields",
            "nutrition",
            "times",
        ):
            if member in variant:
                report.add(
                    "variant-restructures-recipe",
                    f"a variant does not restructure the recipe through {member!r}",
                    f"{path}.{member}",
                )


def _check_assets(assets: Any, context: _Context) -> None:
    report = context.report
    if not isinstance(assets, list):
        return
    paths: set[str] = set()
    for index, asset in enumerate(assets):
        path = f"assets[{index}]"
        if not isinstance(asset, dict):
            continue
        asset_id = asset.get("id")
        if isinstance(asset_id, str):
            if asset_id in context.assets:
                report.add("duplicate-asset-id", f"asset {asset_id!r} is declared twice", path)
            else:
                context.assets[asset_id] = asset
            _check_id(asset_id, report, f"{path}.id")

        declared_path = asset.get("path")
        if isinstance(declared_path, str):
            if declared_path in paths:
                report.add(
                    "duplicate-asset-path",
                    f"{declared_path!r} is declared twice",
                    f"{path}.path",
                )
            paths.add(declared_path)
            if not _safe_asset_path(declared_path):
                report.add(
                    "unsafe-asset-path",
                    f"{declared_path!r} is not a safe asset path",
                    f"{path}.path",
                )

        role = asset.get("role")
        if role in LEGACY_ASSET_ROLES:
            report.add(
                LEGACY_ASSET_ROLES[role],
                f"{role!r} asserted a relationship; use `image_refs` and keep `role` a classification",
                f"{path}.role",
            )
        elif role is not None and role not in ASSET_ROLES:
            report.add("unknown-asset-role", f"{role!r} is not an asset role", f"{path}.role")

        license_ = asset.get("license")
        if isinstance(license_, dict) and not ("name" in license_ or "url" in license_):
            report.add(
                "asset-license-identity-required",
                "an asset license has a name, a URL, or both",
                f"{path}.license",
            )
        source_url = asset.get("source_url")
        if isinstance(source_url, str) and not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", source_url):
            report.add(
                "asset-relative-source-url",
                "an asset source URL is absolute",
                f"{path}.source_url",
            )


def _safe_asset_path(value: str) -> bool:
    if not value.startswith("assets/") or value.startswith("/"):
        return False
    if "\\" in value or "\x00" in value or re.match(r"^[A-Za-z]:", value):
        return False
    segments = value.split("/")
    return all(segment not in ("", ".", "..") for segment in segments[1:]) and len(segments) > 1


def _check_yield(value: Any, context: _Context, path: str) -> None:
    if not isinstance(value, dict):
        return
    quantity = value.get("quantity")
    if quantity is None:
        return
    _check_bounded_quantity(quantity, context, f"{path}.quantity", "temperature-yield", "a yield")


def _check_bounded_quantity(
    quantity: Any, context: _Context, path: str, code: str, subject: str
) -> None:
    before = len(context.report.violations)
    check_quantity(
        quantity,
        context.report,
        path,
        context.registry,
        newer_registry=context.newer_registry,
    )
    # A temperature here is a different mistake from one in an ingredient
    # amount, and the corpus distinguishes them, so the generic code is
    # rewritten for this slot.
    for violation in context.report.violations[before:]:
        if violation.code == "temperature-as-quantity":
            context.report.violations.remove(violation)
            context.report.add(code, f"{subject} is not a temperature", path)


#: Members SREF 0.4.0 added, by the object that carries them.
_ADDED_IN_0_3 = ("yields", "nutrition", "dietary_claims", "allergen_declarations")
_ASSET_PROVENANCE = ("creator", "credit", "rights", "license", "source_url")


def _check_members_added_in_0_3(document: dict[str, Any], context: _Context) -> None:
    """A 0.2 document carries a singular `yield`; a later one carries `yields`."""
    report = context.report
    if context.format_version is None:
        return
    if context.format_version >= (0, 4, 0):
        if document.get("yields") == []:
            report.add("empty-yields", "a yields array is nonempty", "yields")
        if "yield" in document and not context.newer_format:
            report.add(
                "singular-yield-member",
                "SREF 0.4.0 records yields as an array; `yield` is not defined",
                "yield",
            )
        return
    added = [member for member in _ADDED_IN_0_3 if member in document]
    for index, variant in enumerate(document.get("variants") or []):
        if isinstance(variant, dict) and "id" in variant:
            added.append(f"variants[{index}].id")
    for index, asset in enumerate(document.get("assets") or []):
        if isinstance(asset, dict):
            added += [f"assets[{index}].{m}" for m in _ASSET_PROVENANCE if m in asset]
    for section in document.get("ingredient_sections") or []:
        for ingredient in _ingredients_of(section):
            if "recipe" in ingredient:
                added.append(f"ingredient {ingredient.get('id')}.recipe")
    for path in added:
        report.add(
            "member-not-defined-by-version",
            f"{path} was added in SREF 0.4.0",
            path,
        )


def _ingredients_of(section: Any) -> list[dict[str, Any]]:
    if not isinstance(section, dict):
        return []
    found: list[dict[str, Any]] = []
    for ingredient in section.get("ingredients") or []:
        if isinstance(ingredient, dict):
            found.append(ingredient)
            found += [b for b in ingredient.get("alternatives") or [] if isinstance(b, dict)]
    return found


DIETS = {
    "diabetic",
    "gluten_free",
    "halal",
    "hindu",
    "kosher",
    "low_calorie",
    "low_fat",
    "low_lactose",
    "low_salt",
    "vegan",
    "vegetarian",
}
PRESENCES = {"contains", "may_contain", "free_from"}


def _check_claims(document: dict[str, Any], context: _Context) -> None:
    """Section 6.11: authored claims, scoped to a variant by its ID."""
    report = context.report
    variant_ids: set[str] = set()
    for index, variant in enumerate(document.get("variants") or []):
        if not isinstance(variant, dict) or "id" not in variant:
            continue
        variant_id = variant["id"]
        _check_id(variant_id, report, f"variants[{index}].id")
        if variant_id in variant_ids:
            report.add(
                "duplicate-variant-id",
                f"variant {variant_id!r} is declared twice",
                f"variants[{index}].id",
            )
        variant_ids.add(variant_id)

    for index, claim in enumerate(document.get("dietary_claims") or []):
        if not isinstance(claim, dict):
            continue
        path = f"dietary_claims[{index}]"
        if "suitability" not in claim:
            report.add(
                "dietary-claim-suitability-required",
                "a dietary claim states suitable or unsuitable",
                path,
            )
        diet = claim.get("diet")
        if diet is not None and diet not in DIETS:
            report.add(
                "dietary-claim-unknown-diet",
                f"{diet!r} is not a normalized diet; keep it in the claim text",
                f"{path}.diet",
            )
        _check_variant_ref(claim, variant_ids, report, path)

    for index, declaration in enumerate(document.get("allergen_declarations") or []):
        if not isinstance(declaration, dict):
            continue
        path = f"allergen_declarations[{index}]"
        presence = declaration.get("presence")
        if presence is None:
            report.add(
                "allergen-presence-required",
                "an allergen declaration states contains, may_contain, or free_from",
                path,
            )
        elif presence not in PRESENCES:
            report.add(
                "allergen-unknown-presence",
                f"{presence!r} is not contains, may_contain, or free_from",
                f"{path}.presence",
            )
        if not (declaration.get("substance") or "").strip():
            report.add(
                "allergen-substance-required",
                "an allergen declaration names its substance",
                path,
            )
        _check_variant_ref(declaration, variant_ids, report, path)


def _check_variant_ref(
    claim: dict[str, Any], variant_ids: set[str], report: Report, path: str
) -> None:
    reference = claim.get("variant_ref")
    if reference is not None and reference not in variant_ids:
        report.add(
            "dangling-claim-variant-reference",
            f"variant {reference!r} does not exist",
            f"{path}.variant_ref",
        )


NUTRIENTS = {
    "energy",
    "fat",
    "saturated_fat",
    "trans_fat",
    "unsaturated_fat",
    "cholesterol",
    "carbohydrate",
    "sugars",
    "fiber",
    "protein",
    "sodium",
    "salt",
    "other",
}
BASIS_KINDS = {"recipe", "serving", "quantity", "unspecified"}
ENERGY_UNITS = {"kcal", "kJ"}
MASS_UNITS = {"g", "mg", "mcg"}


def _check_nutrition(value: Any, context: _Context) -> None:
    """Section 6.10: authored nutrition statements and their bases."""
    if not isinstance(value, list):
        return
    report = context.report
    for index, statement in enumerate(value):
        if not isinstance(statement, dict):
            continue
        path = f"nutrition[{index}]"
        basis = statement.get("basis")
        if isinstance(basis, dict):
            kind = basis.get("kind")
            if kind not in BASIS_KINDS:
                report.add(
                    "nutrition-unknown-basis",
                    f"{kind!r} is not recipe, serving, quantity, or unspecified",
                    f"{path}.basis.kind",
                )
            if kind == "quantity" and "quantity" not in basis:
                report.add(
                    "nutrition-quantity-basis-without-quantity",
                    "a quantity basis states its quantity",
                    f"{path}.basis",
                )
            if kind == "unspecified" and "quantity" in basis:
                report.add(
                    "nutrition-unspecified-basis-with-quantity",
                    "an unspecified basis carries no quantity",
                    f"{path}.basis.quantity",
                )
            if "quantity" in basis:
                _check_bounded_quantity(
                    basis["quantity"],
                    context,
                    f"{path}.basis.quantity",
                    "temperature-nutrition-basis",
                    "a nutrition basis",
                )
        nutrients = statement.get("nutrients")
        if isinstance(nutrients, list) and not nutrients:
            report.add(
                "nutrition-empty-nutrients", "a statement lists a nutrient", f"{path}.nutrients"
            )
        seen: set[tuple[Any, Any]] = set()
        for nutrient_index, nutrient in enumerate(nutrients or []):
            if isinstance(nutrient, dict):
                _check_nutrient(nutrient, report, f"{path}.nutrients[{nutrient_index}]", seen)


def _check_nutrient(
    nutrient: dict[str, Any], report: Report, path: str, seen: set[tuple[Any, Any]]
) -> None:
    name = nutrient.get("nutrient")
    if name == "other" and not nutrient.get("label"):
        report.add("nutrition-other-without-label", "an `other` nutrient carries a label", path)
    identity = (name, nutrient.get("label", ""))
    if identity in seen:
        report.add("nutrition-duplicate-nutrient", f"{name!r} is listed twice", path)
    seen.add(identity)
    if "text" in nutrient and any(m in nutrient for m in ("amount", "unit", "source_text")):
        report.add(
            "nutrition-opaque-value-with-amount",
            "an opaque nutrient value carries no amount or unit",
            path,
        )
    unit = nutrient.get("unit")
    if unit is not None:
        if name == "energy" and unit not in ENERGY_UNITS:
            report.add(
                "nutrition-energy-mass-unit", f"energy is not measured in {unit!r}", f"{path}.unit"
            )
        elif name != "energy" and unit not in MASS_UNITS:
            report.add(
                "nutrition-mass-energy-unit",
                f"{name!r} is not measured in {unit!r}",
                f"{path}.unit",
            )
    check_amount(nutrient.get("amount"), report, f"{path}.amount")


TRANSLATION_RELATIONS = {"original", "translation", "alternate"}


def _check_translations(document: dict[str, Any], context: _Context) -> None:
    """Section 6.9: declared relationships between renditions of one recipe."""
    translations = document.get("translations")
    if not isinstance(translations, list):
        return
    report = context.report
    if context.format_version is not None and context.format_version < (0, 4, 0):
        report.add(
            "member-not-defined-by-version",
            "translation relationships were added in SREF 0.4.0",
            "translations",
        )
    if translations and "language" not in document:
        report.add(
            "translation-requires-document-language",
            "a recipe that declares translations states its own language",
            "language",
        )
    originals = 0
    for index, translation in enumerate(translations):
        if not isinstance(translation, dict):
            continue
        path = f"translations[{index}]"
        relation = translation.get("relation")
        if relation not in TRANSLATION_RELATIONS:
            report.add(
                "translation-unknown-relation",
                f"{relation!r} is not original, translation, or alternate",
                f"{path}.relation",
            )
        elif relation == "original":
            originals += 1
        language = translation.get("language")
        if isinstance(language, str) and not well_formed_bcp47(language):
            report.add(
                "translation-invalid-language",
                f"{language!r} is not a BCP 47 tag",
                f"{path}.language",
            )
        if not any(member in translation for member in ("title", "recipe_id", "url")):
            report.add(
                "translation-counterpart-required",
                "a relationship identifies its counterpart by title, recipe ID, or URL",
                path,
            )
        recipe_id = translation.get("recipe_id")
        _check_id(recipe_id, report, f"{path}.recipe_id")
        if recipe_id is not None and recipe_id == document.get("id"):
            report.add(
                "translation-self-reference",
                "a relationship names another recipe, not this one",
                f"{path}.recipe_id",
            )
    if originals > 1:
        report.add(
            "translation-single-original",
            "a recipe declares at most one original",
            "translations",
        )


def _check_equipment(value: Any, context: _Context) -> None:
    if not isinstance(value, list):
        return
    for index, equipment in enumerate(value):
        if not isinstance(equipment, dict):
            continue
        path = f"equipment[{index}]"
        if not str(equipment.get("name", "")).strip():
            context.report.add(
                "equipment-name-required",
                "an equipment requirement preserves its authored name",
                f"{path}.name",
            )
        if "quantity" not in equipment:
            continue
        before = len(context.report.violations)
        check_quantity(
            equipment["quantity"],
            context.report,
            f"{path}.quantity",
            context.registry,
            newer_registry=context.newer_registry,
        )
        for violation in context.report.violations[before:]:
            if violation.code == "temperature-as-quantity":
                context.report.violations.remove(violation)
                context.report.add(
                    "temperature-equipment",
                    "a temperature is not an equipment quantity",
                    f"{path}.quantity",
                )


def _check_times(times: Any, context: _Context) -> None:
    report = context.report
    if not isinstance(times, dict):
        return
    summaries = [member for member in ("prep", "cook", "additional", "total") if member in times]
    assertions = times.get("assertions")
    if not summaries and not isinstance(assertions, list):
        report.add(
            "timing-summary-expression-required",
            "`times` carries a summary duration or a timing assertion",
            "times",
        )
    for member in summaries:
        _check_duration(times[member], report, f"times.{member}")

    if not isinstance(assertions, list):
        return
    if not summaries and not assertions:
        report.add("timing-summary-expression-required", "`times` says nothing", "times")
    for index, assertion in enumerate(assertions):
        path = f"times.assertions[{index}]"
        if not isinstance(assertion, dict):
            continue
        assertion_id = assertion.get("id")
        if not isinstance(assertion_id, str) or not assertion_id:
            report.add("timing-id-required", "a timing assertion has a recipe-local id", path)
        elif assertion_id in context.timing_ids:
            report.add("duplicate-timing-id", f"timing {assertion_id!r} is declared twice", path)
        else:
            context.timing_ids.add(assertion_id)

        if "duration" not in assertion:
            report.add("timing-without-duration", "a timing assertion states a duration", path)
        else:
            _check_duration(assertion["duration"], report, f"{path}.duration")

        microwave = assertion.get("microwave")
        if isinstance(microwave, dict):
            if context.format_version is not None and context.format_version < (0, 4, 0):
                report.add(
                    "member-not-defined-by-version",
                    "microwave conditions were added in SREF 0.4.0",
                    f"{path}.microwave",
                )
            _check_microwave(
                microwave,
                assertion.get("duration"),
                report,
                f"{path}.microwave",
            )

        kind = assertion.get("kind")
        if kind is not None and kind not in TIMING_KINDS:
            report.add("timing-unknown-kind", f"{kind!r} is not a timing kind", f"{path}.kind")
        elif kind == "other" and not (assertion.get("label") or "").strip():
            report.add(
                "timing-other-without-label",
                "`other` is not presentation text, so it carries a label",
                path,
            )
        attention = assertion.get("attention")
        if attention is not None and attention not in {"active", "passive"}:
            report.add(
                "timing-invalid-attention",
                f"{attention!r} is not an attention value",
                f"{path}.attention",
            )


def _check_microwave(microwave: dict[str, Any], duration: Any, report: Report, path: str) -> None:
    def check_power(power: Any, power_path: str) -> None:
        if not isinstance(power, dict):
            return
        if sum(member in power for member in ("watts", "percent", "text")) > 1:
            report.add(
                "microwave-power-conflict",
                "selected watts, percentage, and opaque wording are exclusive",
                power_path,
            )
        if "percent" not in power:
            return
        try:
            value = rational.parse(power["percent"])
        except rational.NotCanonicalError:
            return
        if value <= 0 or value > 100:
            report.add(
                "microwave-percent-out-of-range",
                "power percent must be greater than 0 and at most 100",
                f"{power_path}.percent",
            )

    check_power(microwave.get("power"), f"{path}.power")
    choices = microwave.get("choices")
    if not isinstance(choices, list):
        return
    if not isinstance(duration, dict) or "text" not in duration:
        report.add(
            "microwave-choice-duration-must-be-opaque",
            "an alternative schedule retains its complete opaque duration",
            path,
        )
    if "rated_output_watts" in microwave or "power" in microwave:
        report.add(
            "microwave-choice-shared-power-conflict",
            "a choice schedule must not also carry shared rated output or power",
            path,
        )
    seen: set[str] = set()
    for index, choice in enumerate(choices):
        if not isinstance(choice, dict):
            continue
        check_power(choice.get("power"), f"{path}.choices[{index}].power")
        condition = json.dumps(
            {key: choice[key] for key in ("rated_output_watts", "power") if key in choice},
            sort_keys=True,
            separators=(",", ":"),
        )
        if condition in seen:
            report.add(
                "microwave-choice-duplicate-conditions",
                "microwave choice conditions must be distinct",
                f"{path}.choices[{index}]",
            )
        seen.add(condition)


def _check_duration(duration: Any, report: Report, path: str) -> None:
    if not isinstance(duration, dict):
        report.add("timing-summary-expression-required", "a duration is an expression object", path)
        return
    numeric = [member for member in ("value", "min", "max") if member in duration]
    opaque = "text" in duration
    if opaque and (numeric or "approximate" in duration):
        report.add(
            "timing-duration-conflict",
            "opaque wording has no numeric reading, so it carries neither",
            path,
        )
    if not opaque and not numeric:
        report.add("timing-without-duration", "a duration expression says nothing", path)
    for member in numeric:
        if not isinstance(duration[member], str) or not _DURATION.match(duration[member]):
            report.add(
                "invalid-duration",
                f"{duration[member]!r} is not an ISO 8601 duration",
                f"{path}.{member}",
            )


def _check_ingredients(sections: Any, context: _Context) -> None:
    report = context.report
    if not isinstance(sections, list):
        return
    section_ids: set[str] = set()
    for section_index, section in enumerate(sections):
        path = f"ingredient_sections[{section_index}]"
        if not isinstance(section, dict):
            continue
        section_id = section.get("id")
        if isinstance(section_id, str):
            if section_id in section_ids:
                report.add(
                    "duplicate-section-id",
                    f"section {section_id!r} is declared twice",
                    path,
                )
            section_ids.add(section_id)
            _check_id(section_id, report, f"{path}.id")

        for index, ingredient in enumerate(section.get("ingredients") or []):
            _check_ingredient(ingredient, context, f"{path}.ingredients[{index}]")


def _check_ingredient(ingredient: Any, context: _Context, path: str) -> None:
    report = context.report
    if not isinstance(ingredient, dict):
        return
    ingredient_id = ingredient.get("id")
    if isinstance(ingredient_id, str):
        # Ingredient IDs are unique across every section, not merely within
        # one, because a step's `ingredient_ref` addresses the whole recipe.
        if ingredient_id in context.ingredient_ids:
            report.add(
                "duplicate-ingredient-id",
                f"ingredient {ingredient_id!r} is declared twice",
                path,
            )
        context.ingredient_ids.add(ingredient_id)
        _check_id(ingredient_id, report, f"{path}.id")

    alternatives = ingredient.get("alternatives")
    if isinstance(alternatives, list):
        if isinstance(ingredient_id, str):
            context.choice_ids.add(ingredient_id)
        for index, branch in enumerate(alternatives):
            if isinstance(branch, dict) and "alternatives" not in branch:
                _check_ingredient(branch, context, f"{path}.alternatives[{index}]")

    if "optional" in ingredient and not isinstance(ingredient["optional"], bool):
        report.add("optional-must-be-boolean", "`optional` is a boolean", f"{path}.optional")

    if "quantity" in ingredient:
        check_quantity(
            ingredient["quantity"],
            report,
            f"{path}.quantity",
            context.registry,
            newer_registry=context.newer_registry,
        )
    if "package_size" in ingredient:
        check_package_size(
            ingredient["package_size"],
            report,
            f"{path}.package_size",
            context.registry,
            newer_registry=context.newer_registry,
        )
    if "temperature" in ingredient:
        check_temperature(
            ingredient["temperature"],
            report,
            f"{path}.temperature",
            context.registry,
            newer_registry=context.newer_registry,
        )
    if "recipe" in ingredient:
        _check_dependency(ingredient["recipe"], context, f"{path}.recipe")
    _check_normalization(ingredient, report, path)


def _check_dependency(reference: Any, context: _Context, path: str) -> None:
    """Section 10.3: the recipe that makes an ingredient, identified as in 6.12."""
    if not isinstance(reference, dict):
        return
    report = context.report
    if not any(member in reference for member in ("title", "recipe_id", "url")):
        report.add(
            "dependency-identity-required",
            "a recipe dependency identifies the recipe by title, recipe ID, or URL",
            path,
        )
    recipe_id = reference.get("recipe_id")
    _check_id(recipe_id, report, f"{path}.recipe_id")
    if recipe_id is not None and recipe_id == context.recipe_id:
        report.add(
            "dependency-self-reference",
            "a recipe dependency names another recipe, not this one",
            f"{path}.recipe_id",
        )


def _check_normalization(ingredient: dict[str, Any], report: Report, path: str) -> None:
    normalization = ingredient.get("normalization")
    if not isinstance(normalization, dict):
        return
    method = normalization.get("method")
    if method is not None and method not in NORMALIZATION_METHODS:
        report.add(
            "unknown-normalization-method",
            f"{method!r} is not a method",
            f"{path}.normalization.method",
        )
        return
    # A derived reading is only auditable beside the text it was derived from.
    if (
        method in {"extracted", "inferred", "unresolved"}
        and not (ingredient.get("source_text") or "").strip()
    ):
        report.add(
            "normalization-source-required",
            f"`{method}` normalization keeps the source line it read",
            f"{path}.source_text",
        )
    if method in {"inferred", "unresolved"} and not (normalization.get("warnings") or []):
        report.add(
            "normalization-warning-required",
            f"`{method}` normalization says what is uncertain",
            f"{path}.normalization.warnings",
        )


def _check_instructions(sections: Any, context: _Context) -> None:
    report = context.report
    if not isinstance(sections, list):
        return
    section_ids: set[str] = set()
    step_ids: set[str] = set()
    for section_index, section in enumerate(sections):
        path = f"instruction_sections[{section_index}]"
        if not isinstance(section, dict):
            continue
        section_id = section.get("id")
        if isinstance(section_id, str):
            if section_id in section_ids:
                report.add(
                    "duplicate-instruction-section-id",
                    f"section {section_id!r} is declared twice",
                    path,
                )
            section_ids.add(section_id)
            _check_id(section_id, report, f"{path}.id")

        for index, step in enumerate(section.get("steps") or []):
            _check_step(step, context, step_ids, f"{path}.steps[{index}]")


def _check_step(step: Any, context: _Context, step_ids: set[str], path: str) -> None:
    report = context.report
    if not isinstance(step, dict):
        return
    step_id = step.get("id")
    if isinstance(step_id, str):
        if step_id in step_ids:
            report.add("duplicate-step-id", f"step {step_id!r} is declared twice", path)
        step_ids.add(step_id)
        _check_id(step_id, report, f"{path}.id")

    if not (step.get("text") or "").strip():
        # A title is a heading. Instruction text is what the cook follows, and
        # a step that has only a heading has not said what to do.
        report.add("step-text-required", "a step's instruction text is required", f"{path}.text")

    seen_refs: set[str] = set()
    for index, use in enumerate(step.get("ingredient_uses") or []):
        use_path = f"{path}.ingredient_uses[{index}]"
        if not isinstance(use, dict):
            continue
        reference = use.get("ingredient_ref")
        if not isinstance(reference, str) or not reference:
            report.add(
                "ingredient-use-reference-required",
                "an ingredient use names an ingredient",
                use_path,
            )
            continue
        if reference in seen_refs:
            report.add(
                "duplicate-step-ingredient-reference",
                f"{reference!r} is used twice in one step",
                use_path,
            )
        seen_refs.add(reference)
        if reference not in context.ingredient_ids:
            report.add(
                "dangling-ingredient-reference",
                f"{reference!r} does not resolve to an ingredient",
                use_path,
            )
        if "quantity" in use and reference in context.choice_ids:
            report.add("choice-use-quantity", "a whole-choice use cannot carry quantity", use_path)
        if "quantity" in use:
            before = len(report.violations)
            check_quantity(
                use["quantity"],
                report,
                f"{use_path}.quantity",
                context.registry,
                newer_registry=context.newer_registry,
            )
            for violation in report.violations[before:]:
                if violation.code == "temperature-as-quantity":
                    report.violations.remove(violation)
                    report.add(
                        "temperature-ingredient-use",
                        "a step's ingredient amount is not a temperature",
                        f"{use_path}.quantity",
                    )

    _check_reference_set(
        step.get("image_refs"),
        context,
        f"{path}.image_refs",
        duplicate="duplicate-step-image-reference",
        dangling="dangling-step-image-reference",
        media_type="step-image-media-type",
    )

    seen_timings: set[str] = set()
    for index, reference in enumerate(step.get("timing_refs") or []):
        reference_path = f"{path}.timing_refs[{index}]"
        if reference in seen_timings:
            report.add(
                "duplicate-step-timing-reference",
                f"{reference!r} is referenced twice",
                reference_path,
            )
        seen_timings.add(reference)
        if reference not in context.timing_ids:
            report.add(
                "dangling-step-timing-reference",
                f"{reference!r} does not resolve to a timing assertion",
                reference_path,
            )

    for index, temperature in enumerate(step.get("temperatures") or []):
        check_temperature(
            temperature,
            report,
            f"{path}.temperatures[{index}]",
            context.registry,
            newer_registry=context.newer_registry,
        )


def _check_recipe_images(document: dict[str, Any], context: _Context) -> None:
    """The primary image is checked first, and the order is the point.

    An asset that is not an image fails both as the primary image and as a
    gallery member, because the primary image must also appear in `image_refs`.
    The primary-image failure, the cause, is reported first.
    """
    primary = document.get("primary_image")
    if primary is not None:
        _check_primary_image(document, primary, context)

    _check_reference_set(
        document.get("image_refs"),
        context,
        "image_refs",
        duplicate="duplicate-recipe-image-reference",
        dangling="dangling-recipe-image-reference",
        media_type="recipe-image-media-type",
    )


def _check_primary_image(document: dict[str, Any], primary: Any, context: _Context) -> None:
    report = context.report
    asset = context.assets.get(primary)
    if asset is None:
        report.add(
            "dangling-recipe-image-reference",
            f"{primary!r} does not resolve",
            "primary_image",
        )
        return
    if not str(asset.get("media_type", "")).startswith("image/"):
        report.add("primary-image-media-type", f"{primary!r} is not an image", "primary_image")
    references = document.get("image_refs")
    if not isinstance(references, list) or primary not in references:
        # The primary image is a preference among the recipe's images, not a
        # separate slot, so it has to be one of them.
        report.add(
            "primary-image-not-in-image-refs",
            "the primary image is one of the recipe's own images",
            "primary_image",
        )


def _check_reference_set(
    references: Any,
    context: _Context,
    path: str,
    *,
    duplicate: str,
    dangling: str,
    media_type: str,
) -> None:
    report = context.report
    if not isinstance(references, list):
        return
    seen: set[str] = set()
    for index, reference in enumerate(references):
        reference_path = f"{path}[{index}]"
        if reference in seen:
            report.add(duplicate, f"{reference!r} is referenced twice", reference_path)
        seen.add(reference)
        asset = context.assets.get(reference)
        if asset is None:
            report.add(dangling, f"{reference!r} does not resolve to an asset", reference_path)
            continue
        if not str(asset.get("media_type", "")).startswith("image/"):
            report.add(media_type, f"{reference!r} is not an image asset", reference_path)

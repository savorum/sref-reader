"""Tests for behaviour the published corpus does not pin down.

The conformance corpus is the real proof that this reader follows the
specification, and `tools/conformance.py` runs it. These tests cover the rest:
the library's own API, its resource policy, and the handful of decisions where
the specification leaves a reader a choice and this one has made it.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import pathlib
import sys
import unittest
import zipfile
from importlib import resources

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import sref_reader
from sref_reader import jsonio, rational, snapshot, versions
from sref_reader import registry as registry_module
from sref_reader.errors import Report

MINIMAL = {
    "sref": {"version": "0.2.0", "unit_registry": "0.2.0"},
    "id": "minimal",
    "title": "Minimal",
    "ingredient_sections": [{"id": "i", "ingredients": [{"id": "salt", "name": "salt"}]}],
    "instruction_sections": [{"id": "m", "steps": [{"id": "s", "text": "Cook."}]}],
}


def document(**changes):
    body = json.loads(json.dumps(MINIMAL))
    body.update(changes)
    return json.dumps(body).encode("utf-8")


class StrictJSON(unittest.TestCase):
    def test_a_duplicate_member_is_refused_rather_than_resolved(self):
        with self.assertRaises(jsonio.JSONRejectedError) as caught:
            jsonio.loads(b'{"id":"a","id":"b"}')
        self.assertEqual(caught.exception.code, "duplicate-json-member")

    def test_a_byte_order_mark_is_not_stripped_silently(self):
        with self.assertRaises(jsonio.JSONRejectedError):
            jsonio.loads(b"\xef\xbb\xbf{}")

    def test_a_second_top_level_value_is_a_second_document(self):
        with self.assertRaises(jsonio.JSONRejectedError) as caught:
            jsonio.loads(b'{"a":1} {"a":2}')
        self.assertEqual(caught.exception.code, "trailing-json-value")

    def test_a_number_beyond_float_range_is_not_read_as_infinity(self):
        with self.assertRaises(jsonio.JSONRejectedError) as caught:
            jsonio.loads(b'{"a":1e1000000}')
        self.assertEqual(caught.exception.code, "nonfinite-json-number")


def nested(depth: int) -> str:
    """A JSON array nested `depth` levels, small enough to fit any size limit."""
    return "[" * depth + "]" * depth


class NestingDepth(unittest.TestCase):
    """Hostile nesting is a resource limit, reported like any other, and never a crash."""

    HOSTILE = 5000

    def _archive(self, entries: dict[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        return buffer.getvalue()

    def test_deep_nesting_in_a_document_is_a_resource_limit(self):
        with self.assertRaises(jsonio.JSONRejectedError) as caught:
            jsonio.loads(nested(self.HOSTILE))
        self.assertEqual(caught.exception.code, "json-depth-limit")

    def test_the_limit_is_measured_without_recursing(self):
        # Far deeper than any stack, and still measured.
        with self.assertRaises(jsonio.JSONRejectedError) as caught:
            jsonio.loads("[" * 2_000_000)
        self.assertEqual(caught.exception.code, "json-depth-limit")

    def test_brackets_inside_strings_are_not_nesting(self):
        text = json.dumps({"note": "[" * 5000 + '{"' * 5000})
        self.assertEqual(jsonio.loads(text), json.loads(text))

    def test_nesting_at_the_limit_is_read_and_one_beyond_is_not(self):
        limit = sref_reader.DEFAULT_LIMITS.json_depth
        self.assertEqual(jsonio.loads(nested(limit)), json.loads(nested(limit)))
        with self.assertRaises(jsonio.JSONRejectedError):
            jsonio.loads(nested(limit + 1))

    def test_a_recipe_nested_thousands_deep_reports_a_resource_limit(self):
        body = json.loads(json.dumps(MINIMAL))
        text = json.dumps(body)[:-1] + ',"x-hostile":' + nested(self.HOSTILE) + "}"
        recipe, report = sref_reader.validate_recipe(text.encode())
        self.assertIsNone(recipe)
        self.assertEqual(report.category, "resource-limit")
        self.assertIn("json-depth-limit", report.codes)

    def test_a_decoded_value_that_nests_too_deeply_is_refused_too(self):
        deep: list = []
        for _ in range(self.HOSTILE):
            deep = [deep]
        body = json.loads(json.dumps(MINIMAL))
        body["x-hostile"] = deep
        recipe, report = sref_reader.validate_recipe(body)
        self.assertIsNone(recipe)
        self.assertEqual(report.category, "resource-limit")

    def test_a_package_recipe_nested_thousands_deep_reports_a_resource_limit(self):
        data = self._archive({"manifest.json": b"{}", "recipe.json": nested(self.HOSTILE).encode()})
        package, report = sref_reader.validate_package(data)
        self.assertIsNone(package)
        self.assertIn("json-depth-limit", report.codes)
        self.assertIn("resource-limit", {v.category for v in report.violations})

    def test_a_package_manifest_nested_thousands_deep_reports_a_resource_limit(self):
        data = self._archive({"manifest.json": nested(self.HOSTILE).encode(), "recipe.json": b"{}"})
        package, report = sref_reader.validate_package(data)
        self.assertIsNone(package)
        self.assertIn("json-depth-limit", report.codes)
        self.assertIn("resource-limit", {v.category for v in report.violations})

    def test_a_bundle_manifest_nested_thousands_deep_reports_a_resource_limit(self):
        data = self._archive({"manifest.json": nested(self.HOSTILE).encode()})
        bundle, report = sref_reader.validate_bundle(data)
        self.assertIsNone(bundle)
        self.assertIn("json-depth-limit", report.codes)
        self.assertIn("resource-limit", {v.category for v in report.violations})

    def test_the_depth_limit_is_policy_a_caller_can_tighten(self):
        text = json.dumps(MINIMAL)
        _, report = sref_reader.validate_recipe(text.encode())
        self.assertTrue(report.valid)
        with self.assertRaises(jsonio.JSONRejectedError):
            jsonio.loads(text, max_depth=2)


class Rationals(unittest.TestCase):
    def test_canonical_forms_parse_exactly(self):
        self.assertEqual(str(rational.parse("1/3")), "1/3")
        self.assertEqual(str(rational.parse("9/4")), "9/4")

    def test_an_unreduced_fraction_is_not_merely_equivalent(self):
        # `2/4` and `1/2` are the same number and different documents. SREF
        # picks one spelling so that equality is decidable without arithmetic.
        with self.assertRaises(rational.NotCanonicalError) as caught:
            rational.parse("2/4")
        self.assertEqual(caught.exception.code, "unreduced-rational")

    def test_a_decimal_is_refused_rather_than_converted(self):
        with self.assertRaises(rational.NotCanonicalError):
            rational.parse("1.5")

    def test_negative_zero_is_never_canonical(self):
        with self.assertRaises(rational.NotCanonicalError):
            rational.parse("-0", signed=True)


class Versions(unittest.TestCase):
    def test_a_newer_release_in_the_same_line_is_readable(self):
        self.assertEqual(
            versions.compare("0.2.0", "0.1.0"),
            versions.Compatibility(supported=True, newer=True),
        )

    def test_a_different_major_line_is_not(self):
        self.assertEqual(
            versions.compare("1.0.0", "0.1.0"),
            versions.Compatibility(supported=False, newer=False),
        )

    def test_an_unparseable_version_is_not_assumed_to_be_the_current_one(self):
        # Treating `0.1` as `0.1.0` would be exactly the silent reinterpretation
        # section 5 forbids.
        self.assertFalse(versions.compare("0.1", "0.1.0").supported)

    def test_a_compatible_newer_registry_keeps_its_unknown_units(self):
        data = document(
            sref={"version": "0.4.0", "unit_registry": "0.3.0"},
            ingredient_sections=[
                {
                    "id": "i",
                    "ingredients": [
                        {
                            "id": "x",
                            "name": "future",
                            "quantity": {
                                "kind": "simple",
                                "amount": {"value": "1"},
                                "unit": "volume.measure.future",
                            },
                        },
                    ],
                },
            ],
        )
        parsed, report = sref_reader.validate_recipe(data)
        self.assertTrue(report.valid, report.codes)
        unit = parsed["ingredient_sections"][0]["ingredients"][0]["quantity"]["unit"]
        self.assertEqual(unit, "volume.measure.future")

    def test_the_same_unknown_unit_is_refused_under_the_exact_registry(self):
        data = document(
            ingredient_sections=[
                {
                    "id": "i",
                    "ingredients": [
                        {
                            "id": "x",
                            "name": "future",
                            "quantity": {
                                "kind": "simple",
                                "amount": {"value": "1"},
                                "unit": "volume.measure.future",
                            },
                        },
                    ],
                },
            ],
        )
        _, report = sref_reader.validate_recipe(data)
        self.assertIn("unknown-unit", report.codes)


class Preservation(unittest.TestCase):
    def test_unknown_standard_members_and_extensions_survive_reading(self):
        data = document(
            sref={"version": "0.5.0", "unit_registry": "0.2.0"},
            publication={"status": "draft"},
            **{"x-org.example.note": {"kept": True}},
        )
        parsed = sref_reader.read_recipe(data)
        self.assertEqual(parsed["publication"], {"status": "draft"})
        self.assertEqual(parsed["x-org.example.note"], {"kept": True})

    def test_an_extension_cannot_supply_a_required_member(self):
        body = json.loads(json.dumps(MINIMAL))
        del body["title"]
        body["x-org.example.title"] = "Not the standard title"
        _, report = sref_reader.validate_recipe(json.dumps(body).encode())
        self.assertIn("extension-cannot-satisfy-core", report.codes)


class Equipment(unittest.TestCase):
    def test_authored_equipment_is_read_without_inventing_identity(self):
        parsed = sref_reader.read_recipe(
            document(
                equipment=[
                    {
                        "name": "8-inch cake pan",
                        "quantity": {
                            "kind": "simple",
                            "amount": {"value": "2"},
                            "unit": "count.item",
                        },
                    },
                    {"name": "stand mixer with dough hook", "note": "Use the dough hook."},
                ],
            ),
        )
        self.assertEqual(
            [item["name"] for item in parsed["equipment"]],
            [
                "8-inch cake pan",
                "stand mixer with dough hook",
            ],
        )
        self.assertNotIn("id", parsed["equipment"][0])

    def test_temperature_is_not_an_equipment_quantity(self):
        _, report = sref_reader.validate_recipe(
            document(
                equipment=[
                    {
                        "name": "oven",
                        "quantity": {
                            "kind": "simple",
                            "amount": {"value": "180"},
                            "unit": "temperature.celsius",
                        },
                    }
                ],
            ),
        )
        self.assertIn("temperature-equipment", report.codes)


class Reporting(unittest.TestCase):
    def test_public_diagnostics_separate_category_from_requirement(self):
        body = json.loads(json.dumps(MINIMAL))
        body["ingredient_sections"][0]["ingredients"][0]["quantity"] = {
            "kind": "simple",
            "amount": {"value": "1"},
            "unit": "volume.measure.future",
        }
        _, report = sref_reader.validate_recipe(json.dumps(body).encode())
        violation = next(v for v in report.violations if v.code == "unknown-unit")
        self.assertEqual(violation.category, "unknown-unit")
        self.assertEqual(violation.requirement_id, "unknown-unit")
        self.assertEqual(report.category, "unknown-unit")
        self.assertEqual(report.requirement_id, "unknown-unit")

    def test_every_independent_failure_is_reported_at_once(self):
        # Section 23: report every failure that can be found safely.
        body = json.loads(json.dumps(MINIMAL))
        body["ingredient_sections"][0]["ingredients"].append({"id": "salt", "name": "more salt"})
        body["instruction_sections"][0]["steps"].append({"id": "s", "text": "Again."})
        _, report = sref_reader.validate_recipe(json.dumps(body).encode())
        self.assertIn("duplicate-ingredient-id", report.codes)
        self.assertIn("duplicate-step-id", report.codes)

    def test_a_violation_names_where_it_happened(self):
        body = json.loads(json.dumps(MINIMAL))
        body["ingredient_sections"][0]["ingredients"][0]["optional"] = "yes"
        _, report = sref_reader.validate_recipe(json.dumps(body).encode())
        violation = next(v for v in report.violations if v.code == "optional-must-be-boolean")
        self.assertEqual(violation.path, "ingredient_sections[0].ingredients[0].optional")


class ArchiveSafety(unittest.TestCase):
    def _archive(self, entries: dict[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        return buffer.getvalue()

    def test_a_traversal_path_is_rejected_before_anything_is_read(self):
        data = self._archive(
            {"../escape.json": b"{}", "manifest.json": b"{}", "recipe.json": b"{}"},
        )
        _, report = sref_reader.validate_package(data)
        self.assertIn("unsafe-archive-path", report.codes)

    def test_a_zip_bomb_is_refused_by_ratio_rather_than_expanded(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", b"{}")
            archive.writestr("recipe.json", b"0" * (4 << 20))
        _, report = sref_reader.validate_package(buffer.getvalue())
        self.assertIn("compression-ratio-limit", report.codes)

    def test_limits_are_policy_a_caller_can_tighten(self):
        data = self._archive({"manifest.json": b"{}", "recipe.json": b"{}"})
        _, report = sref_reader.validate_package(data, sref_reader.Limits(archive_bytes=8))
        self.assertIn("archive-bytes-limit", report.codes)
        # A ceiling this caller chose, not a claim that the archive is broken.
        self.assertEqual(report.category, "resource-limit")


class Units(unittest.TestCase):
    def setUp(self):
        self.registry = sref_reader.unit_registry()

    def test_conversion_is_exact_rather_than_rounded(self):
        amount = self.registry.convert_amount(
            {"value": "1"},
            "volume.cup.us.customary",
            "volume.milliliter",
        )
        self.assertEqual(amount, {"value": "473176473/2000000"})

    def test_an_inexact_definition_makes_the_result_approximate(self):
        amount = self.registry.convert_amount(
            {"value": "1"},
            "volume.cup.us.culinary",
            "volume.milliliter",
        )
        self.assertTrue(amount["approximate"])

    def test_a_range_converts_endpoint_by_endpoint_and_keeps_its_order(self):
        amount = self.registry.convert_amount(
            {"min": "27", "max": "28"},
            "temperature.celsius",
            "temperature.fahrenheit",
        )
        self.assertEqual(amount, {"min": "403/5", "max": "412/5"})

    def test_mass_to_volume_is_refused_rather_than_guessed(self):
        with self.assertRaises(registry_module.IncompatibleDimensionError):
            self.registry.convert_amount({"value": "100"}, "mass.gram", "volume.milliliter")

    def test_a_count_unit_has_no_physical_size(self):
        with self.assertRaises(registry_module.NonphysicalUnitError):
            self.registry.convert_amount({"value": "2"}, "count.clove", "mass.gram")

    def test_the_pinned_registry_validates_against_its_own_rules(self):
        report = Report()
        self.assertIsNotNone(registry_module.load(snapshot.units(), report))
        self.assertTrue(report.valid, report.codes)


class MicrowaveConditions(unittest.TestCase):
    def body(self, microwave, duration=None, version="0.4.0"):
        body = json.loads(document())
        body["sref"]["version"] = version
        body["times"] = {
            "assertions": [
                {
                    "id": "heat",
                    "kind": "cook",
                    "duration": duration or {"value": "PT2M"},
                    "microwave": microwave,
                },
            ],
        }
        body["instruction_sections"][0]["steps"][0]["timing_refs"] = ["heat"]
        return body

    def test_conditions_and_ordered_choices_are_read(self):
        microwave = {
            "source_text": "600 W: 4 minutes; 500 W: 5 minutes",
            "choices": [
                {
                    "power": {"watts": 600},
                    "duration": {"value": "PT4M"},
                    "source_text": "600 W: 4 minutes",
                },
                {
                    "power": {"watts": 500},
                    "duration": {"value": "PT5M"},
                    "source_text": "500 W: 5 minutes",
                },
            ],
        }
        parsed = sref_reader.read_recipe(
            self.body(microwave, {"text": "600 W: 4 minutes; 500 W: 5 minutes"})
        )
        self.assertEqual(
            [
                choice["power"]["watts"]
                for choice in parsed["times"]["assertions"][0]["microwave"]["choices"]
            ],
            [600, 500],
        )

    def test_invalid_percentage_and_duplicate_conditions_are_rejected(self):
        invalid_percent = {"source_text": "at 101%", "power": {"percent": "101"}}
        self.assertIn(
            "microwave-percent-out-of-range",
            sref_reader.validate_recipe(self.body(invalid_percent))[1].codes,
        )
        duplicate = {
            "source_text": "600 W alternatives",
            "choices": [
                {"power": {"watts": 600}, "duration": {"value": "PT4M"}, "source_text": "first"},
                {"power": {"watts": 600}, "duration": {"value": "PT5M"}, "source_text": "second"},
            ],
        }
        report = sref_reader.validate_recipe(self.body(duplicate, {"text": "600 W alternatives"}))[
            1
        ]
        self.assertIn("microwave-choice-duplicate-conditions", report.codes)

    def test_historical_0_2_cannot_emit_the_0_3_member(self):
        microwave = {"source_text": "at 600 W", "power": {"watts": 600}}
        report = sref_reader.validate_recipe(self.body(microwave, version="0.2.0"))[1]
        self.assertIn("member-not-defined-by-version", report.codes)


class TranslationRelationships(unittest.TestCase):
    def body(self, translations, version="0.4.0", **changes):
        body = json.loads(document(**changes))
        body["sref"]["version"] = version
        body["translations"] = translations
        return body

    def test_an_unresolved_counterpart_is_read_and_kept(self):
        translations = [
            {"relation": "original", "language": "la", "title": "Conditum paradoxum"},
            {"relation": "alternate", "language": "ar", "recipe_id": "not-in-this-document"},
        ]
        parsed = sref_reader.read_recipe(self.body(translations, language="en"))
        self.assertEqual(parsed["translations"], translations)

    def test_each_rule_reports_its_own_requirement(self):
        translations = [
            {"relation": "original", "language": "fr", "title": "Recette"},
            {"relation": "original", "language": "fr_FR", "recipe_id": MINIMAL["id"]},
            {"relation": "adaptation", "language": "de"},
        ]
        codes = sref_reader.validate_recipe(self.body(translations))[1].codes
        for code in (
            "translation-requires-document-language",
            "translation-single-original",
            "translation-invalid-language",
            "translation-self-reference",
            "translation-unknown-relation",
            "translation-counterpart-required",
        ):
            self.assertIn(code, codes)

    def test_historical_0_2_cannot_emit_the_0_3_member(self):
        translations = [{"relation": "alternate", "language": "fr", "title": "Recette"}]
        report = sref_reader.validate_recipe(self.body(translations, "0.2.0", language="en"))[1]
        self.assertIn("member-not-defined-by-version", report.codes)


class YieldsAcrossVersions(unittest.TestCase):
    def body(self, version, **members):
        body = json.loads(document())
        body["sref"]["version"] = version
        body.update(members)
        return body

    def test_a_0_2_document_keeps_its_singular_yield(self):
        parsed = sref_reader.read_recipe(self.body("0.2.0", **{"yield": {"text": "4 servings"}}))
        self.assertEqual(parsed["yield"], {"text": "4 servings"})

    def test_a_0_3_document_refuses_the_singular_yield(self):
        report = sref_reader.validate_recipe(self.body("0.4.0", **{"yield": {"text": "4"}}))[1]
        self.assertIn("singular-yield-member", report.codes)

    def test_a_0_2_document_cannot_carry_0_3_members(self):
        body = self.body(
            "0.2.0",
            nutrition=[
                {
                    "basis": {"kind": "recipe"},
                    "nutrients": [{"nutrient": "protein", "text": "some"}],
                }
            ],
            allergen_declarations=[
                {"text": "Contains milk", "substance": "milk", "presence": "contains"}
            ],
        )
        self.assertIn("member-not-defined-by-version", sref_reader.validate_recipe(body)[1].codes)

    def test_independent_yields_are_all_kept_in_order(self):
        yields = [{"text": "12 cookies"}, {"text": "6 servings"}, {"text": "12 cookies"}]
        self.assertEqual(
            sref_reader.read_recipe(self.body("0.4.0", yields=yields))["yields"], yields
        )


class PinnedSnapshot(unittest.TestCase):
    """The snapshot is read as package data, not as a path beside the package.

    The `installed` CI job checks packaging; this checks the library never
    reaches outside itself for the snapshot.
    """

    def test_every_snapshot_file_loads_and_matches_its_digest(self):
        for name in snapshot.manifest()["files"]:
            with self.subTest(name=name):
                self.assertIsNotNone(snapshot.load(name))

    def test_the_snapshot_is_read_from_inside_the_package(self):
        located = resources.files("sref_reader") / "_vendor" / "sref-0.4.0" / "manifest.json"
        self.assertTrue(located.is_file())

    def test_a_snapshot_file_the_release_did_not_record_is_refused(self):
        with self.assertRaises(snapshot.SnapshotCorruptError):
            snapshot.read("schema/invented.schema.json")


class ManifestSchemas(unittest.TestCase):
    """Every fixed and required manifest member is actually enforced.

    The package version, the constant recipe path and media type, and the
    closed member sets, each reported as `malformed-package-json`.
    """

    def archive(self, entries: dict[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        return buffer.getvalue()

    def manifest(self, recipe: bytes) -> dict:
        return {
            "sref_package": {"version": "0.1.0"},
            "recipe": {
                "path": "recipe.json",
                "media_type": "application/json",
                "sha256": hashlib.sha256(recipe).hexdigest(),
                "size": len(recipe),
            },
            "assets": [],
        }

    def package(self, change=None) -> bytes:
        recipe = document()
        manifest = self.manifest(recipe)
        if change is not None:
            change(manifest)
        return self.archive({"manifest.json": json.dumps(manifest).encode(), "recipe.json": recipe})

    def bundle_manifest(self, member: bytes) -> dict:
        return {
            "format": "sref-bundle",
            "version": 1,
            "recipes": [
                {
                    "member_id": "r1",
                    "recipe_id": "minimal",
                    "path": "recipes/r1.sref",
                    "sha256": hashlib.sha256(member).hexdigest(),
                    "size": len(member),
                },
            ],
        }

    def bundle(self, change=None) -> bytes:
        member = self.package()
        manifest = self.bundle_manifest(member)
        if change is not None:
            change(manifest)
        return self.archive(
            {"manifest.json": json.dumps(manifest).encode(), "recipes/r1.sref": member},
        )

    def test_an_unaltered_package_and_bundle_are_accepted(self):
        # Without this the rest could pass by rejecting everything.
        _, report = sref_reader.validate_package(self.package())
        self.assertTrue(report.valid, report.codes)
        _, report = sref_reader.validate_bundle(self.bundle())
        self.assertTrue(report.valid, report.codes)

    def refuses_package(self, change):
        _, report = sref_reader.validate_package(self.package(change))
        self.assertIn("malformed-package-json", report.codes)

    def test_a_manifest_without_the_package_version_is_refused(self):
        self.refuses_package(lambda manifest: manifest.pop("sref_package"))

    def test_a_package_version_this_build_does_not_implement_is_refused(self):
        self.refuses_package(lambda manifest: manifest["sref_package"].update(version="9.9.9"))

    def test_an_unknown_member_of_the_package_version_object_is_refused(self):
        self.refuses_package(lambda manifest: manifest["sref_package"].update(flavour="extra"))

    def test_a_manifest_without_a_recipe_entry_is_refused(self):
        self.refuses_package(lambda manifest: manifest.pop("recipe"))

    def test_a_recipe_entry_naming_another_file_is_refused(self):
        self.refuses_package(lambda manifest: manifest["recipe"].update(path="not-recipe.json"))

    def test_a_recipe_entry_claiming_another_media_type_is_refused(self):
        self.refuses_package(lambda manifest: manifest["recipe"].update(media_type="text/plain"))

    def test_a_recipe_entry_without_a_digest_is_refused(self):
        self.refuses_package(lambda manifest: manifest["recipe"].pop("sha256"))

    def test_a_recipe_entry_without_a_size_is_refused(self):
        self.refuses_package(lambda manifest: manifest["recipe"].pop("size"))

    def test_a_manifest_without_an_assets_array_is_refused(self):
        # Required even when the recipe has none.
        self.refuses_package(lambda manifest: manifest.pop("assets"))

    def test_an_unknown_root_member_is_refused(self):
        self.refuses_package(lambda manifest: manifest.update(extra=True))

    def refuses_bundle(self, change):
        _, report = sref_reader.validate_bundle(self.bundle(change))
        self.assertIn("malformed-package-json", report.codes)

    def test_a_bundle_manifest_declaring_another_format_is_refused(self):
        self.refuses_bundle(lambda manifest: manifest.update(format="sref-collection"))

    def test_a_bundle_manifest_without_its_format_is_refused(self):
        self.refuses_bundle(lambda manifest: manifest.pop("format"))

    def test_a_bundle_manifest_version_this_build_does_not_implement_is_refused(self):
        self.refuses_bundle(lambda manifest: manifest.update(version=2))

    def test_a_bundle_manifest_without_its_version_is_refused(self):
        self.refuses_bundle(lambda manifest: manifest.pop("version"))

    def test_an_unknown_bundle_manifest_root_member_is_refused(self):
        self.refuses_bundle(lambda manifest: manifest.update(extra=True))

    def test_a_bundle_member_entry_missing_a_required_member_is_refused(self):
        for member in ("member_id", "recipe_id", "path", "sha256", "size"):
            with self.subTest(member=member):
                _, report = sref_reader.validate_bundle(
                    self.bundle(lambda manifest, member=member: manifest["recipes"][0].pop(member)),
                )
                self.assertFalse(report.valid)

    def test_a_specific_category_still_outranks_the_schema(self):
        # When both apply, the specific `recipe-digest-mismatch` is primary.
        def change(manifest):
            manifest["recipe"]["sha256"] = "0" * 64
            manifest["extra"] = True

        _, report = sref_reader.validate_package(self.package(change))
        self.assertIn("malformed-package-json", report.codes)
        self.assertEqual(report.primary, "recipe-digest-mismatch")

    def test_a_recipe_document_keeps_the_generic_category(self):
        # The schema is a second gate for a recipe, behind checks that name
        # their own requirements, so a schema-only failure stays the least
        # specific code this reader emits.
        body = json.loads(document())
        body["title"] = 5
        _, report = sref_reader.validate_recipe(json.dumps(body).encode())
        self.assertIn("schema-violation", report.codes)

    def test_the_timestamp_rule_keeps_its_own_category(self):
        _, report = sref_reader.validate_bundle(
            self.bundle(lambda manifest: manifest.update(generated_at="2026-01-01T00:00:00Z")),
        )
        self.assertEqual(report.primary, "bundle-manifest-timestamp")


class ResourcePolicy(unittest.TestCase):
    """One allowance per operation, shared by a bundle's members, spent once,
    and reported as a violation.
    """

    def package(self, asset: bytes | None = None) -> bytes:
        body = json.loads(document())
        entries = {}
        if asset is not None:
            body["assets"] = [
                {
                    "id": "a",
                    "path": "assets/a.bin",
                    "media_type": "application/octet-stream",
                    "sha256": hashlib.sha256(asset).hexdigest(),
                    "size": len(asset),
                },
            ]
            entries["assets/a.bin"] = asset
        recipe = json.dumps(body).encode()
        manifest = {
            "sref_package": {"version": "0.1.0"},
            "recipe": {
                "path": "recipe.json",
                "media_type": "application/json",
                "sha256": hashlib.sha256(recipe).hexdigest(),
                "size": len(recipe),
            },
            "assets": body.get("assets", []),
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest).encode())
            archive.writestr("recipe.json", recipe)
            for name, content in entries.items():
                archive.writestr(name, content)
        return buffer.getvalue()

    def bundle(self, members: list[bytes]) -> bytes:
        buffer = io.BytesIO()
        declared = []
        with zipfile.ZipFile(buffer, "w") as archive:
            for index, member in enumerate(members, start=1):
                path = f"recipes/r{index}.sref"
                declared.append(
                    {
                        "member_id": f"r{index}",
                        "recipe_id": "minimal",
                        "path": path,
                        "sha256": hashlib.sha256(member).hexdigest(),
                        "size": len(member),
                    },
                )
                archive.writestr(path, member)
            archive.writestr(
                "manifest.json",
                json.dumps({"format": "sref-bundle", "version": 1, "recipes": declared}).encode(),
            )
        return buffer.getvalue()

    def incompressible(self, size: int) -> bytes:
        # Compressible enough that the member file stays small, not so
        # compressible that the per-entry ratio limit notices first.
        return (os.urandom(size // 10) * 10)[:size]

    def test_a_limit_failure_is_a_violation_rather_than_an_exception(self):
        # `validate_*` reports exhaustion as a violation rather than raising.
        package, report = sref_reader.validate_package(
            self.package(),
            sref_reader.Limits(json_bytes=64),
        )
        self.assertIsNone(package)
        self.assertIn("json-bytes-limit", report.codes)

    def test_the_matching_read_raises_that_same_failure(self):
        with self.assertRaises(sref_reader.SrefError) as caught:
            sref_reader.read_package(self.package(), sref_reader.Limits(json_bytes=64))
        self.assertIn("json-bytes-limit", caught.exception.codes)

    def test_each_limit_is_reported_under_its_own_category(self):
        # Each per-entry overrun names the limit it crossed.
        for limits, expected in (
            (sref_reader.Limits(json_bytes=64), "json-bytes-limit"),
            (sref_reader.Limits(bytes_per_entry=64), "entry-bytes-limit"),
        ):
            with self.subTest(expected=expected):
                _package, report = sref_reader.validate_package(self.package(), limits)
                self.assertIn(expected, report.codes)
                self.assertNotIn("compression-ratio-limit", report.codes)

    def test_bundle_members_share_one_expanded_byte_allowance(self):
        asset = self.incompressible(3000)
        data = self.bundle([self.package(asset), self.package(asset[::-1])])

        # Either member alone fits; the two of them together do not.
        _, report = sref_reader.validate_bundle(data, sref_reader.Limits(expanded_bytes=5000))
        self.assertIn("expanded-bytes-limit", report.codes)
        self.assertEqual(report.category, "resource-limit")

        bundle, report = sref_reader.validate_bundle(data, sref_reader.Limits(expanded_bytes=20000))
        self.assertTrue(report.valid, report.codes)
        retained = sum(
            len(content) for member in bundle.members for content in member.package.assets.values()
        )
        self.assertEqual(retained, 6000)

    def test_exhaustion_inside_a_member_names_the_ceiling_not_the_member(self):
        # A member that runs the operation out of budget has not been shown to
        # be an invalid package. Nothing about it was established.
        asset = self.incompressible(3000)
        data = self.bundle([self.package(asset), self.package(asset[::-1])])
        _, report = sref_reader.validate_bundle(data, sref_reader.Limits(expanded_bytes=5000))
        self.assertEqual(report.primary, "expanded-bytes-limit")
        self.assertNotIn("invalid-bundle-member-package", report.codes)
        self.assertIn("recipes/r", report.violations[0].message)

    def test_entries_are_counted_across_the_whole_operation(self):
        data = self.bundle([self.package(), self.package()])
        _, report = sref_reader.validate_bundle(data, sref_reader.Limits(entries=4))
        self.assertIn("entry-count-limit", report.codes)
        self.assertEqual(report.category, "resource-limit")

    def test_members_are_counted_against_the_callers_ceiling(self):
        data = self.bundle([self.package(), self.package()])
        _, report = sref_reader.validate_bundle(data, sref_reader.Limits(members=1))
        self.assertIn("member-count-limit", report.codes)
        self.assertEqual(report.category, "resource-limit")

    def test_a_package_opened_on_its_own_gets_a_whole_allowance(self):
        asset = self.incompressible(3000)
        _, report = sref_reader.validate_package(
            self.package(asset),
            sref_reader.Limits(expanded_bytes=20000),
        )
        self.assertTrue(report.valid, report.codes)


class StreamingMembers(unittest.TestCase):
    """`bundle_members` yields what `read_bundle` returns, one at a time."""

    def bundle(self, count: int = 3, extra: dict[str, bytes] | None = None) -> bytes:
        recipe = document()
        manifest = {
            "sref_package": {"version": "0.1.0"},
            "recipe": {
                "path": "recipe.json",
                "media_type": "application/json",
                "sha256": hashlib.sha256(recipe).hexdigest(),
                "size": len(recipe),
            },
            "assets": [],
        }
        member = io.BytesIO()
        with zipfile.ZipFile(member, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest).encode())
            archive.writestr("recipe.json", recipe)
        package = member.getvalue()

        buffer = io.BytesIO()
        declared = []
        with zipfile.ZipFile(buffer, "w") as archive:
            for index in range(1, count + 1):
                path = f"recipes/r{index:06d}.sref"
                declared.append(
                    {
                        "member_id": f"r{index:06d}",
                        "recipe_id": "minimal",
                        "path": path,
                        "sha256": hashlib.sha256(package).hexdigest(),
                        "size": len(package),
                    },
                )
                archive.writestr(path, package)
            for name, content in (extra or {}).items():
                archive.writestr(name, content)
            archive.writestr(
                "manifest.json",
                json.dumps({"format": "sref-bundle", "version": 1, "recipes": declared}).encode(),
            )
        return buffer.getvalue()

    def test_it_yields_the_members_read_bundle_returns(self):
        data = self.bundle()
        streamed = [member.member_id for member in sref_reader.bundle_members(data)]
        self.assertEqual(
            streamed,
            [member.member_id for member in sref_reader.read_bundle(data).members],
        )

    def test_it_yields_rather_than_building_a_list(self):
        # A caller can stop early.
        stream = sref_reader.bundle_members(self.bundle(count=5))
        self.assertEqual(next(stream).member_id, "r000001")
        self.assertEqual(next(stream).member_id, "r000002")
        stream.close()

    def test_it_raises_on_an_invalid_bundle(self):
        data = self.bundle(extra={"recipes/stowaway.sref": b"not a package"})
        with self.assertRaises(sref_reader.SrefError) as caught:
            list(sref_reader.bundle_members(data))
        self.assertIn("undeclared-bundle-entry", caught.exception.codes)

    def test_stopping_early_forgoes_only_the_whole_bundle_check(self):
        # An undeclared entry cannot be detected until every declared member
        # has been seen, so a caller who stops has chosen not to ask. Each
        # member yielded before that point was fully validated.
        data = self.bundle(extra={"recipes/stowaway.sref": b"not a package"})
        first = next(iter(sref_reader.bundle_members(data)))
        self.assertEqual(first.member_id, "r000001")
        self.assertEqual(first.package.recipe["id"], "minimal")

    def test_a_member_that_is_not_a_valid_package_stops_the_iteration(self):
        data = self.bundle()
        with zipfile.ZipFile(io.BytesIO(data)) as source:
            names = source.namelist()
            contents = {name: source.read(name) for name in names}
        contents["recipes/r000002.sref"] = b"not a package"
        manifest = json.loads(contents["manifest.json"])
        for entry in manifest["recipes"]:
            if entry["member_id"] == "r000002":
                entry["sha256"] = hashlib.sha256(contents["recipes/r000002.sref"]).hexdigest()
                entry["size"] = len(contents["recipes/r000002.sref"])
        contents["manifest.json"] = json.dumps(manifest).encode()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name in names:
                archive.writestr(name, contents[name])

        stream = sref_reader.bundle_members(buffer.getvalue())
        self.assertEqual(next(stream).member_id, "r000001")
        with self.assertRaises(sref_reader.SrefError):
            next(stream)

    def test_it_spends_the_same_operation_budget(self):
        with self.assertRaises(sref_reader.SrefError) as caught:
            list(sref_reader.bundle_members(self.bundle(count=4), sref_reader.Limits(members=2)))
        self.assertIn("member-count-limit", caught.exception.codes)


class VersionScopedMembers(unittest.TestCase):
    """A member is admitted by the version the document declares.

    Section 13 permits unknown standard members only when reading a newer
    compatible version.
    """

    def document(self, version: str = "0.4.0", **members) -> bytes:
        base = {
            "sref": {"version": version, "unit_registry": "0.2.0"},
            "id": "scoped",
            "title": "Scoped",
            "ingredient_sections": [{"id": "i", "ingredients": [{"id": "salt", "name": "salt"}]}],
            "instruction_sections": [{"id": "m", "steps": [{"id": "s", "text": "Season."}]}],
        }
        base.update(members)
        return json.dumps(base).encode()

    def test_an_unknown_member_at_the_declared_version_is_refused(self):
        _recipe, report = sref_reader.validate_recipe(
            self.document(future_standard_field={"value": 4})
        )
        self.assertIn("member-not-defined-by-version", report.codes)

    def test_the_same_member_under_a_newer_version_is_accepted(self):
        recipe, report = sref_reader.validate_recipe(
            self.document(version="0.5.0", future_standard_field={"value": 4})
        )
        self.assertTrue(report.valid, report.codes)
        self.assertEqual(recipe["future_standard_field"], {"value": 4})

    def test_an_extension_is_accepted_at_the_declared_version(self):
        _recipe, report = sref_reader.validate_recipe(
            self.document(**{"x-org.example.note": {"reviewed": True}})
        )
        self.assertTrue(report.valid, report.codes)

    def test_a_newer_registry_alone_does_not_admit_an_unknown_member(self):
        # The two compatibility lines are independent: a newer registry widens
        # the unit vocabulary and says nothing about which members exist.
        body = json.loads(self.document(future_standard_field={"value": 4}))
        body["sref"]["unit_registry"] = "0.3.0"
        _recipe, report = sref_reader.validate_recipe(json.dumps(body).encode())
        self.assertIn("member-not-defined-by-version", report.codes)

    def test_a_nested_unknown_member_is_refused_too(self):
        body = json.loads(self.document())
        body["ingredient_sections"][0]["ingredients"][0]["future_member"] = True
        _recipe, report = sref_reader.validate_recipe(json.dumps(body).encode())
        self.assertIn("member-not-defined-by-version", report.codes)


class CorruptedEntries(unittest.TestCase):
    """An entry whose bytes cannot be decoded is a violation, never an exception."""

    def corrupt(self, data: bytes, name: str) -> bytes:
        info = zipfile.ZipFile(io.BytesIO(data)).getinfo(name)
        start = info.header_offset + 30 + len(info.filename) + len(info.extra)
        damaged = bytearray(data)
        damaged[start + 5] ^= 0x01
        return bytes(damaged)

    def package(self, compression: int) -> bytes:
        schemas = ManifestSchemas()
        recipe = document()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression) as archive:
            archive.writestr("manifest.json", json.dumps(schemas.manifest(recipe)).encode())
            archive.writestr("recipe.json", recipe)
        return buffer.getvalue()

    def test_a_damaged_package_entry_is_reported(self):
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            with self.subTest(compression=compression):
                data = self.corrupt(self.package(compression), "recipe.json")
                _, report = sref_reader.validate_package(data)
                self.assertEqual(report.codes, ["invalid-zip"])
                with self.assertRaises(sref_reader.SrefError):
                    sref_reader.read_package(data)

    def test_a_damaged_bundle_entry_is_reported(self):
        data = self.corrupt(ManifestSchemas().bundle(), "manifest.json")
        _, report = sref_reader.validate_bundle(data)
        self.assertEqual(report.codes, ["invalid-zip-bundle"])
        with self.assertRaises(sref_reader.SrefError):
            list(sref_reader.bundle_members(data))


if __name__ == "__main__":
    unittest.main()

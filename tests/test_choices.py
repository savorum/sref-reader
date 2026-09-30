import copy
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import sref_reader


def choice_recipe():
    return {
        "sref": {"version": "0.4.0", "unit_registry": "0.2.0"},
        "id": "choices",
        "title": "Choices",
        "ingredient_sections": [
            {
                "id": "i",
                "ingredients": [
                    {
                        "id": "fat",
                        "relation": "or",
                        "alternatives": [
                            {"id": "butter", "name": "butter"},
                            {"id": "oil", "name": "oil"},
                        ],
                    }
                ],
            }
        ],
    }


class ChoiceTests(unittest.TestCase):
    def test_branch_semantics_are_checked(self):
        for field, value, code in [
            (
                "quantity",
                {"kind": "simple", "amount": {"value": "1"}, "unit": "temperature.celsius"},
                "temperature-as-quantity",
            ),
            ("normalization", {"method": "extracted"}, "normalization-source-required"),
            ("id", "fat", "duplicate-ingredient-id"),
        ]:
            with self.subTest(field=field):
                body = choice_recipe()
                body["ingredient_sections"][0]["ingredients"][0]["alternatives"][0][field] = value
                self.assertIn(code, sref_reader.validate_recipe(body)[1].codes)

    def test_unknown_branch_members_are_version_scoped(self):
        body = choice_recipe()
        body["ingredient_sections"][0]["ingredients"][0]["alternatives"][0]["future_member"] = {
            "value": 1
        }
        self.assertIn("member-not-defined-by-version", sref_reader.validate_recipe(body)[1].codes)
        newer = copy.deepcopy(body)
        newer["sref"]["version"] = "0.4.1"
        self.assertFalse(sref_reader.validate_recipe(newer)[1].violations)

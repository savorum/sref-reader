"""Property tests that feed the reader hostile input.

The reader promises that no input makes it raise anything but `SrefError`, so
each test asserts that any outcome is either a result or that exception.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import pathlib
import sys
import unittest
import warnings
import zipfile

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import sref_reader
from sref_reader import SrefError

VALID = {
    "sref": {"version": "0.4.0", "unit_registry": "0.2.0"},
    "id": "soup",
    "title": "Soup",
    "ingredient_sections": [
        {
            "id": "i",
            "ingredients": [
                {
                    "id": "salt",
                    "name": "salt",
                    "quantity": {"kind": "simple", "amount": {"value": "1/2"}},
                }
            ],
        }
    ],
    "instruction_sections": [{"id": "m", "steps": [{"id": "s", "text": "Cook."}]}],
}

FUZZ = settings(
    max_examples=int(os.environ.get("SREF_FUZZ_EXAMPLES", "200")),
    deadline=None,
    derandomize=True,
    database=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**30), max_value=10**30),
    st.floats(allow_nan=False),
    st.text(max_size=40),
)
json_values = st.recursive(
    scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(max_size=12), children, max_size=4),
    ),
    max_leaves=25,
)


def paths(node, prefix=()):
    yield prefix
    if isinstance(node, dict):
        for key, child in node.items():
            yield from paths(child, (*prefix, key))
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from paths(child, (*prefix, index))


ALL_PATHS = [path for path in paths(VALID) if path]


@st.composite
def mutated_recipes(draw):
    body = copy.deepcopy(VALID)
    for _ in range(draw(st.integers(min_value=1, max_value=3))):
        *parents, last = draw(st.sampled_from(ALL_PATHS))
        target = body
        try:
            for step in parents:
                target = target[step]
            if draw(st.booleans()):
                del target[last]
            else:
                target[last] = draw(json_values)
        except (KeyError, IndexError, TypeError):
            continue
    return json.dumps(body).encode("utf-8")


member_names = st.one_of(
    st.sampled_from(
        [
            "manifest.json",
            "recipe.json",
            "assets/a",
            "../x",
            "/abs",
            "a//b",
            "a\\b",
            "assets/",
            "assets/a",
        ]
    ),
    st.text(max_size=20),
)


@st.composite
def archives(draw):
    buffer = io.BytesIO()
    with (
        warnings.catch_warnings(),
        zipfile.ZipFile(
            buffer, "w", draw(st.sampled_from([zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED]))
        ) as archive,
    ):
        warnings.simplefilter("ignore")
        for name in draw(st.lists(member_names, max_size=5)):
            with contextlib.suppress(ValueError, UnicodeEncodeError):
                archive.writestr(name, draw(st.binary(max_size=200)))
    return buffer.getvalue()


def refuses_or_reads(function, data):
    with contextlib.suppress(SrefError):
        function(data)


class HostileInput(unittest.TestCase):
    @FUZZ
    @given(st.binary(max_size=2000))
    def test_recipe_bytes(self, data):
        refuses_or_reads(sref_reader.read_recipe, data)

    @FUZZ
    @given(json_values)
    def test_recipe_json_values(self, value):
        refuses_or_reads(sref_reader.read_recipe, json.dumps(value).encode("utf-8"))

    @FUZZ
    @given(mutated_recipes())
    def test_mutated_valid_recipe(self, data):
        refuses_or_reads(sref_reader.read_recipe, data)

    @FUZZ
    @given(st.binary(max_size=2000))
    def test_package_bytes(self, data):
        refuses_or_reads(sref_reader.read_package, data)

    @FUZZ
    @given(archives())
    def test_package_archives(self, data):
        refuses_or_reads(sref_reader.read_package, data)

    @FUZZ
    @given(archives())
    def test_bundle_archives(self, data):
        refuses_or_reads(sref_reader.read_bundle, data)

    @FUZZ
    @given(st.integers(min_value=1, max_value=3000), st.sampled_from(["[", '{"a":']))
    def test_nesting(self, depth, opener):
        refuses_or_reads(sref_reader.read_recipe, (opener * depth).encode("utf-8"))


if __name__ == "__main__":
    unittest.main()

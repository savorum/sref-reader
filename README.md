# SREF Reader

A Python library that reads and validates SREF (Structured Recipe Exchange Format)
recipe JSON, `.sref` packages, and `.srefbundle` bundles, and converts amounts
between registered units exactly. It does not write recipes; SREF Writer is the
separate library for that.

## Install

Python 3.10 or later. From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install .
```

The only runtime dependency is `jsonschema`. The SREF schemas and unit registry
ship inside the distribution, so no specification checkout is needed at
runtime.

## Example

```python
import json
import sref_reader

recipe = sref_reader.read_recipe(
    json.dumps(
        {
            "sref": {"version": "0.4.0", "unit_registry": "0.2.0"},
            "id": "tea",
            "title": "Tea",
            "instruction_sections": [
                {
                    "id": "method",
                    "steps": [{"id": "steep", "text": "Steep the tea, then strain."}],
                }
            ],
        }
    ).encode("utf-8")
)
print(recipe["title"])
```

## Documentation

- [Usage](docs/usage.md): reading, diagnostics, unit conversion, streaming
  bundles, and resource limits.
- [Development](docs/development.md): checks, conformance, and testing the
  installed distribution.
- [Security assessment](docs/security-assessment.md): the risks reviewed and the
  controls and tests that cover them.

## Compatibility

`__version__` and `pyproject.toml` version this library.
`SUPPORTED_FORMAT` (`0.4.0`) and `SUPPORTED_REGISTRY` (`0.2.0`) name the SREF
and unit-registry versions it implements. The three numbers are independent.

The vendored SREF snapshot is loaded through `importlib.resources` and checked
against its manifest digests; altered bytes fail to load. Documents are checked
against the schema and then against the semantic and archive rules the schema
cannot express. A document from a compatible newer SREF version keeps its
unrecognized members and extensions, and no meaning is invented for them.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for reporting defects, changes, and the
Developer Certificate of Origin sign-off, and [GOVERNANCE.md](GOVERNANCE.md) for
roles.

## Licence

MIT, in [LICENSE.txt](LICENSE.txt). The vendored schemas and registry data keep
SREF's CC0 1.0 Universal dedication.

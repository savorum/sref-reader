# Development

Run commands from the repository root with Python 3.10 or later.

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install '.[dev]' build
python3 -m ruff check .
python3 -m ruff format --check .
python3 -m unittest discover -s tests -v
```

## Conformance

The library claims these capabilities for SREF 0.4.0 with unit registry 0.2.0:

| Capability | Status |
| --- | --- |
| JSON reader | claimed |
| Package reader | claimed |
| Bundle reader | claimed |
| Registry | claimed |
| Unit conversion | claimed |
| JSON, package, and bundle writer | not implemented (see SREF Writer) |
| Round trip | not claimed; it requires a writer |
| Ingredient-line parser | not implemented |

The fixture corpus lives in the SREF repository. With it checked out beside
this one:

```sh
python3 tools/conformance.py --sref ../sref
```

Report the SREF commit with the result. CI runs against the commit pinned in
its `SREF_COMMIT` variable, so a run against any other checkout is evidence for
that revision only. `SREF_COMMIT=<pinned commit> tools/drift.sh ../sref` reports
the SREF commits made since the pin.

## Installed distribution

`tools/installed.py` checks an installed copy of the library; it does not build
or install it. Build a wheel and install it into a clean environment:

```sh
python3 -m build --outdir dist .
python3 -m venv .installed
.installed/bin/python -m pip install dist/*.whl
```

Then run the check from an empty directory, so the checkout is not on the import
path:

```sh
mkdir -p /tmp/sref-installed && cd /tmp/sref-installed
/path/to/sref-reader/.installed/bin/python /path/to/sref-reader/tools/installed.py \
  --sref /path/to/sref
```

It loads every vendored snapshot file and runs one valid case per claimed
capability. It is a packaging smoke test, not a substitute for the conformance
run.

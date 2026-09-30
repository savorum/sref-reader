# Usage

## Read

```python
import sref_reader

recipe = sref_reader.read_recipe(open("cake.recipe.json", "rb").read())
package = sref_reader.read_package(open("cake.sref", "rb").read())
bundle = sref_reader.read_bundle(open("library.srefbundle", "rb").read())

print(recipe["title"])
print(package.assets.keys())
for member in bundle.members:
    print(member.member_id, member.package.recipe["title"])
```

Each `read_*` function raises `SrefError`, which carries every violation found.
Each has a `validate_*` counterpart that returns the document and a report
instead of raising:

```python
data = open("cake.recipe.json", "rb").read()
document, report = sref_reader.validate_recipe(data)
for violation in report.violations:
    print(violation.category, violation.requirement_id, violation.path, violation.message)
```

## Diagnostics

A violation has two identifiers:

- `category` is one of SREF's seven portable failure categories. Branch on it.
- `requirement_id` names the specific rule this reader enforced. It is
  diagnostic detail and is not portable between implementations. `code` is an
  alias for it.

## What a recipe must contain

A recipe needs `ingredient_sections`, `instruction_sections`, or both, and any
section array present must be nonempty. A recipe that has only one of them is
read as it is, with nothing added. A record with neither is refused.

An ingredient may be a choice: a list of complete `alternatives`, each with its
own ID, name, quantity, and modifiers, joined by a `relation` of `or`,
`and_or`, or a preferred substitution. The choice and each branch keep their own
notes and optionality. A step may refer to the choice as a whole, or to one
branch with that branch's amount. The reader never selects a branch or infers
that ingredients are interchangeable.

## Units

```python
registry = sref_reader.unit_registry()
registry.convert_amount({"value": "1"}, "volume.cup.us.customary", "volume.milliliter")
# {'value': '473176473/2000000'}
```

Conversion uses exact rational arithmetic. The result is marked `approximate`
only when a unit definition on the conversion path is itself approximate.
Converting between mass and volume raises an error, because it depends on the
density of a particular ingredient.

The reader does not parse ingredient text, so it does not resolve bare unit
words such as `teaspoon`. Unit IDs in a document are read exactly as stored.

## Stream a bundle

`read_bundle` keeps every decoded member and its assets in memory.
`bundle_members` yields one decoded package at a time:

```python
data = open("library.srefbundle", "rb").read()
for member in sref_reader.bundle_members(data):
    print(member.recipe_id, member.package.recipe["title"])
```

The iterator validates members in order and raises `SrefError` at the first
invalid one. It keeps the archive bytes but not earlier members; memory stays
bounded only if the caller also discards each member after use. The check for
undeclared entries runs when the iterator is exhausted, so stopping early leaves
the rest of the bundle unvalidated.

## Resource limits

The SREF specification requires readers to enforce finite limits and leaves the
values to the implementation. Defaults are in
[`limits.py`](../sref_reader/limits.py). Pass a `Limits` value to change them:

```python
sref_reader.read_package(data, sref_reader.Limits(archive_bytes=8 << 20, entries=64))
```

The limits apply to the whole operation. A bundle and every package inside it
draw expanded bytes, entries, and members from one shared allowance, so a
bundle cannot multiply the budget by nesting packages.

Exceeding a limit is reported like any other violation. Every limit reports the
`resource-limit` category, which tells a caller that its own ceiling was reached
rather than that the artifact is malformed:

| Limit | Requirement ID |
| --- | --- |
| `json_bytes` | `json-bytes-limit` |
| `json_depth` | `json-depth-limit` |
| `archive_bytes`, `bundle_bytes` | `archive-bytes-limit` |
| `bytes_per_entry` | `entry-bytes-limit` |
| `compression_ratio` | `compression-ratio-limit` |
| `expanded_bytes` | `expanded-bytes-limit` |
| `entries` | `entry-count-limit` |
| `members` | `member-count-limit` |

`json_depth` bounds how deeply arrays and objects nest in a recipe, a package's
`recipe.json`, and a manifest. Nesting is measured before the document is
decoded, so a few kilobytes of `[` cannot exhaust the interpreter's stack, and a
document already decoded by the caller is measured the same way.

An archive path longer than `path_length` is refused as an unsafe archive path
(`unsafe-archive`), not as a resource limit.

## Choices the specification leaves open

The corpus is the proof that this reader follows the specification. Where the
specification names a standard without settling a point, the reader makes a
choice, and the SREF Writer makes the same one. Another implementation may
choose differently and still conform.

- **ISO 8601 durations.** A duration is `P` followed by years, months, weeks,
  and days in that order, then optionally `T` and hours, minutes, and seconds. A
  fraction is accepted on seconds only, with a period and never a comma. `P1W`
  and `P1Y2M` are accepted. `P1.5D`, `PT0,5H`, `P`, and `PT` are refused.

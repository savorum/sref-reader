"""A reference SREF reader.

Reads SREF 0.4.0 recipe documents, packages, and bundles, validates them
structurally and semantically, and rejects invalid inputs. Writing is
intentionally excluded to ensure an independent reader implementation.

    import sref_reader

    recipe = sref_reader.read_recipe(open("cake.recipe.json", "rb").read())
    package = sref_reader.read_package(open("cake.sref", "rb").read())
    bundle = sref_reader.read_bundle(open("library.srefbundle", "rb").read())

`read_bundle` returns every member at once. `bundle_members` yields them one at
a time for a caller who does not want a library's worth of photographs in
memory to write them somewhere else.

Each `read_*` raises `SrefError` carrying every violation found. When you want
the failures rather than an exception, call the matching `validate_*`, which
returns the document alongside a report:

    document, report = sref_reader.validate_recipe(data)
    for violation in report.violations:
        print(violation.category, violation.requirement_id, violation.path, violation.message)

Unknown data is preserved rather than dropped. A document from a compatible
newer release keeps its unrecognized standard members and its extensions,
because the caller may well need to write them back out.
"""

from __future__ import annotations

from .bundle import Bundle, Member
from .bundle import members as bundle_members
from .bundle import read as read_bundle
from .bundle import validate as validate_bundle
from .errors import FAILURE_CATEGORIES, Report, SrefError, UnsupportedVersionError, Violation
from .limits import DEFAULT as DEFAULT_LIMITS
from .limits import Limits
from .package import Package
from .package import read as read_package
from .package import validate as validate_package
from .recipe import read as read_recipe
from .recipe import validate as validate_recipe
from .registry import (
    IncompatibleDimensionError,
    IncompatibleUnitsError,
    NonphysicalUnitError,
    Registry,
    Unit,
    UnknownUnitError,
)
from .versions import SUPPORTED_FORMAT, SUPPORTED_REGISTRY

#: The distribution version, which is not the SREF version it implements.
__version__ = "0.4.0"

#: The conformance capabilities this implementation claims, in the vocabulary
#: of the specification's section 22. Round-trip conformance is deliberately
#: absent: it requires a writer, and this library has none.
CAPABILITIES = (
    "json-reader",
    "package-reader",
    "bundle-reader",
    "registry",
    "unit-conversion",
)


def unit_registry() -> Registry:
    """The unit registry this build implements, already validated.

    Not named `registry`, which is the submodule.
    """
    from . import registry as registry_module
    from . import snapshot

    report = Report()
    catalog = registry_module.load(snapshot.units(), report)
    if catalog is None:  # pragma: no cover - the pinned snapshot is verified
        raise SrefError(report.violations)
    return catalog


__all__ = [
    "CAPABILITIES",
    "DEFAULT_LIMITS",
    "FAILURE_CATEGORIES",
    "SUPPORTED_FORMAT",
    "SUPPORTED_REGISTRY",
    "Bundle",
    "IncompatibleDimensionError",
    "IncompatibleUnitsError",
    "Limits",
    "Member",
    "NonphysicalUnitError",
    "Package",
    "Registry",
    "Report",
    "SrefError",
    "Unit",
    "UnknownUnitError",
    "UnsupportedVersionError",
    "Violation",
    "__version__",
    "bundle_members",
    "read_bundle",
    "read_package",
    "read_recipe",
    "unit_registry",
    "validate_bundle",
    "validate_package",
    "validate_recipe",
]

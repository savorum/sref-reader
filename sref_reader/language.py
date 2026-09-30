"""BCP 47 well-formedness (specification section 6.3).

JSON Schema cannot validate a language tag, so a semantic validator must. This
checks well-formedness only, not IANA registration, which SREF does not
require.
"""

from __future__ import annotations

import re

_LANGTAG = re.compile(
    r"""^
    (?:
      (?:[A-Za-z]{2,3}(?:-[A-Za-z]{3}){0,3}|[A-Za-z]{4}|[A-Za-z]{5,8})   # language
      (?:-[A-Za-z]{4})?                                                   # script
      (?:-(?:[A-Za-z]{2}|[0-9]{3}))?                                      # region
      (?:-(?:[A-Za-z0-9]{5,8}|[0-9][A-Za-z0-9]{3}))*                      # variants
      (?:-[0-9A-WY-Za-wy-z](?:-[A-Za-z0-9]{2,8})+)*                       # extensions
      (?:-x(?:-[A-Za-z0-9]{1,8})+)?                                       # private use
      |x(?:-[A-Za-z0-9]{1,8})+                                            # private use only
    )
    $""",
    re.VERBOSE,
)

#: Tags RFC 5646 grandfathered in. They predate the current grammar and several
#: do not match it, so they are listed rather than parsed.
_GRANDFATHERED = {
    tag.casefold()
    for tag in (
        "art-lojban",
        "cel-gaulish",
        "en-GB-oed",
        "i-ami",
        "i-bnn",
        "i-default",
        "i-enochian",
        "i-hak",
        "i-klingon",
        "i-lux",
        "i-mingo",
        "i-navajo",
        "i-pwn",
        "i-tao",
        "i-tay",
        "i-tsu",
        "no-bok",
        "no-nyn",
        "sgn-BE-FR",
        "sgn-BE-NL",
        "sgn-CH-DE",
        "zh-guoyu",
        "zh-hakka",
        "zh-min",
        "zh-min-nan",
        "zh-xiang",
    )
}


def well_formed_bcp47(tag: object) -> bool:
    if not isinstance(tag, str) or not tag:
        return False
    if tag.casefold() in _GRANDFATHERED:
        return True
    if not _LANGTAG.match(tag):
        return False
    # Duplicate singleton extensions would make the tag ambiguous.
    parts = tag.split("-")
    singletons = [part for part in parts if len(part) == 1]
    return len(singletons) == len({part.casefold() for part in singletons})

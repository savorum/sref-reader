"""Canonical rational amounts (specification section 7).

SREF serializes normalized numbers as strings so that an exchanged amount is
exactly the amount that was authored. A third of a cup is `1/3`, not
0.3333333333333333, and no reader has to guess how much precision the writer
believed it had.

Canonical form is `0`, a positive integer, or a reduced positive fraction with
a denominator greater than one, so equal values have one spelling. `2/4`,
`3/1`, `00`, `+1`, `1.5` and `0/7` are all invalid.
"""

from __future__ import annotations

import re
from fractions import Fraction

#: Both parts must fit in an unsigned 64-bit integer (section 7). Larger values
#: MAY be supported; this reader declines them so that arithmetic stays bounded
#: on untrusted input.
UINT64_MAX = 2**64 - 1

_UNSIGNED = re.compile(r"^(?:0|[1-9][0-9]*|[1-9][0-9]*/[1-9][0-9]*)$")


class NotCanonicalError(ValueError):
    """A rational string that is not in SREF's canonical form.

    `code` names the detailed conformance requirement, because the specification
    distinguishes a value whose *syntax* is wrong from one whose syntax is fine
    but which is unreduced or out of range.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def parse(text: object, *, signed: bool = False) -> Fraction:
    """Return the exact value of a canonical rational string.

    `signed` permits the leading minus temperatures may carry. Negative zero is
    never canonical, so `-0` is rejected rather than normalized to zero.
    """
    if not isinstance(text, str):
        raise NotCanonicalError("noncanonical-rational-syntax", "rational must be a string")
    body = text
    if signed and body.startswith("-"):
        if body == "-0":
            raise NotCanonicalError("negative-zero-temperature", "negative zero is not canonical")
        body = body[1:]
    if not _UNSIGNED.match(body):
        raise NotCanonicalError(
            "noncanonical-rational-syntax",
            f"{text!r} is not a canonical rational",
        )

    numerator, _, denominator = body.partition("/")
    if _exceeds_uint64(numerator) or (denominator and _exceeds_uint64(denominator)):
        raise NotCanonicalError(
            "rational-out-of-range",
            f"{text!r} exceeds the interoperability bound",
        )
    if not denominator:
        value = Fraction(int(numerator))
    else:
        if int(denominator) <= 1:
            raise NotCanonicalError(
                "noncanonical-denominator",
                "a fraction denominator must exceed one",
            )
        value = Fraction(int(numerator), int(denominator))
        if value.denominator != int(denominator):
            raise NotCanonicalError("unreduced-rational", f"{text!r} is not reduced")
    return -value if text.startswith("-") else value


def is_canonical(text: object, *, signed: bool = False) -> bool:
    try:
        parse(text, signed=signed)
    except NotCanonicalError:
        return False
    return True


def to_string(value: Fraction) -> str:
    """Render an exact value in canonical form."""
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value.denominator == 1:
        return f"{sign}{value.numerator}"
    return f"{sign}{value.numerator}/{value.denominator}"


def _exceeds_uint64(digits: str) -> bool:
    # Compared as text so that an absurdly long numeral is rejected before it
    # is turned into an integer.
    return len(digits) > 20 or (len(digits) == 20 and int(digits) > UINT64_MAX)

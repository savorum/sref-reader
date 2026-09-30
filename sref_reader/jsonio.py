"""Strict JSON reading (specification section 4).

Python's `json` module is more permissive than SREF allows in three ways that
matter, and each of them is a silent change of meaning rather than a parse
error:

- it keeps the *last* of a set of duplicate object members, which can change an
  identifier, a digest, or a quantity without any complaint;
- it accepts `NaN`, `Infinity` and `-Infinity`, which are not JSON at all, and
  turns a number too large for a float, such as `1e400`, into infinity; and
- it accepts a byte-order mark and trailing content after the top-level value.

So the document is decoded once with hooks that catch the first two, and the
raw text is checked for the rest. Readers MUST reject duplicate member names,
and a reader that merely tolerates them is not conforming.
"""

from __future__ import annotations

import json
import math
import re
from typing import TYPE_CHECKING, Any

from .limits import DEFAULT as _DEFAULT_LIMITS

if TYPE_CHECKING:
    from .errors import Report

#: A document is decoded as text; these are the C0 characters that may never
#: appear unescaped in a JSON string SREF accepts (section 6.2 permits tab,
#: line feed and carriage return only where the schema allows them).
_FORBIDDEN_TEXT = {chr(code) for code in range(0x20)} - {"\t", "\n", "\r"}


class JSONRejectedError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


#: A string literal, or one bracket outside any string.
_STRUCTURE = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|[\[\]{}]')


def nesting_depth(text: str, limit: int) -> int:
    """How deeply `text` nests arrays and objects, counting no further than `limit + 1`.

    This reads brackets without recursing, so it is safe on the input that would
    exhaust the stack of a recursive decoder. Text that is not valid JSON may be
    measured loosely; the decoder rejects it afterwards.
    """
    depth = 0
    for token in _STRUCTURE.finditer(text):
        bracket = token.group()
        if bracket in "[{":
            depth += 1
            if depth > limit:
                return depth
        elif bracket in "]}" and depth:
            depth -= 1
    return depth


def value_depth(value: Any, limit: int) -> int:
    """How deeply a decoded value nests, counting no further than `limit + 1`."""
    deepest = 0
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, dict):
            children = item.values()
        elif isinstance(item, list):
            children = item
        else:
            continue
        deepest = max(deepest, depth)
        if deepest > limit:
            return deepest
        pending.extend((child, depth + 1) for child in children)
    return deepest


def depth_message(limit: int) -> str:
    return f"JSON nests deeper than the reader's limit of {limit} levels"


def loads(
    data: bytes | str,
    *,
    max_bytes: int | None = None,
    max_depth: int = _DEFAULT_LIMITS.json_depth,
) -> Any:
    """Decode one JSON value under SREF's reading rules.

    Raises JSONRejectedError with the detailed requirement identifier. The
    public report projects that detail onto the normative failure category.
    """
    if isinstance(data, bytes):
        if max_bytes is not None and len(data) > max_bytes:
            raise JSONRejectedError(
                "malformed-package-json",
                "JSON exceeds the reader's size limit",
            )
        if data.startswith(b"\xef\xbb\xbf"):
            raise JSONRejectedError("malformed-package-json", "a byte-order mark is not permitted")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise JSONRejectedError("malformed-package-json", f"not valid UTF-8: {exc}") from exc
    else:
        text = data
        if max_bytes is not None and len(text.encode("utf-8")) > max_bytes:
            raise JSONRejectedError(
                "malformed-package-json",
                "JSON exceeds the reader's size limit",
            )

    if nesting_depth(text, max_depth) > max_depth:
        raise JSONRejectedError("json-depth-limit", depth_message(max_depth))

    decoder = json.JSONDecoder(
        object_pairs_hook=_reject_duplicates,
        parse_constant=_reject_constant,
        parse_float=_parse_finite_float,
    )
    try:
        value, end = decoder.raw_decode(text.lstrip())
    except JSONRejectedError:
        raise
    except RecursionError as exc:
        # The measurement above is the intended guard. This is what stands
        # between a hostile file and an uncaught error if it ever misses.
        raise JSONRejectedError("json-depth-limit", depth_message(max_depth)) from exc
    except ValueError as exc:
        raise JSONRejectedError("malformed-package-json", f"malformed JSON: {exc}") from exc

    # A second top-level value is a second document, not trailing content.
    remainder = text.lstrip()[end:].strip()
    if remainder:
        raise JSONRejectedError(
            "trailing-json-value",
            "a second top-level JSON value follows the first",
        )
    return value


def check_control_characters(value: Any, report: Report, path: str = "") -> None:
    """Reject C0 control characters carried inside decoded strings.

    Escaped or not, `\\u0007` in a title is not text a recipe should carry, and
    accepting it lets a document smuggle terminal control sequences through
    every consumer that prints it.
    """
    if isinstance(value, str):
        if any(character in _FORBIDDEN_TEXT for character in value):
            report.add(
                "prohibited-control-character",
                "text contains a prohibited control character",
                path,
            )
    elif isinstance(value, dict):
        for name, member in value.items():
            check_control_characters(name, report, f"{path}.{name}" if path else name)
            check_control_characters(member, report, f"{path}.{name}" if path else name)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            check_control_characters(item, report, f"{path}[{index}]")


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    for name, _ in pairs:
        if name in seen:
            raise JSONRejectedError("duplicate-json-member", f"duplicate object member {name!r}")
        seen.add(name)
    return dict(pairs)


def _reject_constant(name: str) -> Any:
    raise JSONRejectedError("nonfinite-json-number", f"{name} is not a JSON number")


def _parse_finite_float(literal: str) -> float:
    value = float(literal)
    if not math.isfinite(value):
        raise JSONRejectedError("nonfinite-json-number", f"{literal} does not fit a finite number")
    return value

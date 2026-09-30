"""One operation's resource allowance, spent once.

It is created once per `read_*` or `validate_*` call and carried down into
every nested package, so a bundle's members share one allowance rather than
each receiving it afresh. When it runs out it stays out.

`Limits` remains what the caller sets: policy, immutable, shared between
operations. This is what one operation has left, which is a different thing and
belongs in a different object.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .limits import DEFAULT, Limits


class ExhaustedError(Exception):
    """The operation asked for more than the caller allowed.

    Carries the detailed requirement identifier for the ceiling it crossed.
    Every one projects to the normative `resource-limit` category.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass
class _Spent:
    """The running totals. One of these per operation, shared by reference."""

    expanded: int = 0
    entries: int = 0
    members: int = 0


@dataclass
class Budget:
    """What the operation has left, and where the reader is while spending it."""

    limits: Limits = DEFAULT
    #: Where in the operation the reader currently is. Without it a nested
    #: failure names `manifest.json` without saying whose.
    location: str = ""
    spent: _Spent = field(default_factory=_Spent)

    def spend_expanded(self, count: int, name: str) -> None:
        self.spent.expanded += count
        if self.spent.expanded > self.limits.expanded_bytes:
            raise ExhaustedError(
                "expanded-bytes-limit",
                f"{self.where(name)} expands beyond the reader's total limit for this operation",
            )

    def spend_entries(self, count: int) -> None:
        self.spent.entries += count
        if self.spent.entries > self.limits.entries:
            raise ExhaustedError(
                "entry-count-limit",
                "this operation declares more entries than the reader accepts",
            )

    def spend_members(self, count: int) -> None:
        self.spent.members += count
        if self.spent.members > self.limits.members:
            raise ExhaustedError(
                "member-count-limit",
                "this operation declares more members than the reader accepts",
            )

    def entered(self, location: str) -> Budget:
        """The same allowance, further in.

        The totals object is shared rather than copied, so a nested package
        spends the operation's remaining allowance and not one of its own.
        """
        return Budget(limits=self.limits, location=self.where(location), spent=self.spent)

    def where(self, name: str) -> str:
        return f"{self.location}:{name}" if self.location else name


def for_operation(limits: Limits | None) -> Budget:
    """The budget a top-level `read_*` or `validate_*` starts with."""
    return Budget(limits=limits if limits is not None else DEFAULT)

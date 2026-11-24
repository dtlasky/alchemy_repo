from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Self


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


@dataclass
class SortOrder:
    column: str
    direction: SortDirection | None = None

    def __post_init__(self) -> None:
        if not self.column:
            raise ValueError("Sort column must not be empty")

    @classmethod
    def parse(cls, value: str) -> Self:
        column, _, direction = value.partition(":")
        return cls(column=column, direction=SortDirection(direction) if direction else None)


@dataclass
class PageRequest:
    page: int = 0
    size: int = 20
    sort: Sequence[SortOrder] = field(default_factory=list)

    def __post_init__(self) -> None:
        _validate_page_bounds(self.page, self.size)


@dataclass
class Page[PageItem]:
    items: Sequence[PageItem]
    page: int
    size: int
    total: int

    def __post_init__(self) -> None:
        _validate_page_bounds(self.page, self.size)
        if self.total < 0:
            raise ValueError("Total must be non-negative")

    @property
    def total_pages(self) -> int:
        return (self.total + self.size - 1) // self.size

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages - 1

    @property
    def has_previous(self) -> bool:
        return self.page > 0


@dataclass
class CursorPage[PageItem]:
    items: Sequence[PageItem]
    next_cursor: tuple[object, ...] | None = None

    @property
    def has_next(self) -> bool:
        return self.next_cursor is not None


def _validate_page_bounds(page: int, size: int) -> None:
    if page < 0:
        raise ValueError("Page must be non-negative")
    if size < 1:
        raise ValueError("Page size must be positive")

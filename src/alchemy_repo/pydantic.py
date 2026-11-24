from collections.abc import Sequence
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field


from .page import PageRequest as CorePageRequest
from .page import SortDirection
from .page import SortOrder as CoreSortOrder

__all__ = ["Page", "PageRequest", "SortDirection", "SortOrder"]


class SortOrder(BaseModel):
    column: str = Field(min_length=1)
    direction: SortDirection | None = None

    @classmethod
    def parse(cls, value: str) -> Self:
        column, _, direction = value.partition(':')
        return cls.model_validate({"column": column, "direction": direction or None})


class PageRequest(BaseModel):
    page: int = Field(default=0, ge=0)
    size: int = Field(default=20, ge=1)
    sort: list[SortOrder] = Field(default_factory=list)


    def to_request(self) -> CorePageRequest:
        return CorePageRequest(
            page=self.page,
            size=self.size,
            sort=[CoreSortOrder(order.column, order.direction) for order in self.sort],
        )


class Page[PageItem](BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, from_attributes=True)

    items: Sequence[PageItem]
    page: int = Field(ge=0)
    size: int = Field(ge=1)
    total: int = Field(ge=0)

    @computed_field
    @property
    def total_pages(self) -> int:
        return (self.total + self.size - 1) // self.size

    @computed_field
    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages - 1

    @computed_field
    @property
    def has_previous(self) -> bool:
        return self.page > 0

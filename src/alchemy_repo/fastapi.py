from typing import Annotated

from fastapi import Depends, Query
from fastapi.exceptions import RequestValidationError

from .page import PageRequest, SortOrder

__all__ = ["PageRequestDepends", "build_page_request"]


async def build_page_request(
        page: Annotated[int, Query(ge=0, description="Zero-based page index.")] = 0,
        size: Annotated[int, Query(ge=1, le=100, description="Items per page.")] = 20,
        sort: Annotated[
            list[str] | None,
            Query(description="Repeat for each sort column. Use column or column:asc/desc."),
        ] = None,
) -> PageRequest:
    """Build a repository page request from FastAPI query parameters."""
    orders: list[SortOrder] = []
    for index, value in enumerate(sort or ()):
        try:
            orders.append(SortOrder.parse(value))
        except ValueError as exc:
            raise RequestValidationError([{
                "type": "value_error",
                "loc": ("query", "sort", index),
                "msg": "Expected a column name with an optional :asc or :desc suffix.",
                "input": value,
            }]) from exc

    return PageRequest(page=page, size=size, sort=orders)


PageRequestDepends = Annotated[PageRequest, Depends(build_page_request)]

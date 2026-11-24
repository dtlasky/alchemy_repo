from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, func, inspect, select
from sqlalchemy.sql import ColumnElement

from .entity import resolve_column
from .page import PageRequest, SortDirection, SortOrder


__all__ = ["count_query", "map_sort_orders", "page_query"]


def _resolve_sort_order_column(
        stmt: Select[*tuple[Any, ...]],
        sort_order: SortOrder,
) -> ColumnElement[Any]:
    if '.' in sort_order.column:
        entity_name, _, column_name = sort_order.column.partition('.')

        for description in stmt.column_descriptions:
            if (entity := description.get('entity')) is not None:
                inspection = inspect(entity)
                if inspection.is_aliased_class:
                    if inspection.name == entity_name:
                        return resolve_column(inspection.selectable.c, column_name)
                    continue
                if (inspection.local_table.name == entity_name
                        or inspection.class_.__name__.lower() == entity_name.lower()):
                    return resolve_column(inspection.columns, column_name)

        raise ValueError(f'Unknown sort entity "{entity_name}"')

    return resolve_column(stmt.selected_columns, sort_order.column)


def map_sort_orders(
        stmt: Select[*tuple[Any, ...]],
        sort_orders: Sequence[SortOrder]
) -> list[ColumnElement[Any]]:
    sorts: list[ColumnElement[Any]] = []

    for sort_order in sort_orders:
        column = _resolve_sort_order_column(stmt, sort_order)

        match sort_order.direction:
            case SortDirection.ASC:
                sorts.append(column.asc())
            case SortDirection.DESC:
                sorts.append(column.desc())
            case _:
                sorts.append(column)

    return sorts


def count_query(stmt: Select[*tuple[Any, ...]]) -> Select[int]:
    return select(func.count()).select_from(stmt.subquery())


def page_query[*Columns](
        stmt: Select[*Columns],
        page_request: PageRequest
) -> Select[*Columns]:
    return stmt.offset(page_request.page * page_request.size).limit(page_request.size)

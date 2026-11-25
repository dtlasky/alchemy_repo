from dataclasses import dataclass
from typing import Any, overload

from sqlalchemy import Column, ColumnCollection, ColumnElement, Table, UniqueConstraint, inspect
from sqlalchemy.orm import ColumnProperty, InstrumentedAttribute, MappedColumn, Mapper
from sqlalchemy.types import TypeEngine

__all__ = ["ColumnInfo", "column_info", 'ColumnLikeType', 'column_name', 'resolve_column']

type ColumnLikeType = InstrumentedAttribute[Any] | MappedColumn[Any] | Column[Any]


@dataclass(frozen=True)
class ColumnInfo:
    """Declared metadata for a mapped database column."""

    key: str
    column: Column[Any]
    is_unique: bool

    @property
    def name(self) -> str:
        return self.column.name

    @property
    def is_primary_key(self) -> bool:
        return self.column.primary_key

    @property
    def nullable(self) -> bool | None:
        return self.column.nullable

    @property
    def sql_type(self) -> TypeEngine[Any]:
        return self.column.type


@overload
def column_info(entity: type[object], name: str) -> ColumnInfo: ...


@overload
def column_info(entity: InstrumentedAttribute[Any], /) -> ColumnInfo: ...


def column_info(
        entity: type[object] | InstrumentedAttribute[Any],
        name: str | None = None,
) -> ColumnInfo:
    """Inspect an ORM attribute by its mapped name or by attribute reference."""
    if isinstance(entity, InstrumentedAttribute):
        if name is not None:
            raise TypeError("Pass either a mapped attribute or a model and attribute name")
        prop = entity.property
        key = entity.key
    elif name is not None:
        mapper = inspect(entity, raiseerr=False)
        if not isinstance(mapper, Mapper):
            raise TypeError("Expected a mapped ORM class")
        prop = mapper.attrs.get(name)
        key = name
    else:
        raise TypeError("An attribute name is required when inspecting a model")

    if not isinstance(prop, ColumnProperty) or len(prop.columns) != 1:
        raise ValueError(f"{key!r} does not map to a single database column")
    column = prop.columns[0]
    if not isinstance(column, Column) or not isinstance(column.table, Table):
        raise ValueError(f"{key!r} does not map to a database table column")

    return ColumnInfo(key=key, column=column, is_unique=_is_unique(column, column.table))


def _is_unique(column: Column[Any], table: Table) -> bool:
    if column.unique or (column.primary_key and len(table.primary_key.columns) == 1):
        return True

    for constraint in table.constraints:
        if (isinstance(constraint, UniqueConstraint)
                and len(constraint.columns) == 1
                and constraint.columns.contains_column(column)):
            return True

    for index in table.indexes:
        if (index.unique and len(index.expressions) == 1
                and index.expressions[0] is column
                and not any(key.endswith("_where") and value is not None
                            for key, value in index.dialect_kwargs.items())):
            return True

    return False


def column_name(mapper: Mapper[Any], column: str | ColumnLikeType) -> str:
    match column:
        case str(value):
            return value
        case InstrumentedAttribute() as value:
            return value.key
        case MappedColumn() as value:
            return mapper.get_property_by_column(value.column).key
        case Column() as value:
            return mapper.get_property_by_column(value).key
        case _:
            raise TypeError(f'Unsupported column type: {type(column)!r}')


def resolve_column(
        columns: ColumnCollection[str, ColumnElement[Any]], name: str
) -> ColumnElement[Any]:
    if (column := columns.get(name)) is not None:
        return column
    raise ValueError(f'Unknown column "{name}"')

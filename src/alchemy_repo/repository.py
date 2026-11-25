from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar, overload

from sqlalchemy import CursorResult, Delete, Insert, Result, ScalarResult, Select, Update
from sqlalchemy import and_, delete, inspect, insert, or_, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql import Executable
from sqlalchemy.sql.selectable import SelectBase, TypedReturnsRows

from .entity import ColumnLikeType, column_name
from .page import CursorPage, Page, PageRequest
from .pagination import count_query, map_sort_orders, page_query


class BaseRepository[EntityType: DeclarativeBase]:
    """Async repository using a caller-owned SQLAlchemy session."""

    audit_timestamps: ClassVar[tuple[str, str] | None] = None  # (created_at, updated_at)

    def __init__(self, entity_type: type[EntityType], session: AsyncSession):
        self._entity_type = entity_type
        self._session = session

    # CRUD
    def insert_model(self) -> Insert:
        match self._session.get_bind(mapper=self._entity_type).dialect.name:
            case 'postgresql':
                return postgresql.insert(self._entity_type)
            case 'sqlite':
                return sqlite.insert(self._entity_type)
            case _:
                return insert(self._entity_type)

    def select_model(self) -> Select[EntityType]:
        return select(self._entity_type)

    def update_model(self) -> Update:
        return update(self._entity_type)

    def delete_model(self) -> Delete:
        return delete(self._entity_type)

    async def get_by_pk(self, identity: object) -> EntityType | None:
        """Look up a model by scalar, tuple, or attribute-name dictionary identity."""
        return await self._session.get(self._entity_type, identity)

    # Results
    @overload
    async def execute[*Columns](self, stmt: TypedReturnsRows[*Columns]) -> Result[*Columns]:
        ...

    @overload
    async def execute(self, stmt: Executable) -> Result[*tuple[Any, ...]]:
        ...

    async def execute(self, stmt: Executable) -> Result[*tuple[Any, ...]]:
        return await self._session.execute(stmt)

    async def scalar[Value, *Columns](self, stmt: Select[Value, *Columns]) -> Value | None:
        """Return the first column of the only row, or None if there is no row."""
        result = await self.execute(stmt)
        return result.scalar_one_or_none()

    async def scalars[Value, *Columns](self, stmt: Select[Value, *Columns]) -> ScalarResult[Value]:
        """Return the first column of each row."""
        result = await self.execute(stmt)
        return result.scalars()

    async def exists(self, stmt: SelectBase) -> bool:
        """Return whether the statement produces at least one row."""
        return await self.scalar(select(1).where(stmt.exists())) is not None

    async def upsert[Key: str | ColumnLikeType](
            self,
            data: Mapping[Key, object],
            index_elements: Sequence[ColumnLikeType],
            update_none: bool = False,
    ) -> None:
        """Insert a row or update the columns provided for a matching key."""
        mapper = inspect(self._entity_type)
        insert_data = {column_name(mapper, column): value for column, value in data.items()}
        index_names = [column_name(mapper, column) for column in index_elements]
        if not index_names or any(name not in insert_data for name in index_names):
            raise ValueError("Upsert requires values for every conflict key column")

        update_data = {
            column: value for column, value in insert_data.items()
            if column not in index_names and (update_none or value is not None)
        }
        if self.audit_timestamps is not None:
            created_at, updated_at = self.audit_timestamps
            timestamp = datetime.now(UTC)
            insert_data.setdefault(created_at, timestamp)
            update_data.setdefault(updated_at, timestamp)

        insert_stmt = self.insert_model().values(**insert_data)
        conflict_columns = [getattr(self._entity_type, name) for name in index_names]
        if update_none and update_data and isinstance(insert_stmt, (postgresql.Insert, sqlite.Insert)):
            update_values = {getattr(self._entity_type, name): value for name, value in update_data.items()}
            await self.execute(insert_stmt.on_conflict_do_update(index_elements=conflict_columns, set_=update_values))
            return

        predicates = [column == insert_data[name] for column, name in zip(conflict_columns, index_names)]
        if update_data:
            stmt = self.update_model().where(*predicates).values(**update_data)
            result = await self.execute(stmt)
            if isinstance(result, CursorResult) and result.rowcount >= 0:
                if result.rowcount > 0:
                    return
                await self.execute(insert_stmt)
                return

        match_query = select(1).select_from(self._entity_type).where(*predicates).limit(1)
        if await self.scalar(match_query) is not None:
            return

        await self.execute(insert_stmt)

    # Save
    async def save(self, entity: EntityType) -> EntityType:
        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)
        return entity

    async def merge(self, entity: EntityType) -> EntityType:
        entity = await self._session.merge(entity)
        await self._session.flush()
        await self._session.refresh(entity)
        return entity

    async def remove(self, entity: EntityType) -> None:
        await self._session.delete(entity)
        await self._session.flush()

    # Sorts
    async def apply_sorts(
            self,
            page_request: PageRequest,
            stmt: Select[EntityType] | None = None
    ) -> Select[EntityType]:
        base_stmt = stmt if stmt is not None else self.select_model()
        sort_orders = map_sort_orders(base_stmt, page_request.sort)
        return base_stmt.order_by(*sort_orders)

    # Pages
    async def get_page(
            self,
            page_request: PageRequest,
            stmt: Select[EntityType] | None = None
    ) -> Page[EntityType]:
        base_stmt = stmt if stmt is not None else self.select_model()
        count_stmt = count_query(base_stmt)
        data_stmt = page_query(await self.apply_sorts(page_request, base_stmt), page_request)

        total = await self.scalar(count_stmt)
        result = await self.scalars(data_stmt)

        return Page[EntityType](
            items=result.all(),
            page=page_request.page,
            size=page_request.size,
            total=total or 0
        )

    async def get_cursor_page(
            self,
            stmt: Select[EntityType] | None = None,
            *,
            size: int = 20,
            after: tuple[object, ...] | None = None,
    ) -> CursorPage[EntityType]:
        """Read a page in ascending mapped primary-key order without counting rows."""
        if size < 1:
            raise ValueError("Page size must be positive")

        query = stmt if stmt is not None else self.select_model()
        descriptions = query.column_descriptions
        if len(descriptions) != 1 or descriptions[0]["expr"] is not self._entity_type:
            raise ValueError("Cursor pagination requires a select of the repository model")

        primary_key = tuple(inspect(self._entity_type).primary_key)
        if after is not None:
            if len(after) != len(primary_key) or any(value is None for value in after):
                raise ValueError("Cursor must contain one non-null value per primary-key column")

            # Expand the lexicographic comparison for dialects without tuple inequalities.
            after_key = primary_key[-1] > after[-1]
            for column, value in reversed(tuple(zip(primary_key[:-1], after[:-1]))):
                after_key = or_(column > value, and_(column == value, after_key))
            query = query.where(after_key)

        query = query.order_by(None).order_by(*primary_key).offset(None).limit(size + 1)
        rows = (await self.scalars(query)).all()
        items = rows[:size]
        next_cursor = inspect(items[-1]).identity if len(rows) > size else None
        return CursorPage(items=items, next_cursor=next_cursor)

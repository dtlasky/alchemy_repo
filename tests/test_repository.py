from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import DateTime, UniqueConstraint, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, aliased, mapped_column

from alchemy_repo import BaseRepository, PageRequest, SortOrder


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("group", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group: Mapped[str]
    name: Mapped[str]
    note: Mapped[str | None] = mapped_column("item_note")
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Membership(Base):
    __tablename__ = "memberships"

    group_id: Mapped[int] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(primary_key=True)


class AuditedRepository(BaseRepository[Item]):
    audit_timestamps = ("created_at", "updated_at")


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with AsyncSession(engine) as session:
            yield session
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_crud_uses_the_callers_transaction(session):
    repo = BaseRepository(Item, session)
    item = await repo.save(Item(group="a", name="one"))
    item_id = item.id

    assert await repo.scalar(repo.select_model().where(Item.id == item_id)) is item
    await session.commit()
    session.expunge(item)
    item.note = "merged"

    merged = await repo.merge(item)
    assert merged.note == "merged"
    assert await repo.scalar(select(func.count()).select_from(Item)) == 1

    await repo.remove(merged)
    assert (await repo.scalars(repo.select_model())).all() == []
    await session.rollback()
    restored = await session.get(Item, item_id)
    assert restored is not None
    assert restored.note is None


@pytest.mark.asyncio
async def test_result_helpers_keep_the_first_column_of_multi_column_selects(session):
    repo = BaseRepository(Item, session)
    await repo.save(Item(group="a", name="one"))
    stmt = select(Item.name, Item.id)

    assert (await repo.execute(stmt)).one() == ("one", 1)
    assert await repo.scalar(stmt) == "one"
    assert (await repo.scalars(stmt)).all() == ["one"]


@pytest.mark.asyncio
async def test_upsert_skips_nulls_by_default_and_accepts_composite_keys(session):
    repo = BaseRepository(Item, session)
    key = [Item.group, Item.name]
    await repo.upsert({Item.group: "a", Item.name: "one", Item.note: "keep"}, key)
    await repo.upsert({"group": "a", "name": "one", "note": None}, key)
    await repo.upsert({"group": "b", "name": "one", "note": "other"}, key)
    stmt = select(Item.note).where(Item.group == "a")

    assert await repo.scalar(stmt) == "keep"
    await repo.upsert({"group": "a", "name": "one", Item.__table__.c.item_note: None}, key, update_none=True)
    assert await repo.scalar(stmt) is None
    await repo.upsert({"group": "a", "name": "one"}, key, update_none=True)
    assert (await repo.scalars(select(Item.name))).all() == ["one", "one"]


@pytest.mark.asyncio
async def test_upsert_accepts_partial_updates_and_preserves_audit_creation_time(session):
    repo = AuditedRepository(Item, session)
    await repo.upsert({"id": 1, "group": "a", "name": "one"}, [Item.__table__.c.id])
    created_at = await repo.scalar(select(Item.created_at))
    assert created_at is not None
    assert await repo.scalar(select(Item.updated_at)) is None

    await repo.upsert({"id": 1, "note": "changed"}, [Item.id])
    assert await repo.scalar(select(Item.created_at)) == created_at
    assert await repo.scalar(select(Item.updated_at)) is not None
    assert await repo.scalar(select(Item.note)) == "changed"


@pytest.mark.asyncio
@pytest.mark.parametrize("index_elements", [[], [Item.name]])
async def test_upsert_requires_values_for_conflict_key(session, index_elements):
    with pytest.raises(ValueError, match="conflict key"):
        await BaseRepository(Item, session).upsert({"group": "a"}, index_elements)


@pytest.mark.asyncio
async def test_page_counts_filtered_rows_and_applies_qualified_sorting(session):
    repo = BaseRepository(Item, session)
    session.add_all([Item(group="a", name=name) for name in ("beta", "alpha", "gamma")])
    session.add(Item(group="b", name="outside"))
    await session.flush()

    request = PageRequest(page=1, size=2, sort=[SortOrder.parse("items.name:desc")])
    page = await repo.get_page(request, select(Item).where(Item.group == "a"))

    assert [item.name for item in page.items] == ["alpha"]
    assert (page.total, page.total_pages, page.has_previous, page.has_next) == (3, 2, True, False)

    empty = await repo.get_page(PageRequest(), select(Item).where(Item.group == "missing"))
    assert (list(empty.items), empty.total_pages, empty.has_next) == ([], 0, False)


@pytest.mark.asyncio
async def test_page_accepts_aliases_and_multiple_sort_orders(session):
    repo = BaseRepository(Item, session)
    session.add_all([Item(group=group, name=name) for group, name in (
        ("b", "alpha"), ("a", "alpha"), ("a", "beta"),
    )])
    await session.flush()
    item = aliased(Item, name="entry")
    request = PageRequest(sort=[SortOrder.parse("entry.group"), SortOrder.parse("name:desc")])

    page = await repo.get_page(request, select(item))

    assert [(row.group, row.name) for row in page.items] == [
        ("a", "beta"), ("a", "alpha"), ("b", "alpha"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("column", "error"), [
    ("unknown", "Unknown column"),
    ("missing.name", "Unknown sort entity"),
])
async def test_page_rejects_unknown_sort_names(session, column, error):
    repo = BaseRepository(Item, session)
    with pytest.raises(ValueError, match=error):
        await repo.get_page(PageRequest(sort=[SortOrder(column=column)]))


@pytest.mark.asyncio
async def test_upsert_without_native_conflict_support(session):
    class GenericRepository(BaseRepository[Item]):
        def insert_model(self):
            return insert(Item)

    repo = GenericRepository(Item, session)
    key = [Item.group, Item.name]
    await repo.upsert({"group": "a", "name": "one", "note": "initial"}, key, update_none=True)
    await repo.upsert({"group": "a", "name": "one", "note": None}, key, update_none=True)
    await repo.upsert({"group": "a", "name": "one"}, key, update_none=True)
    await repo.upsert({"group": "a", "name": "two"}, key, update_none=True)

    rows = (await repo.scalars(select(Item).order_by(Item.name))).all()
    assert [(row.name, row.note) for row in rows] == [("one", None), ("two", None)]


@pytest.mark.asyncio
async def test_get_by_pk_accepts_scalar_and_composite_identities(session):
    item = await BaseRepository(Item, session).save(Item(group="a", name="one"))
    items = BaseRepository(Item, session)
    assert await items.get_by_pk(item.id) is item
    assert await items.get_by_pk(item.id + 1) is None

    memberships = BaseRepository(Membership, session)
    member = await memberships.save(Membership(group_id=2, member_id=5))
    assert await memberships.get_by_pk((2, 5)) is member
    assert await memberships.get_by_pk({"member_id": 5, "group_id": 2}) is member
    assert await memberships.get_by_pk((5, 2)) is None


@pytest.mark.asyncio
async def test_exists_checks_row_presence_including_null_values_and_offsets(session):
    repo = BaseRepository(Item, session)
    await repo.save(Item(group="a", name="one"))
    query = select(Item.note).where(Item.group == "a")

    assert await repo.exists(query)
    assert not await repo.exists(query.offset(1))
    assert not await repo.exists(query.limit(0))
    assert not await repo.exists(select(Item.id).where(Item.group == "missing"))


@pytest.mark.asyncio
async def test_cursor_pagination_filters_and_continues_past_a_deleted_row(session):
    repo = BaseRepository(Item, session)
    session.add_all([
        Item(id=5, group="a", name="third"),
        Item(id=1, group="a", name="first"),
        Item(id=2, group="b", name="excluded"),
        Item(id=3, group="a", name="second"),
    ])
    await session.flush()
    query = repo.select_model().where(Item.group == "a").order_by(Item.id.desc())

    first = await repo.get_cursor_page(query, size=2)
    assert [item.id for item in first.items] == [1, 3]
    assert first.has_next
    assert first.next_cursor == (3,)

    await repo.remove(first.items[-1])
    final = await repo.get_cursor_page(query, size=2, after=first.next_cursor)
    assert [item.id for item in final.items] == [5]
    assert not final.has_next
    assert final.next_cursor is None

    empty = await repo.get_cursor_page(query, size=2, after=(5,))
    assert empty.items == []
    assert not empty.has_next


@pytest.mark.asyncio
async def test_cursor_pagination_compares_composite_primary_keys_in_order(session):
    repo = BaseRepository(Membership, session)
    session.add_all(Membership(group_id=group, member_id=member) for group, member in [
        (2, 1), (1, 8), (2, 2), (1, 3),
    ])
    await session.flush()

    first = await repo.get_cursor_page(size=2)
    assert [(row.group_id, row.member_id) for row in first.items] == [(1, 3), (1, 8)]
    assert first.next_cursor == (1, 8)

    final = await repo.get_cursor_page(size=2, after=first.next_cursor)
    assert [(row.group_id, row.member_id) for row in final.items] == [(2, 1), (2, 2)]
    assert not final.has_next


@pytest.mark.asyncio
@pytest.mark.parametrize(("size", "after", "message"), [
    (0, None, "Page size"),
    (1, (), "Cursor"),
    (1, (1, 2), "Cursor"),
    (1, (None,), "Cursor"),
])
async def test_cursor_pagination_rejects_invalid_inputs(session, size, after, message):
    with pytest.raises(ValueError, match=message):
        await BaseRepository(Item, session).get_cursor_page(size=size, after=after)

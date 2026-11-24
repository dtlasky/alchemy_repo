# Alchemy Repo

Alchemy Repo is a typed repository layer for SQLAlchemy's async ORM. It provides statement builders, persistence helpers, and offset pagination for application-owned sessions.

## Requirements

- Python 3.14+
- SQLAlchemy 2.1+, installed with the library
- An async driver for your database, such as `aiosqlite` or `asyncpg`

## Installation

Add the local checkout as a dependency from your application directory. Adjust the path if the repositories are in different locations.

```sh
uv add ../alchemy_repo
```

## Quickstart

For the SQLite example, run the following commands from the Alchemy Repo checkout. The development dependencies include `aiosqlite`.

```sh
uv sync --locked
uv run --locked python example.py
```

Save this code as `example.py`.

```python
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from alchemy_repo import BaseRepository, Page, PageRequest, SortOrder


class Base(DeclarativeBase):
    pass


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    published: Mapped[bool]


class ArticleRepository(BaseRepository[Article]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Article, session)

    async def get_published(self, request: PageRequest) -> Page[Article]:
        query = self.select_model().where(Article.published.is_(True))
        return await self.get_page(request, query)


async def main() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with sessions.begin() as session:
            articles = ArticleRepository(session)
            await articles.save(Article(title="First post", published=True))
            await articles.save(Article(title="Second post", published=True))
            await articles.save(Article(title="Draft", published=False))

            request = PageRequest(page=0, size=1, sort=[SortOrder.parse("id:desc")])
            page = await articles.get_published(request)

            print([article.title for article in page.items])  # ['Second post']
            print(page.total, page.total_pages, page.has_next)  # 2 2 True
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
```

## Repository design

Define a repository subclass for each model and bind that model in the
subclass constructor. Put application queries behind methods that describe
what the caller needs, such as `ArticleRepository.get_published()`. Routes and
services pass in request data and work with the returned models. The repository
owns the SQLAlchemy filters and statement construction, so multiple callers
can reuse the same query without knowing how it is built.

## Repository API

| Method | Behavior |
| --- | --- |
| `select_model`, `insert_model`, `update_model`, `delete_model` | Build SQLAlchemy statements for the mapped model. |
| `execute` | Execute a statement through the supplied session. Typed row statements retain their result column types. |
| `scalar`, `scalars` | Read the first selected column. `scalar` returns one value or `None` and raises if the result has multiple rows. |
| `get_by_pk` | Look up a model by its primary key using the session's identity map when possible. |
| `exists` | Check whether a select statement produces a row. |
| `save`, `merge`, `remove` | Persist, merge, or delete an instance and flush the session. |
| `apply_sorts` | Add the requested order to a model query. |
| `get_page` | Return a typed `Page[Model]` with items, total row count, and navigation metadata. |
| `get_cursor_page` | Return a page in primary-key order with a cursor for the next page. |
| `upsert` | Insert or update using the supplied conflict key columns. |

The application owns the session and its transaction. Use `async_sessionmaker.begin()` for automatic commit on success and rollback on error, or manage the transaction explicitly.

### Pagination

Pages start at zero. Use a stable sort with a unique tie-breaker when fetching successive pages. Sort names refer to selected columns and can be qualified by the mapped class, table, or named ORM alias. Pass a query before applying offsets or limits. The total counts rows from the supplied query, including filters and grouping.

### Primary-key lookup and existence

```python
# In ArticleRepository
async def has_drafts(self) -> bool:
    query = self.select_model().where(Article.published.is_(False))
    return await self.exists(query)

# At the call site
article = await articles.get_by_pk(42)
has_drafts = await articles.has_drafts()
```

For a composite primary key, pass a tuple in mapped primary-key order or a
dictionary keyed by mapped attribute names to `get_by_pk()`.

### Cursor pagination

```python
from alchemy_repo import CursorPage

# In ArticleRepository
async def get_published_after(
    self, after: tuple[object, ...] | None = None
) -> CursorPage[Article]:
    query = self.select_model().where(Article.published.is_(True))
    return await self.get_cursor_page(query, size=20, after=after)

# At the call site
page = await articles.get_published_after()
if page.next_cursor is not None:
    next_page = await articles.get_published_after(page.next_cursor)
```

Cursor pages use ascending mapped primary-key order, including composite keys.
The repository applies its own ordering and page bounds. Supply a query that
selects the repository model and yields one SQL row per entity. For example,
use an `EXISTS` filter when filtering on a relationship that would otherwise
multiply rows. The repository fetches one extra row to detect the next page
without running a count. Reuse the same filters on subsequent requests.
`next_cursor` is a tuple of key values when another page exists, otherwise
it is `None`. Applications decide how to encode those values for HTTP clients.

### Upsert

Pass model attributes or mapped table columns as `index_elements` and include their values in the data mapping. Data keys may also be mapped attribute names. The default skips null updates. Set `update_none=True` to write nulls to nullable fields.

PostgreSQL and SQLite use native `ON CONFLICT DO UPDATE` when `update_none=True` and there are columns to update. Supply enough data for a valid insert in that mode. Other cases use an update followed by an insert when the row is missing, or look up the row first when only conflict keys are supplied. The portable path supports partial updates. Concurrent inserts for the same key may require a transaction retry.

To populate timestamps during upserts, set `audit_timestamps = ("created_at", "updated_at")` on a repository subclass and define the corresponding mapped attributes. The repository uses UTC timestamps. The application owns any ORM lifecycle hooks.

## SQLAlchemy utilities

Use `column_info()` to inspect one mapped column by attribute or attribute name.

```python
from alchemy_repo.entity import column_info

info = column_info(Article, "id")  # Equivalent to column_info(Article.id)
assert info.is_primary_key
assert info.is_unique
print(info.key, info.name, info.nullable, info.sql_type)
```

`key` is the mapped attribute name and `name` is the database column name.
`is_unique` describes declared standalone uniqueness, including single-column
primary keys, unique constraints, and unconditional unique column indexes.
Membership in a composite or partial key or index does not imply standalone
uniqueness. The helper reads SQLAlchemy metadata without querying the database.

Use the pagination helpers directly with SQLAlchemy queries, including projections
that return more than one column.

```python
from sqlalchemy import select

from alchemy_repo import PageRequest, SortOrder
from alchemy_repo.pagination import count_query, map_sort_orders, page_query

request = PageRequest(page=0, size=10, sort=[SortOrder.parse("id:desc")])
query = select(Article.id, Article.title).where(Article.published.is_(True))
total_stmt = count_query(query)
items_stmt = page_query(query.order_by(*map_sort_orders(query, request.sort)), request)
```

## Pydantic

The default pagination models are standard-library dataclasses. Use the optional
`pydantic` extra for request validation, response serialization, and OpenAPI schemas.

```sh
uv add "../alchemy_repo[pydantic]"
```

Import the HTTP-facing models from `alchemy_repo.pydantic`. Call `.to_request()`
before passing a request to the repository, then use `.model_validate()` to
convert the returned page. The Pydantic response includes `total_pages`,
`has_next`, and `has_previous` in serialized output.

## FastAPI query dependency

Install the `fastapi` extra to get FastAPI and the Pydantic models.

```sh
uv add "../alchemy_repo[fastapi]"
```

Annotate a route parameter with `PageRequestDepends`. It returns a core
`PageRequest` ready to pass to the repository. You can also use
`Depends(build_page_request)` explicitly.

For example, `GET /articles?page=0&size=20&sort=title:asc&sort=id:desc`
sorts by title first, then by ID. `page` defaults to zero, `size` defaults to
20 and accepts values from 1 to 100. Invalid bounds, empty sort names, and
invalid sort directions produce HTTP 422. Which columns can be sorted depends
on the repository query.

This route uses the `ArticleRepository` from the quickstart and assumes your
app provides a `get_session` dependency.

```python
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from alchemy_repo.fastapi import PageRequestDepends
from alchemy_repo.pydantic import Page

from my_app.db import get_session
from my_app.repositories import ArticleRepository

router = APIRouter()


class ArticleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str


@router.get("/articles")
async def search_articles(
    request: PageRequestDepends,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Page[ArticleResponse]:
    articles = ArticleRepository(session)
    page = await articles.get_published(request)
    return Page[ArticleResponse].model_validate(page)
```

## Development

```sh
uv sync --locked
uv run --locked pytest
uv run --locked --extra pydantic pytest
uv run --locked --extra fastapi --group fastapi-test pytest
uv build
```

GitHub Actions runs the test suite and builds the wheel and source distribution on pushes and pull requests.

## Releasing

Publishing a GitHub release with a tag matching the package version (for example,
`v0.1.0`) runs the test and build jobs, then publishes the resulting distributions
to PyPI through Trusted Publishing.

For the initial setup, create a pending GitHub publisher in
[PyPI Publishing](https://pypi.org/manage/account/publishing/) for project
`alchemy-repo`, owner `dtlasky`, repository `alchemy_repo`, workflow
`release.yml`, and environment `pypi`. GitHub Actions authenticates through
OIDC. No PyPI API token is required.

## License

Alchemy Repo is available under the [MIT License](LICENSE).

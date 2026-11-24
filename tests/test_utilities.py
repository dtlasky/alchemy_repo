import pytest
from sqlalchemy import Index, String, UniqueConstraint, literal, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, column_property, mapped_column

from alchemy_repo import PageRequest, SortOrder, column_info
from alchemy_repo.pagination import count_query, map_sort_orders, page_query


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("handle"),
        UniqueConstraint("team", "nickname"),
        Index("uq_accounts_ref", "reference", unique=True),
        Index("uq_accounts_active_ref", "active_ref", unique=True, sqlite_where=text("active_ref <> ''")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column("email_address", unique=True)
    handle: Mapped[str]
    team: Mapped[str]
    nickname: Mapped[str | None]
    reference: Mapped[str]
    active_ref: Mapped[str]
    label = column_property(literal("label"))


class Membership(Base):
    __tablename__ = "memberships"

    account_id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(primary_key=True)


def test_column_info_resolves_mapped_attributes_and_database_names():
    email = column_info(Account, "email")
    assert email == column_info(Account.email)
    assert email.key == "email"
    assert email.name == "email_address"
    assert email.column is Account.__table__.c.email_address
    assert isinstance(email.sql_type, String)
    assert email.is_unique
    assert not email.is_primary_key
    assert not email.nullable
    assert column_info(Account.nickname).nullable


@pytest.mark.parametrize(("attribute", "primary_key", "unique"), [
    (Account.id, True, True),
    (Account.handle, False, True),
    (Account.reference, False, True),
    (Account.active_ref, False, False),
    (Account.team, False, False),
    (Membership.account_id, True, False),
])
def test_only_standalone_uniqueness_counts(attribute, primary_key, unique):
    info = column_info(attribute)
    assert info.is_primary_key is primary_key
    assert info.is_unique is unique


@pytest.mark.parametrize("name", ["missing", "label"])
def test_column_info_rejects_non_column_attributes(name):
    with pytest.raises(ValueError, match="column"):
        column_info(Account, name)


def test_pagination_utilities_accept_projection_without_repository():
    request = PageRequest(page=2, size=5, sort=[SortOrder.parse("email:desc")])
    query = select(Account.id, Account.email)
    ordered = query.order_by(*map_sort_orders(query, request.sort))

    data = page_query(ordered, request).compile()
    assert "ORDER BY accounts.email_address DESC" in str(data)
    assert sorted(data.params.values()) == [5, 10]
    assert "SELECT count(*)" in str(count_query(query))

from dataclasses import asdict, is_dataclass

import pytest

from alchemy_repo import Page, PageRequest, SortDirection, SortOrder


@pytest.mark.parametrize("values", [{"page": -1}, {"size": 0}])
def test_invalid_page_request(values):
    with pytest.raises(ValueError):
        PageRequest(**values)


@pytest.mark.parametrize("value", ["name:sideways", ":asc"])
def test_invalid_sort(value):
    with pytest.raises(ValueError):
        SortOrder.parse(value)


def test_page_and_sort_metadata():
    assert SortOrder.parse("name").direction is None
    assert SortOrder.parse("name:asc").direction == SortDirection.ASC
    assert PageRequest().size == 20
    page = Page[int](items=[1, 2], page=0, size=2, total=3)
    assert is_dataclass(page)
    assert asdict(page) == {"items": [1, 2], "page": 0, "size": 2, "total": 3}
    assert (page.total_pages, page.has_next, page.has_previous) == (2, True, False)

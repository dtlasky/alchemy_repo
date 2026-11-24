from types import SimpleNamespace

import pytest

from alchemy_repo import Page as CorePage
from alchemy_repo import PageRequest as CorePageRequest
from alchemy_repo import SortDirection
from alchemy_repo import SortOrder as CoreSortOrder

pydantic = pytest.importorskip("pydantic", exc_type=ModuleNotFoundError)

from alchemy_repo.pydantic import Page, PageRequest, SortOrder


def test_pydantic_request_validates_and_converts_to_core_request():
    request = PageRequest.model_validate({
        "page": "1",
        "size": "2",
        "sort": [{"column": "name", "direction": "desc"}],
    })

    assert request.to_request() == CorePageRequest(1, 2, [CoreSortOrder("name", SortDirection.DESC)])
    assert SortOrder.parse("name:desc") == request.sort[0]
    with pytest.raises(pydantic.ValidationError):
        PageRequest.model_validate({"size": 0})


def test_pydantic_response_serializes_items_and_pagination():
    class ItemResponse(pydantic.BaseModel):
        model_config = pydantic.ConfigDict(from_attributes=True)

        name: str

    page = CorePage(items=[SimpleNamespace(name="one")], page=1, size=1, total=3)

    response = Page[ItemResponse].model_validate(page)
    assert response.model_dump(mode="json") == {
        "items": [{"name": "one"}],
        "page": 1,
        "size": 1,
        "total": 3,
        "total_pages": 3,
        "has_next": True,
        "has_previous": True,
    }
    assert "total_pages" in Page[ItemResponse].model_json_schema(mode="serialization")["properties"]

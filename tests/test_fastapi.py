from dataclasses import asdict

import pytest
import pytest_asyncio

pytest.importorskip("fastapi", exc_type=ModuleNotFoundError)

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from alchemy_repo.fastapi import PageRequestDepends

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client():
    app = FastAPI()

    @app.get("/items")
    async def list_items(request: PageRequestDepends):
        return asdict(request)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


async def test_pagination_defaults_and_openapi(client):
    assert (await client.get("/items")).json() == {"page": 0, "size": 20, "sort": []}
    operation = (await client.get("/openapi.json")).json()["paths"]["/items"]["get"]
    assert "requestBody" not in operation
    assert {(param["in"], param["name"]) for param in operation["parameters"]} == {
        ("query", "page"), ("query", "size"), ("query", "sort"),
    }


async def test_pagination_preserves_query_sort_priority(client):
    response = await client.get("/items?page=1&size=100&sort=title%3Aasc&sort=id%3Adesc&sort=category")
    assert response.status_code == 200
    assert response.json() == {
        "page": 1,
        "size": 100,
        "sort": [
            {"column": "title", "direction": "asc"},
            {"column": "id", "direction": "desc"},
            {"column": "category", "direction": None},
        ],
    }


@pytest.mark.parametrize(("query", "location"), [
    ("page=-1", ["query", "page"]),
    ("size=0", ["query", "size"]),
    ("size=101", ["query", "size"]),
    ("sort=title&sort=id%3Asideways", ["query", "sort", 1]),
    ("sort=%3Aasc", ["query", "sort", 0]),
])
async def test_invalid_query_parameters_return_422(client, query, location):
    response = await client.get(f"/items?{query}")
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == location

from __future__ import annotations

import pytest
import httpx
import pytest
import pytest_asyncio

from tests.sample_app import app, reset_state


@pytest.fixture(autouse=True)
def _reset_state():
    reset_state()
    yield
    reset_state()


@pytest.fixture
def anyio_backend():
    # Limit anyio tests to asyncio backend; trio is not installed in this environment.
    return "asyncio"


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.anyio("asyncio")
async def test_ping(client: httpx.AsyncClient):
    res = await client.get("/ping")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


@pytest.mark.anyio("asyncio")
async def test_create_and_read_item(client: httpx.AsyncClient):
    create = await client.post("/items/item1", json={"value": "hello"})
    assert create.status_code == 200
    assert create.json() == {"id": "item1", "value": "hello"}

    read = await client.get("/items/item1")
    assert read.status_code == 200
    assert read.json() == {"id": "item1", "value": "hello"}


@pytest.mark.anyio("asyncio")
async def test_list_items_isolated_between_tests(client: httpx.AsyncClient):
    res = await client.get("/items")
    assert res.status_code == 200
    assert res.json() == []

    await client.post("/items/a", json={"value": "A"})
    await client.post("/items/b", json={"value": "B"})
    listed = await client.get("/items")
    assert listed.json() == [
        {"id": "a", "value": "A"},
        {"id": "b", "value": "B"},
    ]

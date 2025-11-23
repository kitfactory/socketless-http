from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.sample_app import app, reset_state


@pytest.fixture(autouse=True)
def _reset_state():
    reset_state()
    yield
    reset_state()


def test_ping():
    client = TestClient(app)
    res = client.get("/ping")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_create_and_read_item():
    client = TestClient(app)
    create = client.post("/items/item1", json={"value": "hello"})
    assert create.status_code == 200
    assert create.json() == {"id": "item1", "value": "hello"}

    read = client.get("/items/item1")
    assert read.status_code == 200
    assert read.json() == {"id": "item1", "value": "hello"}


def test_list_items_isolated_between_tests():
    client = TestClient(app)
    res = client.get("/items")
    assert res.status_code == 200
    assert res.json() == []

    client.post("/items/a", json={"value": "A"})
    client.post("/items/b", json={"value": "B"})
    listed = client.get("/items")
    assert listed.json() == [
        {"id": "a", "value": "A"},
        {"id": "b", "value": "B"},
    ]

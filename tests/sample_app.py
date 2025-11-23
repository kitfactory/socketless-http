from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class Item(BaseModel):
    value: str


app = FastAPI()
_items: dict[str, Item] = {}


@app.get("/ping")
async def ping() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/items/{item_id}")
async def create_item(item_id: str, item: Item) -> dict[str, str]:
    _items[item_id] = item
    return {"id": item_id, "value": item.value}


@app.get("/items/{item_id}")
async def read_item(item_id: str) -> dict[str, str]:
    if item_id not in _items:
        raise HTTPException(status_code=404, detail="not found")
    item = _items[item_id]
    return {"id": item_id, "value": item.value}


@app.get("/items")
async def list_items() -> list[dict[str, str]]:
    return [{"id": k, "value": v.value} for k, v in sorted(_items.items())]


def reset_state() -> None:
    _items.clear()

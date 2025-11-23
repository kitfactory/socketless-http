from __future__ import annotations

from flask import Flask, abort, jsonify, request

app = Flask(__name__)
_items: dict[str, str] = {}


@app.get("/ping")
def ping():
    return jsonify(status="ok")


@app.post("/items/<item_id>")
def create_item(item_id: str):
    data = request.get_json(force=True, silent=True) or {}
    if "value" not in data:
        abort(400)
    _items[item_id] = data["value"]
    return jsonify(id=item_id, value=data["value"])


@app.get("/items/<item_id>")
def read_item(item_id: str):
    if item_id not in _items:
        abort(404)
    return jsonify(id=item_id, value=_items[item_id])


@app.get("/items")
def list_items():
    return jsonify([{"id": k, "value": v} for k, v in sorted(_items.items())])


def reset_state() -> None:
    _items.clear()

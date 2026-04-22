"""Small orjson wrappers for typed JSON boundaries."""

from __future__ import annotations

from typing import cast

import orjson

from parliament.models.common import JSONObject, JSONValue


def dumps_json(value: object) -> str:
    """Serialize *value* as UTF-8 JSON text."""
    return orjson.dumps(value).decode()


def dumps_pretty_json(value: object) -> str:
    """Serialize *value* as indented UTF-8 JSON text."""
    return orjson.dumps(value, option=orjson.OPT_INDENT_2).decode()


def loads_json(data: str | bytes) -> JSONValue:
    """Deserialize JSON text into a JSON value."""
    return cast(JSONValue, orjson.loads(data))


def loads_json_object(data: str | bytes) -> JSONObject:
    """Deserialize JSON text and require a JSON object."""
    value = loads_json(data)
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object")
    return value

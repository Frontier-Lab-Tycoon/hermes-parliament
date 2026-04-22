"""Typed accessors for values inside JSON objects."""

from __future__ import annotations

from parliament.models.common import JSONObject


def str_field(data: JSONObject, key: str, default: str = "") -> str:
    value = data.get(key, default)
    return value if isinstance(value, str) else default


def float_field(data: JSONObject, key: str, default: float = 0.0) -> float:
    value = data.get(key, default)
    if isinstance(value, bool):
        return default
    return float(value) if isinstance(value, (int, float)) else default


def bool_field(data: JSONObject, key: str, default: bool = False) -> bool:
    value = data.get(key, default)
    return value if isinstance(value, bool) else default


def str_list_field(data: JSONObject, key: str) -> list[str] | None:
    value = data.get(key)
    if not isinstance(value, list):
        return None
    strings = [item for item in value if isinstance(item, str)]
    return strings or None

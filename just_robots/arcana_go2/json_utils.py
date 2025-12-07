from __future__ import annotations
import typing as t

JSONKey: t.TypeAlias = str
JSONValue: t.TypeAlias = "None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]"
JSONObject: t.TypeAlias = "dict[JSONKey, JSONValue]"

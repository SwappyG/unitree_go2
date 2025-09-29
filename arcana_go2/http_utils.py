from __future__ import annotations
import typing as t

ScalarParam: t.TypeAlias = "str | int | float | bool | None"
ParamSequence: t.TypeAlias = "list[ScalarParam] | tuple[ScalarParam, ...]"
QueryParams: t.TypeAlias = "t.Mapping[str, ScalarParam | ParamSequence]"
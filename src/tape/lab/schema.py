from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from tape.engine.strategy import Sizing
from tape.strategies.registry import STRATEGIES

if TYPE_CHECKING:
    from datetime import date

    from tape.engine.backtest import ExecutionSpec


def label(value: object) -> str:
    text = str(value).replace("_", " ")
    return text[:1].upper() + text[1:]


def resolve(prop: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    ref = prop.get("$ref") or next((p.get("$ref") for p in prop.get("allOf", []) if "$ref" in p), None)
    if ref is None:
        return prop
    return {**defs[ref.rsplit("/", 1)[-1]], **{k: v for k, v in prop.items() if k not in {"$ref", "allOf"}}}


def fixed(prop: dict[str, Any]) -> bool:
    return "const" in prop


def visible(prop: dict[str, Any], chosen: dict[str, Any]) -> bool:
    return all(chosen.get(field) == value for field, value in prop.get("when", {}).items())


PRESETS: Final[dict[str, int | None]] = {"1Y": 1, "3Y": 3, "5Y": 5, "Max": None}


def years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def presets(first: date, last: date) -> list[str]:
    return [name for name, years in PRESETS.items() if years is None or years_before(last, years) >= first]


def preset_span(name: str, first: date, last: date) -> tuple[date, date]:
    years = PRESETS[name]
    return (first if years is None else max(first, years_before(last, years))), last


def capital(strategy: str, params: dict[str, Any], execution: ExecutionSpec) -> ExecutionSpec:
    config = STRATEGIES[strategy].config_type.model_validate(params)
    if config.sizing is not Sizing.NOTIONAL:
        return execution
    return execution.model_copy(update={"initial_cash": config.notional})

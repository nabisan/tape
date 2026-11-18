from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import JsonSchemaMode, models_json_schema

from tape.baskets import Basket, BasketQuery
from tape.core.hashing import Json
from tape.lab.form.model import RunRequest, Seed, SweepRequest
from tape.service.ledgers import LedgerStatus
from tape.service.models import Catalog, PathsMeta, StrategyInfo, StrategySpace
from tape.service.prices import PriceWindow
from tape.service.sweeps import SweepStatus

SCHEMA: Final = Path(__file__).with_name("types") / "api.schema.json"


class FieldSchema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow", populate_by_name=True)

    ref: str | None = Field(default=None, alias="$ref")
    all_of: list[FieldSchema] | None = Field(default=None, alias="allOf")
    title: str | None = None
    type: str | None = None
    enum: list[Json] | None = None
    const: Json = None
    default: Json = None
    minimum: float | None = None
    maximum: float | None = None
    exclusive_minimum: float | None = Field(default=None, alias="exclusiveMinimum")
    exclusive_maximum: float | None = Field(default=None, alias="exclusiveMaximum")
    step: float | None = None
    when: dict[str, Json] | None = None


class ConfigSchema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow", populate_by_name=True)

    properties: dict[str, FieldSchema]
    defs: dict[str, FieldSchema] = Field(default={}, alias="$defs")


class FormModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    page: Literal["run", "simulation"]
    pages: dict[str, str]
    api: str
    seed: Seed | None
    submission: RunRequest | SweepRequest | None
    message: str
    busy: bool


class PathsTrace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    meta: str
    name: str | None = None


class PathsFigure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    layout: dict[str, Any]
    config: dict[str, Any]
    traces: list[PathsTrace]


class PathsModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    api: str
    key: str
    plotly: str
    runs: list[str]
    figure: PathsFigure


class PriceModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    api: str
    key: str
    plotly: str
    intraday: bool
    labels: dict[str, str]
    figure: PathsFigure


class LedgerModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    api: str
    key: str


SENT: Final[tuple[type[BaseModel], ...]] = (RunRequest, SweepRequest, BasketQuery)
READ: Final[tuple[type[BaseModel], ...]] = (
    Catalog,
    StrategyInfo,
    StrategySpace,
    ConfigSchema,
    Basket,
    PathsMeta,
    SweepStatus,
    LedgerStatus,
    FormModel,
    PathsModel,
    PriceModel,
    PriceWindow,
    LedgerModel,
)


def bundle() -> dict[str, Any]:
    modes: list[tuple[type[BaseModel], JsonSchemaMode]] = [
        *((m, "validation") for m in SENT),
        *((m, "serialization") for m in READ),
    ]
    _, found = models_json_schema(modes, by_alias=True, title="TapeApi")
    return found


def untitled(node: Any) -> Any:
    if isinstance(node, dict):
        refs = node.get("allOf")
        only = refs[0] if isinstance(refs, list) and len(refs) == 1 else None
        if isinstance(only, dict) and isinstance(only.get("$ref"), str):
            return {"$ref": only["$ref"]}
        if isinstance(node.get("$ref"), str):
            return {"$ref": node["$ref"]}
        return {k: untitled(v) for k, v in node.items() if k != "title" or not isinstance(v, str)}
    if isinstance(node, list):
        return [untitled(v) for v in node]
    return node


def named(found: dict[str, Any]) -> dict[str, Any]:
    defs = {key: {**untitled(body), "title": key.replace("-", "")} for key, body in found["$defs"].items()}
    return {"title": found["title"], "$defs": defs}


def render() -> str:
    return json.dumps(named(bundle()), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    SCHEMA.parent.mkdir(exist_ok=True)
    SCHEMA.write_text(render())

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

from tape.lab import nav
from tape.lab.contract import SCHEMA, ConfigSchema, FormModel, LedgerModel, PathsModel, render
from tape.lab.form.model import Form
from tape.lab.widgets.ledger import Ledger
from tape.lab.widgets.paths import Paths
from tape.strategies.registry import STRATEGIES

if TYPE_CHECKING:
    import anywidget
    from pydantic import BaseModel

ROOT: Final = Path(__file__).resolve().parents[1]
TYPES: Final = SCHEMA.with_name("api.d.ts")
NPM: Final = shutil.which("npm")
NODE: Final = pytest.mark.skipif(
    NPM is None or not (ROOT / "node_modules").is_dir(), reason="node toolchain not installed (npm ci)"
)


def synced(widget: type[anywidget.AnyWidget]) -> set[str]:
    return {name for name, trait in widget.class_own_traits().items() if trait.metadata.get("sync")}


def npm(*args: str) -> str:
    assert NPM is not None
    done = subprocess.run([NPM, "run", "-s", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


def test_the_committed_schema_matches_the_pydantic_models() -> None:
    assert SCHEMA.read_text() == render(), "run: uv run python -m tape.lab.contract && npm run types"


@pytest.mark.parametrize(
    ("widget", "model"),
    [(Form, FormModel), (Paths, PathsModel), (Ledger, LedgerModel)],
)
def test_every_widget_syncs_exactly_its_typed_model(
    widget: type[anywidget.AnyWidget], model: type[BaseModel]
) -> None:
    assert synced(widget) == set(model.model_fields)


@pytest.mark.parametrize(
    ("widget", "model"),
    [
        (Form(page="run", pages=nav.PAGES), FormModel),
        (Form(page="simulation", pages=nav.PAGES), FormModel),
        (Paths.of("abc", ["a", "b"], {"a": "AAPL 5/20"}, "QQQ"), PathsModel),
        (Ledger(key="abc"), LedgerModel),
    ],
)
def test_widget_state_validates_against_its_model(
    widget: anywidget.AnyWidget, model: type[BaseModel]
) -> None:
    model.model_validate({name: getattr(widget, name) for name in model.model_fields})


@pytest.mark.parametrize("name", sorted(STRATEGIES))
def test_every_strategy_schema_reads_as_the_form_contract(name: str) -> None:
    schema = ConfigSchema.model_validate(STRATEGIES[name].config_type.model_json_schema())
    assert schema.properties
    fields = [*schema.properties.values(), *(f for p in schema.properties.values() for f in p.all_of or [])]
    for field in fields:
        assert field.ref is None or field.ref.removeprefix("#/$defs/") in schema.defs


@NODE
def test_the_typescript_types_are_generated_from_the_schema() -> None:
    assert npm("types:emit") == TYPES.read_text(), "run: npm run types"


@NODE
def test_the_widget_scripts_type_check() -> None:
    npm("check")


@NODE
def test_the_widget_logic_passes_its_unit_tests() -> None:
    npm("test")

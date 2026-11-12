from __future__ import annotations

import math
from datetime import date
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tape.benchmarks import compare
from tape.calendar import sessions
from tape.catalog.build import build
from tape.catalog.store import Store
from tape.core.types import Ticker, Timeframe
from tape.engine.backtest import BacktestEngine
from tape.engine.matching import FillModel
from tape.runner import Result, RunSpec, execute, session_range
from tape.strategies.registry import create
from tape.strategies.sma_cross import direction_of
from tests.conftest import minutes

if TYPE_CHECKING:
    from pathlib import Path

DAYS = [str(d) for d in sessions(date(2024, 1, 2), date(2024, 6, 28)).day]
CUT = date(2024, 4, 1)

SMA_D1 = ("sma_cross", {"timeframe": "1d", "fast": 3, "slow": 8})
SMA_H1 = ("sma_cross", {"timeframe": "1h", "fast": 4, "slow": 12})
HB_5M = ("high_breakout", {"timeframe": "5m", "stop_loss_pct": 0.25})
HB_1H = ("high_breakout", {"timeframe": "1h", "stop_loss_pct": 1.0})
LB_5M = ("low_breakdown", {"timeframe": "5m", "stop_loss_pct": 0.25})
LB_1H = ("low_breakdown", {"timeframe": "1h", "stop_loss_pct": 1.0})
ALL = [SMA_D1, SMA_H1, HB_5M, HB_1H, LB_5M, LB_1H]


def make_store(root: Path, perturb_after: date | None = None) -> Store:
    src = root / "source"
    src.mkdir(parents=True)
    for i, t in enumerate(["SPY", "AAPL"]):
        table = minutes(DAYS, seed=10 + i)
        if perturb_after is not None:
            later = table["time"].cast(pa.int64()).to_numpy() >= np.datetime64(perturb_after, "ms").astype(
                np.int64
            )
            factor = np.where(later, 1.07, 1.0)
            for col in ("open", "high", "low", "close"):
                table = table.set_column(
                    table.schema.get_field_index(col), col, pa.array(table[col].to_numpy() * factor)
                )
        pq.write_table(table, src / f"{t}.parquet")
    build(src, root / "store")
    return Store.open(root / "store")


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Store:
    return make_store(tmp_path_factory.mktemp("base"))


def spec(strategy: tuple[str, dict[str, object]], **execution: object) -> RunSpec:
    name, params = strategy
    return RunSpec.model_validate(
        {
            "ticker": "AAPL",
            "start": "2024-02-01",
            "end": "2024-06-28",
            "strategy": name,
            "params": params,
            "execution": execution,
            "features": "compute",
        }
    )


LEDGER = [(s, False) for s in ALL] + [(SMA_H1, True)]


@pytest.mark.parametrize(
    ("strategy", "intraday"), LEDGER, ids=lambda v: str(v[0]) if isinstance(v, tuple) else str(v)
)
def test_ledger_reconciles(store: Store, strategy: tuple[str, dict[str, object]], intraday: bool) -> None:
    r = execute(store, spec(strategy, intraday=intraday))
    assert len(r.fills) > 0
    assert r.metrics.final_equity - r.metrics.initial_cash == pytest.approx(
        r.trades["net_pnl"].sum(), abs=1e-6
    )
    assert r.metrics.total_commission == pytest.approx(r.fills["commission"].sum())


@pytest.mark.parametrize("strategy", ALL, ids=lambda s: f"{s[0]}-{s[1]['timeframe']}")
def test_no_look_ahead(store: Store, tmp_path: Path, strategy: tuple[str, dict[str, object]]) -> None:
    shifted = make_store(tmp_path, perturb_after=CUT)
    a, b = execute(store, spec(strategy)), execute(shifted, spec(strategy))
    cut = pd.Timestamp(CUT)
    before_a = a.fills[a.fills["time"] < cut].reset_index(drop=True)
    before_b = b.fills[b.fills["time"] < cut].reset_index(drop=True)
    assert len(before_a) > 0
    pd.testing.assert_frame_equal(before_a, before_b)


@pytest.mark.parametrize("strategy", [SMA_H1], ids=lambda s: s[0])
def test_intraday_is_flat_every_close(store: Store, strategy: tuple[str, dict[str, object]]) -> None:
    r = execute(store, spec(strategy, intraday=True, fill_on="minute"))
    assert bool((r.equity["position"] == 0).all())
    squares = r.fills[r.fills["tag"] == "squareoff"]
    assert len(squares) > 0
    clock = squares["time"].dt.strftime("%H:%M")
    half_day = squares["time"].dt.date == date(2024, 7, 3)
    assert bool((clock[~half_day] >= "15:55").all())


def test_entry_delay_blocks_early_entries(store: Store) -> None:
    r = execute(store, spec(SMA_H1, intraday=True, entry_delay_minutes=120))
    entries = r.orders[(r.orders["status"] == "filled") & (r.orders["tag"] == "signal")]
    rejected = r.orders[r.orders["status"] == "rejected"]
    assert len(rejected) > 0
    assert bool((rejected["reason"] == "outside entry window").all())
    assert bool((entries["submitted"].dt.strftime("%H:%M") >= "11:30").all())


def test_daily_signal_fills_at_next_session_open(store: Store) -> None:
    r = execute(store, spec(SMA_D1))
    daily = set(store.bars(Ticker("AAPL"), Timeframe.D1).time_ns.tolist())
    fill_ns = r.fills["time"].astype("int64").tolist()
    assert all(t in daily for t in fill_ns)
    assert bool(
        (
            r.fills["time"].dt.date
            > r.orders.set_index("order_id").loc[r.fills["order_id"], "submitted"].dt.date.to_numpy()
        ).all()
    )


def test_stop_orders_fill_at_level_or_gap(store: Store) -> None:
    free = FillModel(slippage_bps=0.0, commission_per_share=0.0)
    r = execute(store, spec(HB_5M, fill=free.model_dump()))
    stops = r.orders[(r.orders["type"] == "stop_market") & (r.orders["status"] == "filled")]
    assert len(stops) > 0
    bars = store.bars(Ticker("AAPL"), Timeframe.M5)
    opens = dict(zip(bars.time_ns.tolist(), bars.open.tolist(), strict=True))
    for _, o in stops.iterrows():
        at = opens[int(o["closed"].value)]
        assert o["fill_price"] == pytest.approx(o["price"]) or o["fill_price"] == pytest.approx(at)


def test_deterministic(store: Store) -> None:
    a: Result = execute(store, spec(HB_5M))
    b: Result = execute(store, spec(HB_5M))
    pd.testing.assert_frame_equal(a.fills, b.fills)
    assert a.key == b.key
    assert a.metrics == b.metrics


@pytest.mark.parametrize(
    ("strategy", "intraday"),
    [(HB_1H, False), (SMA_H1, False), (SMA_H1, True)],
    ids=["hb", "sma", "sma-intraday"],
)
def test_fast_forward_is_exact(store: Store, strategy: tuple[str, dict[str, object]], intraday: bool) -> None:
    s = spec(strategy, intraday=intraday, fill_on="minute")
    sessions_ = store.sessions()
    first, last = session_range(sessions_, s.start, s.end)
    runs = []
    for fast in (True, False):
        strat = create(s.strategy, s.params)
        m1 = store.bars(Ticker("AAPL"), Timeframe.M1)
        decision = store.bars(Ticker("AAPL"), strat.config.timeframe)
        engine = BacktestEngine(
            strat, m1=m1, decision=decision, sessions=sessions_, execution=s.execution, fast_forward=fast
        )
        runs.append(engine.run(first, last))
    a, b = runs
    assert a.fills == b.fills
    np.testing.assert_array_equal(a.sessions.equity, b.sessions.equity)
    np.testing.assert_array_equal(a.bar_equity, b.bar_equity)


def test_notional_sizing_buys_at_the_fill(store: Store) -> None:
    free = FillModel(slippage_bps=0.0, commission_per_share=0.0)
    r = execute(store, spec(SMA_D1, fill=free.model_dump()))
    sides: list[str] = r.fills["side"].astype(str).tolist()
    quantities: list[int] = r.fills["quantity"].astype(int).tolist()
    prices: list[float] = r.fills["price"].astype(float).tolist()
    held = 0
    for side, quantity, price in zip(sides, quantities, prices, strict=True):
        signed = quantity if side == "buy" else -quantity
        reverses = held != 0 and (held > 0) != (signed > 0)
        assert quantity - (abs(held) if reverses else 0) == math.floor(100_000.0 / price)
        held += signed


def test_share_sizing_trades_fixed_quantity(store: Store) -> None:
    name, params = SMA_D1
    r = execute(store, spec((name, {**params, "sizing": "shares", "shares": 250})))
    assert set(r.fills["quantity"]) <= {250, 500}
    assert r.fills["quantity"].iloc[0] == 250


def test_sma_cross_never_decides_on_the_first_ready_bar(store: Store) -> None:
    params = SMA_D1[1]
    early = spec(SMA_D1).model_copy(update={"start": date(2024, 1, 2)})
    r = execute(store, early)
    ready = r.bars["time"][r.bars[f"sma_{params['slow']}"].notna()].iloc[0]
    if len(r.orders):
        assert r.orders["submitted"].min() > ready + pd.Timedelta(days=1)


def test_sma_tie_within_rounding_has_no_direction() -> None:
    assert direction_of(216.78700000000077, 216.787) == 0
    assert direction_of(216.78699999999998, 216.787) == 0
    assert direction_of(216.79, 216.787) == 1
    assert direction_of(216.70, 216.787) == -1


def reference_high_breakout(
    o: npt.NDArray[np.float64],
    h: npt.NDArray[np.float64],
    lo: npt.NDArray[np.float64],
    c: npt.NDArray[np.float64],
    stop_pct: float,
) -> list[tuple[int, float, int | None, float | None]]:
    trades: list[tuple[int, float, int | None, float | None]] = []
    e, entry, intrabar, n = 0, float(o[0]), False, len(o)
    while True:
        stop = entry * (1 - stop_pct / 100)
        exit_at: tuple[int, float] | None = None
        for i in range(e, n):
            if i == e:
                if (c[i] if intrabar else lo[i]) <= stop:
                    exit_at = (i, stop)
            elif o[i] <= stop:
                exit_at = (i, float(o[i]))
            elif lo[i] <= stop:
                exit_at = (i, stop)
            if exit_at is not None:
                break
        if exit_at is None:
            trades.append((e, entry, None, None))
            return trades
        x, price = exit_at
        trades.append((e, entry, x, price))
        level = max(entry, float(h[e : x + 1].max()))
        for i in range(x + 1, n):
            if o[i] > level:
                e, entry, intrabar = i, float(o[i]), False
                break
            if h[i] > level:
                e, entry, intrabar = i, level, True
                break
        else:
            return trades


@pytest.mark.parametrize("strategy", [HB_5M, HB_1H], ids=["5m", "1h"])
def test_high_breakout_matches_the_rule(store: Store, strategy: tuple[str, dict[str, object]]) -> None:
    free = FillModel(slippage_bps=0.0, commission_per_share=0.0)
    s = spec(strategy, fill=free.model_dump()).model_copy(update={"ticker": Ticker("SPY")})
    r = execute(store, s)
    tf = Timeframe(str(strategy[1]["timeframe"]))
    bars = store.bars(Ticker("SPY"), tf)
    first, last = session_range(store.sessions(), s.start, s.end)
    k0 = int(np.searchsorted(bars.session, first, side="left"))
    k1 = int(np.searchsorted(bars.session, last, side="right"))
    o, h, lo, c = (a[k0:k1] for a in (bars.open, bars.high, bars.low, bars.close))
    expected = reference_high_breakout(o, h, lo, c, float(str(strategy[1]["stop_loss_pct"])))
    at = {t: i - k0 for i, t in enumerate(bars.time_ns.tolist())}
    fills = r.fills.assign(bar=[at[int(t.value)] for t in r.fills["time"]])
    buys, sells = fills[fills["side"] == "buy"], fills[fills["side"] == "sell"]
    assert len(expected) > 3
    assert buys["bar"].tolist() == [e for e, _, _, _ in expected]
    assert buys["price"].to_numpy() == pytest.approx([p for _, p, _, _ in expected])
    closed = [(x, p) for _, _, x, p in expected if x is not None]
    assert sells["bar"].tolist() == [x for x, _ in closed]
    assert sells["price"].to_numpy() == pytest.approx([p for _, p in closed])


@pytest.mark.parametrize("strategy", [HB_5M, LB_5M], ids=["high", "low"])
def test_breakouts_refuse_intraday(strategy: tuple[str, dict[str, object]]) -> None:
    with pytest.raises(ValueError, match="holds overnight"):
        spec(strategy, intraday=True)


def reference_low_breakdown(
    o: npt.NDArray[np.float64],
    h: npt.NDArray[np.float64],
    lo: npt.NDArray[np.float64],
    c: npt.NDArray[np.float64],
    stop_pct: float,
) -> list[tuple[int, float, int | None, float | None]]:
    trades: list[tuple[int, float, int | None, float | None]] = []
    e, entry, intrabar, n = 0, float(o[0]), False, len(o)
    while True:
        stop = entry * (1 + stop_pct / 100)
        exit_at: tuple[int, float] | None = None
        for i in range(e, n):
            if i == e:
                if (c[i] if intrabar else h[i]) >= stop:
                    exit_at = (i, stop)
            elif o[i] >= stop:
                exit_at = (i, float(o[i]))
            elif h[i] >= stop:
                exit_at = (i, stop)
            if exit_at is not None:
                break
        if exit_at is None:
            trades.append((e, entry, None, None))
            return trades
        x, price = exit_at
        trades.append((e, entry, x, price))
        level = min(entry, float(lo[e : x + 1].min()))
        for i in range(x + 1, n):
            if o[i] < level:
                e, entry, intrabar = i, float(o[i]), False
                break
            if lo[i] < level:
                e, entry, intrabar = i, level, True
                break
        else:
            return trades


TIGHT = [
    ("low_breakdown", {"timeframe": "5m", "stop_loss_pct": 0.1}),
    ("low_breakdown", {"timeframe": "1h", "stop_loss_pct": 0.1}),
]


@pytest.mark.parametrize("strategy", TIGHT, ids=["5m", "1h"])
def test_low_breakdown_matches_the_rule(store: Store, strategy: tuple[str, dict[str, object]]) -> None:
    free = FillModel(slippage_bps=0.0, commission_per_share=0.0)
    s = spec(strategy, fill=free.model_dump()).model_copy(update={"ticker": Ticker("SPY")})
    r = execute(store, s)
    tf = Timeframe(str(strategy[1]["timeframe"]))
    bars = store.bars(Ticker("SPY"), tf)
    first, last = session_range(store.sessions(), s.start, s.end)
    k0 = int(np.searchsorted(bars.session, first, side="left"))
    k1 = int(np.searchsorted(bars.session, last, side="right"))
    o, h, lo, c = (a[k0:k1] for a in (bars.open, bars.high, bars.low, bars.close))
    expected = reference_low_breakdown(o, h, lo, c, float(str(strategy[1]["stop_loss_pct"])))
    at = {t: i - k0 for i, t in enumerate(bars.time_ns.tolist())}
    fills = r.fills.assign(bar=[at[int(t.value)] for t in r.fills["time"]])
    sells, buys = fills[fills["side"] == "sell"], fills[fills["side"] == "buy"]
    assert len(expected) > 3
    assert sells["bar"].tolist() == [e for e, _, _, _ in expected]
    assert sells["price"].to_numpy() == pytest.approx([p for _, p, _, _ in expected])
    closed = [(x, p) for _, _, x, p in expected if x is not None]
    assert buys["bar"].tolist() == [x for x, _ in closed]
    assert buys["price"].to_numpy() == pytest.approx([p for _, p in closed])


def test_sma_signals_are_the_decision_bars(store: Store) -> None:
    r = execute(store, spec(SMA_D1))
    decided = r.orders[(r.orders["tag"] == "signal") & (r.orders["status"] == "filled")]["submitted"]
    assert len(decided) > 1
    assert r.metrics.first_signal == decided.min().to_pydatetime()
    assert r.metrics.last_signal == decided.max().to_pydatetime()


def test_breakout_signals_exclude_protective_stops_and_start_on_the_clock(store: Store) -> None:
    s = spec(HB_1H).model_copy(update={"ticker": Ticker("SPY")})
    r = execute(store, s)
    assert r.orders["submitted"].min() >= pd.Timestamp(s.start)
    buys = r.fills[r.fills["side"] == "buy"]["time"]
    assert len(buys) > 3 and (r.fills["side"] == "sell").any()
    assert r.metrics.first_signal == buys.min().to_pydatetime()
    assert r.metrics.last_signal == buys.max().to_pydatetime()


def test_orders_record_their_origin(store: Store) -> None:
    r = execute(store, spec(HB_1H).model_copy(update={"ticker": Ticker("SPY")}))
    by_tag = r.orders.groupby("tag")["origin"].unique().to_dict()
    assert list(by_tag["stop"]) == ["stop_loss"]
    assert set(by_tag["breakout"]) == {"strategy"} and set(by_tag["initial"]) == {"strategy"}


def test_buy_and_hold_enters_at_the_first_open_and_matches_the_benchmark(store: Store) -> None:
    s = spec(SMA_D1).model_copy(update={"ticker": Ticker("SPY")})
    r = execute(store, s)
    first_open = float(r.bars["open"].iloc[0])
    last_close = float(r.bars["close"].iloc[-1])
    assert r.metrics.buy_hold_return_pct == pytest.approx(100 * (last_close / first_open - 1))
    held = next(b for b in compare(store, Ticker("SPY"), r.equity).benchmarks if b.ticker == "SPY")
    assert held.total_return_pct == pytest.approx(r.metrics.buy_hold_return_pct)

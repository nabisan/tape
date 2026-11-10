# tape: architecture

A bar-level backtesting service for US equities. Computation runs on an always-on server; a web UI lets the team pick a ticker, a date range and two strategies, and compare them side by side. The design target is many strategies and parameter sweeps over ~500 tickers, so every layer is typed, versioned and cached by content.

## 1. Source data

Facts measured on the source files (AAPL, `~/backtesting_data` on EC2):

| Property | Value |
|---|---|
| Layout | one Parquet file per ticker, `{TICKER}.parquet` |
| Schema | `time: timestamp[ms]` (tz-naive), `open, high, low, close: double`, `volume: uint64` |
| Resolution | 1-minute bars, sorted, no duplicate timestamps, min step 60 000 ms |
| Sparsity | only minutes with a trade exist; a missing minute means no trade |
| Session coverage | extended hours, 04:00–20:00 exchange-local (US/Eastern) |
| Span | 2018-05-01 → 2026-04-30 |
| Adjustment | split-adjusted and spin-off-adjusted at source; not dividend-adjusted |
| Size | ~1.6M rows per liquid ticker; ~800M rows across the universe |

Consequences:

- Bars are never forward-filled. Resampling aggregates only the minutes that exist.
- Regular-hours membership is decided by the exchange calendar, not by a fixed 09:30–16:00 window, because extended-hours bars exist on half-days after 13:00.
- `core/corporate_actions.csv` is kept as the provenance record of spin-off factors already applied at source. It is never applied again.

The source location is configuration (`TAPE_SOURCE_DIR`). Nothing in the code assumes a host or a path.

## 2. System overview

```
 source 1m parquet ──► catalog build ──► store (bars, stats, features; versioned)
                                            │
 Streamlit UI ──HTTP──► API ──► job table ──► worker pool ──► engine (pure)
      ▲                  │                                      │
      └────── reads ── result store (content-addressed) ◄───────┘
```

| Layer | Responsibility | Side effects |
|---|---|---|
| `tape.core` | domain types, versions, canonical hashing | none |
| `tape.calendar` | XNYS sessions, regular open/close per session | none |
| `tape.catalog` | source → validated store; bar sets per timeframe | writes store |
| `tape.features` | typed feature specs, registry, kernels, materialization | writes store |
| `tape.engine` | execution, ledger, metrics | none |
| `tape.strategies` | typed strategy specs and signal functions | none |
| `tape.service` | API, job table, workers, result store | writes results |
| `tape.ui` | Streamlit client; submits jobs, renders artifacts | none |

`calendar`, `engine`, `strategies` and feature kernels are pure: same inputs, same outputs, no I/O. All effects live in `catalog`, `features` materialization and `service`.

## 3. Types

Two type systems, one per boundary.

**Specs and configuration** are frozen Pydantic v2 models: `FeatureSpec`, `StrategySpec`, `ExecutionSpec`, `RunSpec`, `JobSpec`. They validate on construction, serialize to canonical JSON (sorted keys, fixed float repr) for hashing, and export JSON Schema, which the UI renders as forms. FastAPI consumes them directly.

**Hot-path data** is fixed-dtype numpy: `int64` ns timestamps, `float64` prices, `uint64` volume, `int32` session index. Kernels are Numba-compiled and receive arrays only.

Domain types:

- Enums: `Timeframe {M1, M5, M15, H1, H4, D1}`, `Field {OPEN, HIGH, LOW, CLOSE, VOLUME}`, `Side {LONG, SHORT}`, `Direction {LONG_SHORT, LONG_ONLY, SHORT_ONLY}`, `Sizing {FIXED_SHARES, FIXED_NOTIONAL}`.
- Distinct named types: `Ticker`, `DataVersion`, `FeatureId`, `JobKey`.
- mypy strict, ruff, no `Any` across module boundaries.

## 4. Versioning

Every layer carries a version. Every result records all of them.

| Version | Bumped when | Effect |
|---|---|---|
| `data_version` | source files change | hash of the source manifest; new store namespace |
| `catalog_schema` | store layout changes | readers refuse a mismatch; explicit migration `vN → vN+1` |
| `FeatureKind.version` | a feature's computation changes | new `FeatureId`; old values remain for old results |
| `StrategyKind.version` | a strategy's logic changes | new job keys |
| `engine_version` | fill, cost or ledger rules change | new job keys |
| `result_schema` | artifact format changes | readers refuse a mismatch |

A result is valid only for the exact version tuple it records. Nothing is overwritten in place.

## 5. Calendar

`exchange_calendars`, calendar `XNYS`. The schedule is returned in UTC; the catalog converts each session's open and close to America/New_York and drops the zone to match the source timestamps.

Used once, in the catalog build. Each bar is stamped with its session index and a regular-hours flag; nothing downstream calls the calendar.

Cross-check at build time against SPY: every calendar session in the span must have SPY bars, and every SPY date must be a calendar session. Any mismatch fails the build.

## 6. Catalog

Input: source directory. Output: `store/catalog-v{schema}/{data_version}/`.

```
manifest.json                      data version, calendar, session span, one report per ticker
sessions.parquet                   day, open, close (local wall-clock ns)
bars/timeframe={tf}/ticker={TICKER}/part-0.parquet   time, session, open, high, low, close, volume, count
```

`data_version = digest(source file digests, calendar library version)`. Unchanged sources mean an unchanged version, and the build returns immediately. A build writes to a temporary directory and renames it into place only after every check passes.

Per ticker, in a worker process:

1. Re-hash the file and fail the build if it changed since the version was computed.
2. Structural checks reject the ticker: exact schema, no null time or volume, strictly increasing time, minute-aligned time.
3. Row checks drop and count: non-finite prices, non-positive prices, `low ≤ min(open, close)` and `high ≥ max(open, close)` violated beyond float rounding.
4. Keep regular-hours minutes only, stamped with their session index.
5. Build every `Timeframe`, labelled by bar open, anchored at the session open and cut at the session close: on a full day the last H1 bar is 15:30–16:00 and the H4 bars are 09:30–13:30 and 13:30–16:00; a half-day ends at 13:00. `D1` is one bar per session. `count` is the number of minutes in the bar, since minutes without trades do not exist in the source.
6. Report: source rows, regular rows, drops by reason, first and last session, missing sessions, days with bars that are not sessions, bars per timeframe.

The reference ticker (SPY) must have bars in every calendar session of the span and none outside it, otherwise the build fails. Other tickers with gaps are built and their gaps listed. Rejected tickers stay in the manifest with the reason and have no bars.

CLI: `tape catalog build [--ticker T ...] [--workers N]`, `tape catalog show [--data-version D]`.

## 7. Feature store

A feature is a typed spec, not a column name:

```python
Sma(source=Field.CLOSE, window=50, timeframe=Timeframe.H1)
ChannelHigh(window=20, timeframe=Timeframe.M15)
```

`FeatureId = hash(kind, kind_version, canonical params, data_version)`.

**Registry.** Each `FeatureKind` registers its spec model, version, kernel and warm-up length. Unknown kinds fail at spec validation.

**TA-Lib.** Indicators TA-Lib provides (SMA, EMA, MAX, MIN, ATR, ...) come from TA-Lib, through one adapter module that is the only importer of `talib`. The adapter pins conventions TA-Lib gets wrong for us: NaN, not 0 or 1, on undefined values, and inputs validated free of interior NaN, since a single interior NaN poisons every later TA-Lib output.

**Sufficient statistics.** Per ticker and timeframe, prefix sums of `close`, `close²`, `volume`, `close·volume` are stored once. Any rolling mean, variance, z-score or VWAP of any window is a difference of two prefix entries: O(1) per bar, no materialization needed.

**Materialized features.** Kinds that are not prefix-decomposable (rolling max/min, EMA, anything path-dependent) are computed by TA-Lib where it has the indicator, otherwise by Numba kernels, and stored under their `FeatureId`. A declared grid of common specs is precomputed by the workers; any other spec is materialized on first request and kept.

**Causality.** One generic test runs over every registered kind: perturbing bars from `t+1` onward must leave values at `t` unchanged. A kind that fails is not registered.

**Warm-up.** Values before a feature's warm-up length are NaN and the engine treats NaN as "no decision".

## 8. Strategy contract

```python
class StrategyKind(Protocol[P]):
    name: ClassVar[str]
    version: ClassVar[int]
    params: type[P]  # frozen Pydantic model

    def requires(self, p: P) -> list[FeatureSpec]: ...
    def target(self, f: FeatureView, p: P) -> NDArray[float64]: ...
```

- `target` returns the desired position in `[-1, 1]` at the close of each decision bar. It never sees fills, cash or the ledger.
- Strategies read features only through `FeatureView`, resolved from `requires`. They do not compute indicators.
- Exit rules that are not signals (stop, target, time) are declared on `ExecutionSpec` and run by the engine.
- Registration is one decorator; the UI form is generated from `params`.

Initial strategies:

| Strategy | Params | Target |
|---|---|---|
| SMA cross | `fast`, `slow` (`fast < slow`), timeframe | `+1` while `SMA_fast > SMA_slow`, `−1` while below |
| N-bar channel breakout | `entry_window N`, `exit_window M ≤ N`, timeframe | `+1` when close > highest high of the previous `N` bars; `−1` when close < lowest low of the previous `N`; a long exits to flat when close < lowest low of the previous `M`, a short when close > highest high of the previous `M`. `M = N` is stop-and-reverse. |

Channels use the previous `N` bars, excluding the current one, so the comparison is causal.

## 9. Engine

Inputs: execution-resolution bars (M1), decision-resolution bars and target array, `ExecutionSpec`. Output: `Ledger`.

**Decision vs execution resolution.** Strategies decide on their timeframe; fills, stops and square-off execute on the M1 bars underneath. A decision made at the close of a decision bar fills at the open of the first M1 bar at or after that close.

**Execution rules (engine_version 1):**

- Fill price: open of the fill bar, adjusted by slippage of `slippage_bps` against the trade.
- Costs: `commission_per_share × shares` on every fill.
- Sizing: fixed shares, or fixed notional `floor(notional / fill price)`; a size of zero shares means no trade.
- Direction filter applied to targets before execution.
- Intraday mode: entries only inside `[entry_from, squareoff)`; any open position is closed at the open of the first M1 bar at or after `squareoff`, or at the close of the session's last regular-hours bar if none exists.
- Stops and targets, when set, are checked against M1 high/low; a gap through the level fills at the bar open.
- Volume participation: fills above `max_participation × bar volume` are flagged on the trade, not resized (v1).

**Ledger.** Fills → positions → M1 mark-to-market (previous close to open on the held position, open to close on the new position) → per-trade records → per-session P&L. The kernel is a single Numba loop over M1 bars.

**Metrics** are computed from the ledger only: net P&L, return, CAGR, Sharpe and Sortino on session returns (flat sessions included), max drawdown and duration, exposure, turnover, trade count, win rate, average win/loss, payoff ratio, profit factor, MAE/MFE distribution, total costs, cost as a share of gross P&L, and buy-and-hold and SPY over the same sessions.

## 10. Service

**Jobs.** A `RunSpec` is one strategy on one ticker over one date range with one `ExecutionSpec`. Its `JobKey` is the hash of the spec plus the version tuple. The UI's side-by-side request is two `RunSpec`s; changing strategy B leaves strategy A's result cached.

**API** (FastAPI, localhost only): submit run, job status, fetch artifacts, list tickers and data range, list strategies with their param schemas.

**Job table.** SQLite in WAL mode: key, spec, status, timestamps, error. An existing key with status `done` returns immediately.

**Workers.** A process pool (cores − 1) pulls queued jobs, loads store arrays by memory map, runs the engine, writes artifacts atomically (temp + rename). The pool also materializes the precomputed feature grid when idle. Redis or Ray replace SQLite and the local pool only when sweeps require more than one machine.

**Result store.** `results/{job_key}/`: `spec.json`, `versions.json`, `metrics.json`, `trades.parquet`, `sessions.parquet`, `equity.parquet`. Immutable once written.

**Logging.** `tape.log` on top of stdlib `logging`, so third-party loggers (uvicorn, numba) share one format: `2026-11-10T12:58:40.389883000Z [INFO] Catalog: built ticker=AAPL bars=780000`. UTC nanosecond timestamp in bold white, level in colour, component, then structured `key=value` fields. Colour only on a TTY; under systemd the plain lines go to journald.

## 11. UI

Streamlit, thin client. It never imports the engine.

Flow: ticker → date range (bounded by the store's span for that ticker) → strategy A and its params → strategy B and its params → execution settings → run.

Views: side-by-side metrics table; equity and drawdown overlay of A and B; trade sheets with filters; price chart with both strategies' fills and their features; monthly P&L grid per strategy; export to CSV and Excel.

## 12. Deployment

- systemd units: `tape-api`, `tape-workers`, `tape-ui`; restart on failure, start on boot.
- nginx terminates TLS and authenticates each team member; proxies `/` to the UI and nothing else. API and UI bind to localhost.
- Store and results on the instance's data volume; paths from environment (`TAPE_SOURCE_DIR`, `TAPE_STORE_DIR`, `TAPE_RESULTS_DIR`).
- Python 3.14 and every dependency pinned by `uv.lock`; TA-Lib and Numba ship prebuilt wheels for macOS arm64 and Linux x86_64/aarch64, so nothing compiles on the server.
- Development happens on the Mac; `deploy/sync.sh <host>` rsyncs the tree to `~/tape/app`, runs `uv sync --frozen` and `tape env` on the host.

## 13. Correctness gates

| Gate | Asserts |
|---|---|
| Catalog validation | schema, order, uniqueness, OHLC consistency, calendar vs SPY |
| Feature causality | perturbing the future never changes the past, for every kind |
| Strategy causality | same, on `target` |
| Ledger reconciliation | sum of trade P&L equals equity change; costs match fill by fill |
| Golden runs | fixed fixtures per strategy reproduce stored ledgers exactly |
| Determinism | same `JobKey` produces byte-identical artifacts |
| Static | mypy strict, ruff, in CI |

## 14. Repository

```
src/tape/
  core/          types, versions, hashing
  calendar/
  catalog/
  features/      registry, kinds, kernels, store
  engine/        execution, ledger, metrics
  strategies/    registry, sma_cross, channel_breakout
  service/       api, jobs, workers, results
  ui/
  log.py
tests/
deploy/          systemd units, nginx config
reference/corporate_actions.csv
```

The existing prototype (`app.py`, `core/`, `strategies/`, `build_cache.py`) targets a 1-second text-timestamp format that the source does not have and computes inside the UI process. It is removed in step 1.

## 15. Build order

Each step is reviewed before the next begins.

1. Repository reset, tooling (uv, mypy, ruff, pytest, CI), logging, settings, `tape env`, deploy sync. (done)
2. Core types and canonical hashing; calendar and catalog build with validation report. (done; next: run on the real source)
3. Feature registry, prefix statistics, Numba kernels, causality tests.
4. Engine, ledger, metrics, reconciliation and determinism tests.
5. SMA cross and channel breakout with golden runs.
6. Service: API, job table, workers, result store.
7. UI.
8. Deployment: systemd, nginx, team login.

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

**Hot-path data** is fixed-dtype numpy: `int64` ns timestamps, `float64` prices, `uint64` volume, `int32` session index. Feature kernels are TA-Lib calls on these arrays; the engine loop works on plain lists of them.

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

## 7. Feature catalog

A feature is a typed spec, `FeatureSpec`, discriminated by `FeatureKind` (`SMA`, `EMA`, `CHANNEL_HIGH`, `CHANNEL_LOW`, `ATR`), with a `Window` and, where relevant, a `Source`. `FeatureId = digest(spec, kind version)`. Kernels are TA-Lib, behind one adapter (`tape.features.ta`) that refuses non-finite input.

**Catalog.** `features/catalog.parquet` in the store holds one row per materialized feature per ticker and timeframe: feature id, kind, spec, kind version, timeframe, ticker, rows, session range, origin (`built` or `adopted`), and the file and column holding the values. The engine, API and UI query the catalog; nothing infers features from file or column names.

**Adoption.** `tape features adopt <dir>` registers a precomputed Hive dataset (`bars/` and `features/` per timeframe and ticker) in place, without copying. A ticker and timeframe is registered only if its bars equal the catalog's exactly and the source was built with the same TA-Lib; every 20th file is recomputed and compared, and any difference fails the adoption. The precompute's column naming is read only inside the adoption module.

**Causality.** One generic test runs over every kind: perturbing bars from `t+1` onward leaves values at `t` unchanged. Channels use the previous `N` bars, excluding the current one.

## 8. Strategy contract

A strategy is a class with typed identity and a typed parameter space:

| Member | Meaning |
|---|---|
| `name`, `title`, `version` | registry key, display name, logic version (part of the run key) |
| `config_type` | frozen Pydantic config: `timeframe`, `direction`, `notional`, plus the strategy's own parameters as domain types (`Window`, enums) |
| `parameters: ParameterSpace` | which config fields are feature windows (`WindowParameter(kinds=...)`) and their ordering constraints (`Ordered(lower, upper, strict)`) |
| `on_start`, `on_bar`, `on_fill`, `on_stop` | event callbacks, Nautilus style |

`ParameterSpace.requires(config)` gives the exact `FeatureSpec`s a run needs. `ParameterSpace.resolve(catalog, ticker)` gives, per timeframe, the windows actually materialized for that ticker that satisfy every constraint. The API serves the resolved space and the config schema; the UI renders any strategy from them, so adding a strategy needs no UI code.

Strategies reach features only through `indicator(spec)`, place orders through `submit` (market, limit, stop), and use `target` and `flatten` helpers. They never see the ledger.

Initial strategies: SMA cross (`fast < slow`, enters only on a cross) and N-bar channel breakout (entry window `N`, exit window `M <= N`, resting stops or close triggers).

## 9. Engine

An event-driven loop modelled on NautilusTrader's bar execution, in Python:

- **Decision vs execution resolution.** Strategies decide on their timeframe; orders fill on the 1-minute bars underneath. A decision at the close of a bar fills from the next minute.
- **Bar matching.** Each minute becomes the price path open, then high and low (adaptive: the extreme nearer the open first), then close. A gap at the open fills at the open. Market orders fill at the open with slippage; limits fill at the limit (or the better open on a gap, with a touch probability); stops trigger at the stop (or the open on a gap) with slippage.
- **Costs.** Commission per share with a minimum, slippage in bps against the trade (`FillModel`).
- **Intraday mode.** Entries only from open plus a delay until square-off; open positions are closed a configurable number of minutes before each session's close, so half-days square off before 13:00.
- **Portfolio.** Netting position, average price, realized P&L, trades from flat to flat with flips handled; MAE and MFE from 1-minute extremes.
- **Fast-forward.** While only resting orders are working, NumPy finds the next minute that can touch any order or the next bar or session boundary, and the loop jumps there. Results are identical to the slow path (tested).
- **Metrics** come from the session-level equity curve and the trade list: return, CAGR, volatility, Sharpe, Sortino, max drawdown and duration, Calmar, exposure, trade statistics, commission, buy and hold.

`FeaturePolicy` on a run: `precomputed` (default) refuses a run whose required features are not in the catalog, listing them; `compute` computes them for research.

## 10. Service

**Jobs.** A `RunSpec` is one strategy on one ticker over one date range with one `ExecutionSpec`. Its `JobKey` is the hash of the spec plus the version tuple. The UI's side-by-side request is two `RunSpec`s; changing strategy B leaves strategy A's result cached.

**API** (FastAPI, localhost only): submit run, job status, fetch artifacts, list tickers and data range, list strategies with their param schemas.

**Workers.** A process pool (cores - 1) runs the engine and writes artifacts atomically (temp + rename). In-flight runs with the same key are shared. Redis or Ray replace the local pool only when sweeps need more than one machine.

**Result store.** `results/{job_key}/`: `spec.json`, `versions.json`, `metrics.json`, `trades.parquet`, `sessions.parquet`, `equity.parquet`. Immutable once written.

**Logging.** `tape.log` on top of stdlib `logging`, so third-party loggers (uvicorn, numba) share one format: `2026-11-10T12:58:40.389883000Z [INFO] Catalog: built ticker=AAPL bars=780000`. UTC nanosecond timestamp in bold white, level in colour, component, then structured `key=value` fields. Colour only on a TTY; under systemd the plain lines go to journald.

## 10a. Feature build and sweeps

**Build.** `FeatureGrid` is a typed set of timeframes and `FeatureSpec`s; `DEFAULT_GRID` is SMA and EMA 5 to 200, channels 10, 20 and 55, ATR 14 and volume SMA 20 on 5m to 1d. `tape features build` computes, for every ticker and timeframe, only the specs the catalog does not already hold (`--force` rebuilds), writes them under `features/built/` with columns named by feature id, and registers them with origin `built`.

**Sweeps.** `SweepSpec` names tickers, dates, strategy, timeframe, a grid of parameter values, fixed parameters, execution and feature policy; its key is its digest. `expand` turns it into `RunSpec`s: under the precomputed policy each window parameter's domain is the catalog's resolved domain for that ticker (intersected with any explicit grid), ordering constraints are applied, and tickers without coverage are reported as skipped. Every run is an ordinary run with its own key, so sweeps share results with single runs. The API runs sweeps as background jobs on the worker pool with bounded concurrency (`POST /sweeps`, `GET /sweeps/{key}`, `GET /sweeps/{key}/summary`); `tape sweep` does the same from the command line. The summary is one row per run: ticker, swept parameters, key metrics, run key.

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
3. Feature kinds, TA-Lib adapter, feature catalog, adoption, causality tests. (done)
4. Engine, ledger, metrics, reconciliation and determinism tests. (done)
5. SMA cross and channel breakout, parameter spaces resolved from the catalog. (done)
6. Service: API, workers, result store, feature policy. (done)
7. UI: single run or A/B comparison, rendered from strategy parameter spaces. (done)
8. Deployment: systemd, nginx, optional team login. (done)
9. Feature build from a typed grid, then parameter sweeps. (done)

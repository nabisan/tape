import marimo

__generated_with = "0.25.2"
app = marimo.App(
    width="full", app_title="tape · Simulation", css_file="../lab.css", html_head_file="../head.html"
)

with app.setup:
    import html
    import os

    import marimo as mo
    import pandas as pd
    from pydantic import ValidationError

    from tape import book
    from tape.benchmarks import REFERENCE
    from tape.core.hashing import Digest
    from tape.lab import markup, views
    from tape.lab.form.model import Form, SweepRequest
    from tape.lab.layout import split
    from tape.lab.nav import PAGES, link, sidebar
    from tape.lab.pages.report import app as report_app
    from tape.lab.widgets.paths import Paths
    from tape.ui import charts
    from tape.ui.client import ApiError, Client
    from tape.ui.theme import DOWN, INK, MUTED

    api = Client(os.environ.get("TAPE_API_URL", "http://127.0.0.1:8000"))


@app.cell
def _():
    mo.Html(markup.STYLE)


@app.cell
def _():
    params = mo.query_params()
    strategies = api.strategies()
    catalog = api.catalog()
    _stored = api.stored_sweeps()
    _first = str(params["sweep"]) if params["sweep"] else (_stored[0].key if _stored else None)
    form = Form(page="simulation", pages=PAGES)
    get_submission, set_submission = mo.state(form.submission)
    form.observe(lambda _: set_submission(form.submission), names=["submission"])
    get_current, set_current = mo.state(_first)
    get_finished, set_finished = mo.state(0)
    get_pinned, set_pinned = mo.state(
        {k: v for k, v in params.to_dict().items() if k != "sweep" and isinstance(v, str)}
    )

    def unpin() -> None:
        for _k in [k for k in params.to_dict() if k != "sweep"]:
            params.remove(_k)
        set_pinned({})

    sidebar(form)
    return (
        catalog,
        form,
        get_current,
        get_finished,
        get_pinned,
        get_submission,
        params,
        set_current,
        set_finished,
        strategies,
        unpin,
    )


@app.cell
def _(form, get_submission, params, set_current, unpin):
    _sent = get_submission()
    mo.stop(not _sent)
    form.message = ""
    try:
        _spec = SweepRequest.model_validate(_sent).spec()
    except ValidationError as _e:
        form.message = "; ".join(str(err["msg"]) for err in _e.errors())
        mo.stop(True)
    form.busy = True
    try:
        _key = api.start_sweep(_spec).key
    except ApiError as _e:
        form.message = str(_e)
        mo.stop(True)
    finally:
        form.busy = False
    unpin()
    params.set("sweep", _key)
    set_current(_key)
    return


@app.cell
def _(get_current, get_finished, params, set_current, strategies, unpin):
    get_finished()
    _stored = [s for s in api.stored_sweeps() if s.strategy in strategies]
    _options = {views.choice(s, strategies[s.strategy].title): s.key for s in _stored}
    _current = get_current()
    _initial = next((name for name, k in _options.items() if k == _current), None)

    def _pick(k: str | None) -> None:
        if k is not None:
            unpin()
            params.set("sweep", k)
            set_current(k)

    picker = mo.ui.dropdown(
        _options, value=_initial, searchable=True, label="Simulation", full_width=True, on_change=_pick
    )
    picker if _options else None
    return


@app.cell
def _():
    get_running, set_running = mo.state(False)
    return get_running, set_running


@app.cell
def _(get_running):
    poll = mo.ui.refresh(options=["2s"], default_interval="2s")
    mo.Html(f'<div class="t-hidden">{poll}</div>') if get_running() else None
    return (poll,)


@app.cell
def _(get_current, get_running, poll, set_finished, set_running):
    poll.value
    _key = get_current()
    mo.stop(
        _key is None,
        mo.Html(
            markup.notice("Choose the universe, a strategy and the values to test, then run the simulation.")
        ),
    )
    try:
        _status = api.sweep(_key)
    except ApiError:
        _status = None
    _running = _status is not None and _status.state == "running"
    if _running != get_running():
        set_running(_running)
        if not _running:
            set_finished(lambda n: n + 1)
    if _status is not None:
        mo.stop(_running, mo.Html(views.progress(_status)))
        mo.stop(
            _status.state == "stopped",
            mo.Html(
                markup.notice(
                    f"Simulation {_key[:8]} was stopped after {_status.done:,} of {_status.total:,} runs."
                )
            ),
        )
        mo.stop(_status.state == "failed", mo.callout(f"Simulation failed: {_status.error}", kind="danger"))
    done = Digest(_key)
    return (done,)


@app.cell
def _(done, get_pinned, strategies):
    key = done
    try:
        spec = api.sweep_spec(key)
        frame = api.sweep_summary(key)
    except ApiError as e:
        mo.stop(True, mo.Html(markup.notice(str(e))))
    mo.stop(frame.empty, mo.Html(markup.notice("The simulation produced no runs.")))
    info = strategies[spec.strategy]
    best = frame.sort_values("sharpe", ascending=False, na_position="last").reset_index(drop=True)
    _pins = book.pins(best, get_pinned())
    _linked = book.narrow(best, _pins)
    _anchor = _linked if _pins and not _linked.empty else best
    _named = views.book_parameters(_anchor.iloc[0], best)
    _source = "parameters of the link" if _anchor is _linked else "parameters of the top run by Sharpe"
    _source = f"{_named} ({_source})" if _named else "all runs"
    try:
        _folio = api.sweep_portfolio(key, str(_anchor["key"].iloc[0]))
    except ApiError:
        _folio = None
    mo.Html(views.header(key, spec, frame, info.title, folio=_folio, source=_source))
    return best, info, key, spec


@app.cell
def _(best, catalog, key, spec):
    paths = Paths.of(key, [str(k) for k in best["key"]], views.leaders(best, spec), REFERENCE)
    spread = charts.distribution(best["total_return_pct"], "%")
    spread.update_layout(dragmode="select", selectdirection="h")
    returns = mo.ui.plotly(spread, config=charts.CONFIG)
    profitable = float((best["total_return_pct"] > 0).mean())
    _cash = f"${spec.execution.initial_cash:,.0f} per run"
    _equities = views.venue(catalog, spec.tickers[0]).venue == "XNYS"
    _reference = f" &middot; dashed: {REFERENCE}, open to close" if _equities else ""
    split(
        mo.vstack(
            [mo.Html(markup.title("Equity, all runs", f"cumulative return on {_cash}{_reference}")), paths]
        ),
        mo.vstack(
            [
                mo.Html(
                    markup.title(
                        "Total return, all runs",
                        f"{len(best):,} runs on {_cash} &middot; {profitable:.0%} profitable"
                        " &middot; drag to filter",
                    )
                ),
                returns,
            ]
        ),
    )
    return (returns,)


@app.cell
def _(best, get_pinned):
    pins = book.pins(best, get_pinned())
    shortlist = book.narrow(best, pins)
    return pins, shortlist


@app.cell
def _(best, catalog, info, pins, returns, shortlist, spec, unpin):
    chosen = views.within(shortlist, "total_return_pct", returns.ranges.get("x"))
    shown = views.sheet(chosen, spec, info.exits, views.venue(catalog, spec.tickers[0]).periods)
    caption = f"{len(chosen):,} of {len(best):,}" if len(chosen) < len(best) else f"{len(best):,}"
    runs = mo.ui.table(
        shown,
        selection="single",
        page_size=25,
        format_mapping=views.formats(shown),
        freeze_columns_left=[views.RANK, "Ticker"],
        hidden_columns=["key"],
        show_column_summaries=False,
        show_data_types=False,
    )
    _named = views.pinned(pins, best)
    unpinner = mo.ui.button(label=f"{_named}  \u00d7", on_click=lambda _: unpin()) if _named else None
    _none = mo.Html(markup.notice(f"No runs in this simulation have {html.escape(_named)}."))
    mo.vstack(
        [
            mo.Html(markup.title("Runs", f"{caption} &middot; by Sharpe &middot; click a row to open it")),
            mo.Html(views.stats(best)),
            *([unpinner] if unpinner is not None else []),
            _none if shortlist.empty else runs,
        ]
    )
    return runs, unpinner


@app.cell
def _(best, catalog, key, pins, runs, shortlist, spec):
    mo.stop(shortlist.empty)
    _picked = not runs.value.empty
    _run = str(runs.value["key"].iloc[0]) if _picked else str(shortlist["key"].iloc[0])
    _row = best[best["key"] == _run].iloc[0]
    try:
        _curve = api.sweep_book(key, _run)
        _sleeves = book.members(best, _run)
        _account = api.account(_run) if spec.execution.margin is not None else None
    except (ApiError, book.BookError) as _e:
        mo.stop(True, mo.Html(markup.notice(str(_e))))
    _cash = spec.execution.initial_cash
    _periods = views.venue(catalog, spec.tickers[0]).periods
    _source = "the selected run" if _picked else "the link" if pins else "the top run by Sharpe"
    _named = views.book_parameters(_row, best)
    _parts = [html.escape(_named)] if _named else []
    _parts += [
        f"{len(_sleeves):,} tickers &times; ${_cash:,.0f} each, "
        + ("one shared margin account" if _account is not None else "cash not shared"),
        f"parameters of {_source}",
    ]
    _caption = " &middot; ".join(_parts)
    _liquidated = (
        pd.Timestamp(_account.report.liquidated_ns, unit="ns")
        if _account is not None and _account.report.liquidated_ns is not None
        else None
    )
    mo.vstack(
        [
            mo.Html(
                markup.title(
                    "Book",
                    _caption,
                    markup.key_line("Equity", INK)
                    + markup.key_line("Drawdown from peak", DOWN)
                    + markup.key_line("Drawdown from capital", MUTED),
                )
            ),
            mo.Html(views.book_stats(book.stats(_curve, _periods), _periods)),
            *(
                [mo.Html(views.account_stats(_account, spec.execution.margin))]
                if _account is not None and spec.execution.margin is not None
                else []
            ),
            mo.ui.plotly(views.zoomable(charts.book(_curve, _liquidated)), config=charts.CONFIG),
        ]
    )
    return


@app.cell
def _():
    view = report_app.clone()
    return (view,)


@app.cell
async def _(runs, view):
    mo.stop(runs.value.empty, mo.Html(markup.notice("Select a run above to see it here.")))
    _run = str(runs.value["key"].iloc[0])
    mo.vstack(
        [
            mo.Html(f'<a class="t-link" href="{link("run", run=_run)}">Open in Single line</a>'),
            (await view.embed(defs={"keys": {"A": _run}})).output,
        ]
    )


if __name__ == "__main__":
    app.run()

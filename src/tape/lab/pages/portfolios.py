import marimo

__generated_with = "0.25.2"
app = marimo.App(
    width="full", app_title="tape · Portfolios", css_file="../lab.css", html_head_file="../head.html"
)

with app.setup:
    import html
    import os

    import marimo as mo

    from tape.lab import markup
    from tape.lab import portfolios as books
    from tape.lab.form.model import Form
    from tape.lab.nav import PAGES, sidebar
    from tape.portfolio_index import RECENT
    from tape.ui.client import ApiError, Client

    api = Client(os.environ.get("TAPE_API_URL", "http://127.0.0.1:8000"))


@app.cell
def _():
    mo.Html(markup.STYLE)


@app.cell
def _():
    get_building, set_building = mo.state(False)
    form = Form(page="portfolios", pages=PAGES)
    return form, get_building, set_building


@app.cell
def _(get_building):
    poll = mo.ui.refresh(options=["5s"], default_interval="5s")
    mo.Html(f'<div class="t-hidden">{poll}</div>') if get_building() else None
    return (poll,)


@app.cell
def _(get_building, poll, set_building):
    poll.value
    frame = books.empty()
    try:
        _state = api.portfolio_index_state()
        _index = api.portfolio_index()
    except ApiError as _e:
        _shown = mo.Html(markup.notice(str(_e)))
    else:
        if _state.building != get_building():
            set_building(_state.building)
        if _index.empty:
            _why = "No completed simulations yet"
            if _state.building:
                _why = "Building the portfolio table for the first time"
            _shown = mo.Html(markup.notice(f"{_why}. This page refreshes on its own."))
        else:
            frame = books.prepare(_index)
            _built = _state.built
            _note = " &middot; updating" if _state.building else ""
            _when = f" &middot; built {_built.built_at:%d %b %Y %H:%M} UTC" if _built is not None else ""
            _scope = f"every book of the {RECENT} most recent simulations"
            _shown = mo.Html(markup.title("Portfolios", f"{_scope}{_when}{_note}"))
    _shown
    return (frame,)


@app.cell
def _(form, frame):
    def _pick(label: str, column: str) -> mo.ui.multiselect:
        return mo.ui.multiselect(books.choices(frame, column), label=label, full_width=True)

    markets = _pick("Market", "market")
    strategies = _pick("Strategy", "strategy_title")
    timeframes = _pick("Timeframe", "timeframe")
    stops = _pick("Stop loss", "stop_label")
    outdated = mo.ui.switch(label="Show outdated engine")
    groups = mo.ui.multiselect(
        [g.value for g in books.Group],
        value=[g.value for g in books.DEFAULT_GROUPS],
        label="Columns",
        full_width=True,
    )
    _filters = [markets, strategies, timeframes, stops, groups, outdated]
    sidebar(form, mo.vstack([mo.Html('<div class="t-side-head">Filters</div>'), *_filters], gap=0.75))
    return groups, markets, outdated, stops, strategies, timeframes


@app.cell
def _(frame, groups, markets, outdated, stops, strategies, timeframes):
    mo.stop(frame.empty)
    criteria = books.Criteria(
        markets=markets.value,
        strategies=strategies.value,
        timeframes=timeframes.value,
        stops=stops.value,
        outdated=outdated.value,
        groups=[books.Group(g) for g in groups.value],
    )
    chosen = books.narrow(frame, criteria)
    _shown, _formats = books.shown(chosen, criteria.groups)
    grid = mo.ui.table(
        _shown,
        selection="single",
        page_size=50,
        format_mapping=_formats,
        freeze_columns_left=["#", "Strategy"],
        show_column_summaries=False,
        show_data_types=False,
    )
    _caption = f"{len(chosen):,} of {len(frame):,} books &middot; by Sharpe &middot; click a row to open it"
    mo.vstack([mo.Html(books.summary(chosen)), mo.Html(markup.title("Books", _caption)), grid])
    return chosen, grid


@app.cell
def _(chosen, grid):
    mo.stop(grid.value.empty, mo.Html(markup.notice("Select a book above to open its simulation.")))
    _row = chosen.iloc[int(grid.value["#"].iloc[0]) - 1]
    _parts = [str(_row["strategy_title"]), str(_row["timeframe"]), str(_row["parameters"])]
    _name = " &middot; ".join(html.escape(p) for p in _parts if p)
    _open = f'<a class="t-link" href="{books.open_link(_row)}">Open in Simulation</a>'
    mo.Html(f'{_open} <span class="t-meta">{_name}</span>')


if __name__ == "__main__":
    app.run()

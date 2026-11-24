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
    sidebar(Form(page="portfolios", pages=PAGES))
    return get_building, set_building


@app.cell
def _(get_building):
    poll = mo.ui.refresh(options=["5s"], default_interval="5s")
    mo.Html(f'<div class="t-hidden">{poll}</div>') if get_building() else None
    return (poll,)


@app.cell
def _(get_building, poll, set_building):
    poll.value
    try:
        _state = api.portfolio_index_state()
        _index = api.portfolio_index()
    except ApiError as _e:
        mo.stop(True, mo.Html(markup.notice(str(_e))))
    if _state.building != get_building():
        set_building(_state.building)
    _built = _state.built
    if _index.empty:
        _why = "No completed simulations yet"
        if _state.building:
            _why = "Building the portfolio table for the first time"
        mo.stop(True, mo.Html(markup.notice(f"{_why}. This page refreshes on its own.")))
    frame = books.prepare(_index)
    _note = " &middot; updating" if _state.building else ""
    _when = f" &middot; built {_built.built_at:%d %b %Y %H:%M} UTC" if _built is not None else ""
    _scope = f"every book of the {RECENT} most recent simulations"
    mo.Html(markup.title("Portfolios", f"{_scope}{_when}{_note}"))
    return (frame,)


@app.cell
def _(frame):
    def _pick(label: str, column: str) -> mo.ui.multiselect:
        return mo.ui.multiselect(books.choices(frame, column), label=label)

    markets = _pick("Market", "market")
    strategies = _pick("Strategy", "strategy_title")
    timeframes = _pick("Timeframe", "timeframe")
    stops = _pick("Stop loss", "stop_label")
    fills = _pick("Fills on", "fills_on")
    margins = _pick("Margin", "margin")
    search = mo.ui.text(placeholder="fast 5, window 200 …", label="Parameters")
    outdated = mo.ui.switch(label="Show outdated engine")
    groups = mo.ui.multiselect(
        [g.value for g in books.Group], value=[g.value for g in books.DEFAULT_GROUPS], label="Columns"
    )
    mo.vstack(
        [
            mo.hstack([markets, strategies, timeframes, stops], justify="start", wrap=True),
            mo.hstack([fills, margins, search, groups, outdated], justify="start", wrap=True),
        ]
    )
    return fills, groups, margins, markets, outdated, search, stops, strategies, timeframes


@app.cell
def _(fills, frame, groups, margins, markets, outdated, search, stops, strategies, timeframes):
    criteria = books.Criteria(
        markets=markets.value,
        strategies=strategies.value,
        timeframes=timeframes.value,
        stops=stops.value,
        fills=fills.value,
        margins=margins.value,
        search=search.value,
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

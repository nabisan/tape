import marimo

__generated_with = "0.25.2"
app = marimo.App(width="full", app_title="tape · Run")

with app.setup:
    import os
    from itertools import cycle

    import marimo as mo

    from tape.core.types import Timeframe
    from tape.lab import blotter, markup, report, schema, views
    from tape.lab.layout import split
    from tape.lab.widgets.ledger import Ledger
    from tape.lab.widgets.price import PriceChart
    from tape.service.results import Table
    from tape.ui import charts
    from tape.ui.client import Client
    from tape.ui.theme import DOWN, INDICATORS, INK, MUTED, SERIES

    api = Client(os.environ.get("TAPE_API_URL", "http://127.0.0.1:8000"))
    BUY, SELL = "&#9650;", "&#9661;"


@app.cell
def _():
    keys: dict[str, str] = {}
    return (keys,)


@app.cell
def _():
    catalog = api.catalog()
    strategies = api.strategies()
    return catalog, strategies


@app.cell
def _(catalog, keys, strategies):
    mo.stop(not keys)
    details = {leg: api.detail(k) for leg, k in keys.items()}
    results = {leg: (d.key, d.metrics) for leg, d in details.items()}
    specs = {leg: d.spec for leg, d in details.items()}
    names = {leg: f"{strategies[s.strategy].title} · {s.timeframe}" for leg, s in specs.items()}
    curves = {leg: api.table(k, Table.EQUITY) for leg, k in keys.items()}
    markets = {leg: api.comparison(k) for leg, k in keys.items()}
    exits = frozenset(e for s in specs.values() for e in strategies[s.strategy].exits)
    charged = frozenset(c for s in specs.values() for c in s.execution.fill.costs)
    worth = api.valuation(next(iter(keys.values())))
    _venue = views.venue(catalog, next(iter(specs.values())).ticker)
    if len(results) == 2:
        _keys = "".join(markup.key_line(lb, c, d) for lb, (c, d) in zip(curves, SERIES, strict=False))
        _keys += markup.key_line("Drawdown from peak A", DOWN) + markup.key_line(
            "Drawdown from capital A", MUTED
        )
    else:
        _keys = markup.key_line("Equity", INK) + markup.key_line("Drawdown from peak", DOWN)
        _keys += markup.key_line("Drawdown from capital", MUTED)
    mo.vstack(
        [
            mo.Html(report.header(specs, results, names, catalog.data_version, markets, venue=_venue)),
            split(
                mo.vstack(
                    [
                        mo.Html(markup.title("Equity", "session closes", _keys)),
                        mo.ui.plotly(
                            views.zoomable(
                                charts.equity(curves, {lg: m.initial_cash for lg, (_, m) in results.items()})
                            ),
                            config=charts.CONFIG,
                        ),
                    ]
                ),
                mo.Html(report.performance(results, worth, exits, charged, _venue)),
            ),
        ],
        gap=1.5,
    )
    return curves, names, results, specs


@app.cell
def _(names, results):
    leg = mo.ui.radio(
        {f"{lb}  {names[lb]}": lb for lb in results},
        value=f"{next(iter(results))}  {names[next(iter(results))]}",
        inline=True,
    )
    mo.vstack([mo.Html(markup.title("Strategy")), leg]) if len(results) == 2 else None
    return (leg,)


@app.cell
def _(leg, results):
    key = results[leg.value][0]
    return (key,)


@app.cell
def _(catalog, key, leg, specs):
    _spec = specs[leg.value]
    _names = sorted(api.price(key, 1).indicators)
    _keys = "".join(
        markup.key_line(schema.label(n), c, d) for n, (c, d) in zip(_names, cycle(INDICATORS), strict=False)
    )
    _keys += markup.key_marker("Buy", BUY) + markup.key_marker("Sell", SELL)
    _zone = views.venue(catalog, _spec.ticker).zone.split("/")[-1].replace("_", " ")
    _caption = f"{_spec.ticker} &middot; {_spec.timeframe} &middot; {_zone}"
    mo.vstack(
        [
            mo.Html(markup.title("Price", _caption, _keys)),
            PriceChart.of(key, _spec.timeframe is not Timeframe.D1, {n: schema.label(n) for n in _names}),
        ]
    )


@app.cell
def _(catalog, key, leg, specs):
    _costs = specs[leg.value].execution.fill.costs
    _digits = views.listed(catalog, specs[leg.value].ticker).price_precision
    _trade_columns = blotter.priced(blotter.TRADES, _digits)
    _order_columns = blotter.priced(blotter.ORDERS, _digits)
    _trades = api.table(key, Table.TRADES)
    _shown = blotter.sheet(_trades, _trade_columns, _costs)

    def _orders() -> object:
        frame = blotter.sheet(api.table(key, Table.ORDERS), _order_columns, _costs)
        return mo.ui.table(
            frame,
            selection=None,
            page_size=20,
            format_mapping=views.formats(frame, _order_columns),
            show_column_summaries=False,
            show_data_types=False,
        )

    _tabs = mo.ui.tabs(
        {
            f"Trades {len(_trades):,}": mo.ui.table(
                _shown,
                selection=None,
                page_size=20,
                format_mapping=views.formats(_shown, _trade_columns),
                show_column_summaries=False,
                show_data_types=False,
            ),
            "Orders": mo.lazy(_orders, show_loading_indicator=True),
        }
    )
    mo.vstack([mo.Html(markup.title("Blotter")), _tabs, Ledger(key=key)])


@app.cell
def _(curves, leg):
    _caption = "% &middot; from session-close equity, the first month measured from starting capital"
    mo.Html(markup.title("Monthly returns", _caption) + report.monthly(curves[leg.value]))


if __name__ == "__main__":
    app.run()

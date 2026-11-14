import marimo

__generated_with = "0.25.2"
app = marimo.App(
    width="full", app_title="tape · Single line", css_file="../lab.css", html_head_file="../head.html"
)

with app.setup:
    import os
    from concurrent.futures import ThreadPoolExecutor

    import marimo as mo
    from pydantic import ValidationError

    from tape.lab import markup
    from tape.lab.form.model import Form, RunRequest, seed
    from tape.lab.nav import PAGES, sidebar
    from tape.lab.pages.report import app as report_app
    from tape.ui.client import ApiError, Client

    api = Client(os.environ.get("TAPE_API_URL", "http://127.0.0.1:8000"))
    LEGS = ("A", "B")
    LINKS = {"A": "run", "B": "b"}


@app.cell
def _():
    mo.Html(markup.STYLE)


@app.cell
def _():
    params = mo.query_params()
    catalog = api.catalog()
    strategies = api.strategies()
    return catalog, params, strategies


@app.cell
def _(params):
    _linked = {leg: str(params[link]) for leg, link in LINKS.items() if params[link]}
    try:
        _specs = {leg: api.detail(key).spec for leg, key in _linked.items()}
    except ApiError:
        _specs = {}
    form = Form(page="run", pages=PAGES, seed=seed(_specs))
    get_submission, set_submission = mo.state(form.submission)
    form.observe(lambda _: set_submission(form.submission), names=["submission"])
    get_keys, set_keys = mo.state(_linked)
    sidebar(form)
    return form, get_keys, get_submission, set_keys


@app.cell
def _(form, get_submission, params, set_keys):
    _sent = get_submission()
    mo.stop(not _sent)
    form.message = ""
    try:
        _specs = RunRequest.model_validate(_sent).specs()
    except ValidationError as _e:
        form.message = "; ".join(str(err["msg"]) for err in _e.errors())
        mo.stop(True)
    form.busy = True
    try:
        with ThreadPoolExecutor(len(_specs)) as _pool:
            _futures = {_leg: _pool.submit(api.run, _s) for _leg, _s in _specs.items()}
            _done = {_leg: _f.result().key for _leg, _f in _futures.items()}
    except ApiError as _e:
        form.message = str(_e)
        mo.stop(True)
    finally:
        form.busy = False
    for _leg, _link in LINKS.items():
        if _leg in _done:
            params.set(_link, _done[_leg])
        elif _link in params:
            params.remove(_link)
    set_keys(_done)
    return


@app.cell
def _():
    view = report_app.clone()
    return (view,)


@app.cell
async def _(get_keys, view):
    _keys = get_keys()
    mo.stop(
        not _keys,
        mo.Html(
            markup.notice(
                "Choose a ticker, a date range and a strategy in the sidebar, then run the single line."
            )
        ),
    )
    (await view.embed(defs={"keys": _keys})).output
    return


if __name__ == "__main__":
    app.run()

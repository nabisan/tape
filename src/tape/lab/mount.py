from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final

import marimo
import plotly
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from plotly.offline import get_plotlyjs_version

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

PAGES: Final = Path(__file__).with_name("pages")
ROUTES: Final = {"run": "", "simulation": "/simulation"}
SWEEP: Final = b"sweep="
PLOTLY_JS: Final = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
PLOTLY: Final = f"/static/plotly-{get_plotlyjs_version()}.min.js"
FOREVER: Final = {"Cache-Control": "public, max-age=31536000, immutable"}


def url(page: str) -> str:
    return f"{ROUTES[page]}/"


def pages() -> ASGIApp:
    server = marimo.create_asgi_app(quiet=True)
    for name, path in ROUTES.items():
        server = server.with_app(path=path, root=str(PAGES / f"{name}.py"))
    app: ASGIApp = server.build()
    return app


def slash(request: Request) -> RedirectResponse:
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"{request.url.path}/{query}")


class SweepLinks:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        query: bytes = scope.get("query_string", b"")
        if scope["type"] == "http" and scope["path"] == "/" and SWEEP in query:
            await RedirectResponse(f"{url('simulation')}?{query.decode()}")(scope, receive, send)
            return
        await self.app(scope, receive, send)


def plotly_js() -> FileResponse:
    return FileResponse(PLOTLY_JS, media_type="text/javascript", headers=FOREVER)


def mount(app: FastAPI) -> None:
    app.add_api_route(PLOTLY, plotly_js, methods=["GET"], include_in_schema=False)
    for path in ROUTES.values():
        if path:
            app.add_api_route(path, slash, methods=["GET"], include_in_schema=False)
    app.add_middleware(SweepLinks)
    app.mount("/", pages())

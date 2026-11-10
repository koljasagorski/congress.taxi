from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app.api import auth, health, match, me, requests, rides
from app.api import config as config_api
from app.config import get_settings
from app.db import dispose_engine
from app.errors import install_error_handlers
from app.middleware.security import install_security
from app.routing import close_routing_client

logger = logging.getLogger("app.request")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    get_settings()  # Konfigurationsfehler (z. B. Platzhalter-Secrets) sofort beim Start melden
    yield
    await close_routing_client()
    await dispose_engine()


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = FastAPI(
        title="Mitfahrer-Matching 40C3",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )
    install_error_handlers(app)
    install_security(app)

    @app.middleware("http")
    async def request_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Datenschutz: kein Query-String (enthält Koordinaten), keine IP, kein Pfad mit IDs –
        # nur das Routen-Template.
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        template = getattr(route, "path", "-")
        logger.info(
            "%s %s -> %d (%.0f ms)",
            request.method,
            template,
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        return response

    app.include_router(health.router)
    app.include_router(config_api.router)
    app.include_router(auth.router)
    app.include_router(me.router)
    app.include_router(rides.router)
    app.include_router(match.router)
    app.include_router(requests.router)
    return app


app = create_app()

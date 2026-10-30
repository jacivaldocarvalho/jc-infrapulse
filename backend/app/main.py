import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routes import health, info, monitoring, services
from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.session import create_database_engine
from app.services.service_registry import InvalidServiceError, ServiceNotFoundError


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        config = settings if settings is not None else Settings()
        configure_logging(config.log_level)
        engine = create_database_engine(config)
        app.state.settings = config
        app.state.engine = engine
        logging.getLogger(__name__).info("application_started")
        try:
            yield
        finally:
            engine.dispose()
            logging.getLogger(__name__).info("application_stopped")

    application = FastAPI(
        title="InfraPulse API", version=version("infrapulse-api"), lifespan=lifespan
    )
    application.include_router(health.router)
    application.include_router(info.router)
    application.include_router(services.router)
    application.include_router(monitoring.router)

    @application.exception_handler(ServiceNotFoundError)
    async def service_not_found(request: Request, exc: ServiceNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": "Service not found"})

    @application.exception_handler(InvalidServiceError)
    async def invalid_service(request: Request, exc: InvalidServiceError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    return application


app = create_app()

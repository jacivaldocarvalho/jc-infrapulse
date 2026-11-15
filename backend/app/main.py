import asyncio
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
from app.observability.http import ObservabilityMiddleware
from app.observability.metrics import Metrics, MetricsServer
from app.observability.tracing import Telemetry
from app.services.service_registry import InvalidServiceError, ServiceNotFoundError


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        config = settings if settings is not None else Settings()
        configure_logging(config.log_level)
        engine = create_database_engine(config)
        app.state.settings = config
        app.state.engine = engine
        metrics = Metrics()
        telemetry = Telemetry(config, "api")
        app.state.metrics, app.state.telemetry = metrics, telemetry
        listener = MetricsServer(metrics, config.metrics_host, config.api_metrics_port)
        pulse = asyncio.create_task(metrics.pulse())
        logging.getLogger(__name__).info("application_started")
        try:
            if config.metrics_enabled:
                await listener.start()
            yield
        finally:
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)
            await listener.close()
            await telemetry.close()
            await asyncio.to_thread(engine.dispose)
            logging.getLogger(__name__).info("application_stopped")

    application = FastAPI(
        title="InfraPulse API", version=version("infrapulse-api"), lifespan=lifespan
    )
    application.add_middleware(ObservabilityMiddleware)
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

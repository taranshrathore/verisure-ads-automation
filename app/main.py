"""FastAPI application entrypoint for VeriSure ad automation."""

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, campaigns, health, metrics, provider_connections
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.core.settings import settings
from app.core.startup import validate_startup_config
from app.middleware.request_context import RequestContextMiddleware


def create_application() -> FastAPI:
    """Validate configuration once, then build the FastAPI application."""
    validate_startup_config()
    configure_logging()

    application = FastAPI(
        title=settings.app_name,
        description="Multi-platform advertisement automation backend for VeriSure.",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    register_exception_handlers(application)

    # TODO: Restrict these CORS settings before deploying to production.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Outermost: request_id + access log wrap the full stack (including CORS).
    application.add_middleware(RequestContextMiddleware)

    api_v1_router = APIRouter(prefix="/api/v1")
    api_v1_router.include_router(auth.router)
    api_v1_router.include_router(campaigns.router)
    api_v1_router.include_router(health.router)
    api_v1_router.include_router(metrics.router)
    api_v1_router.include_router(provider_connections.router)

    application.include_router(api_v1_router)

    @application.get("/")
    def root() -> dict[str, str]:
        """Return a basic service identification payload."""
        return {"message": f"{settings.app_name} is running.", "status": "ok"}

    @application.get("/health")
    def health_legacy() -> dict[str, str]:
        """Return a lightweight liveness signal."""
        return {"status": "healthy"}

    return application


app = create_application()

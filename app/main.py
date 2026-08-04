"""FastAPI application entrypoint for VeriSure ad automation."""

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, campaigns, health, metrics, provider_connections
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.core.startup import validate_startup_config
from app.middleware.request_context import RequestContextMiddleware


def create_application() -> FastAPI:
    """Validate configuration once, then build the FastAPI application."""
    cfg = validate_startup_config()
    configure_logging()

    docs_enabled = cfg.resolve_docs_enabled()
    application = FastAPI(
        title=cfg.app_name,
        description="Multi-platform advertisement automation backend for VeriSure.",
        version="0.1.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    register_exception_handlers(application)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origin_list(),
        allow_credentials=cfg.cors_allow_credentials,
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
        return {"message": f"{cfg.app_name} is running.", "status": "ok"}

    @application.get("/health")
    def health_legacy() -> dict[str, str]:
        """Return a lightweight liveness signal."""
        return {"status": "healthy"}

    return application


app = create_application()

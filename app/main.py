"""FastAPI application entrypoint for VeriSure ad automation."""

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, campaigns, roles
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.core.settings import settings

configure_logging()

app = FastAPI(
    title=settings.app_name,
    description="Multi-platform advertisement automation backend for VeriSure.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)
register_exception_handlers(app)

# TODO: Restrict these CORS settings before deploying to production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(auth.router)
api_v1_router.include_router(campaigns.router)
api_v1_router.include_router(roles.router)

app.include_router(api_v1_router)


@app.get("/")
def root() -> dict[str, str]:
    """Return a basic service identification payload."""
    return {"message": f"{settings.app_name} is running.", "status": "ok"}


@app.get("/health")
def health() -> dict[str, str]:
    """Return a lightweight liveness signal."""
    return {"status": "healthy"}

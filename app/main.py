"""FastAPI application entrypoint for VeriSure ad automation."""

from fastapi import FastAPI

from app.core.settings import settings

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": "0.1.0",
    }

"""FastAPI application entrypoint for VeriSure ad automation."""

from fastapi import FastAPI

app = FastAPI(
    title="VeriSure Ad Automation API",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": "verisure-ad-automation",
        "version": "0.1.0",
    }

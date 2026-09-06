"""Health endpoint tests."""

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_health_returns_service_metadata(test_settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(test_settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "LearnLoop API",
        "version": "0.1.0",
        "environment": "test",
    }


@pytest.mark.asyncio
async def test_unknown_route_returns_not_found(test_settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(test_settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "http_error",
            "message": "Not Found",
            "details": {},
        }
    }

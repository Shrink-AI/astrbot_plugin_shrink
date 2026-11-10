import json

import httpx
import pytest

from shrink.api.app import create_app
from shrink.application import Application


@pytest.mark.asyncio
async def test_public_https_origin_preserves_auth_and_csrf(tmp_path):
    async def unused(*args, **kwargs):
        return "{}"

    app = Application(tmp_path / "data", unused, unused, unused)
    (app.state.root / "dashboard.json").write_text(
        json.dumps({"public_origin": "https://dashboard.example.com"}), encoding="utf-8")
    web = create_app(app, tmp_path, lambda: {})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=web), base_url="https://dashboard.example.com",
        headers={"origin": "https://dashboard.example.com"},
    ) as client:
        assert (await client.get("/api/v1/health")).status_code == 200
        assert (await client.get("/api/v1/session")).status_code == 401
        secret = (app.state.root / "initial-password.txt").read_text().strip()
        credentials = {"password": secret}
        assert (await client.post("/api/v1/login", json=credentials,
                                 headers={"origin": "https://evil.example"})).status_code == 403
        assert (await client.post("/api/v1/login", json=credentials,
                                 headers={"origin": "http://dashboard.example.com"})).status_code == 403
        response = await client.post("/api/v1/login", json=credentials)
        assert response.status_code == 200
        assert "Secure" in response.headers["set-cookie"]
        assert (await client.get("/api/v1/session")).status_code == 200
        assert (await client.post("/api/v1/logout")).status_code == 403
        assert (await client.post("/api/v1/logout",
                                 headers={"x-csrf-token": response.json()["csrf"]})).status_code == 200
        assert (await client.get("/api/v1/health", headers={"host": "evil.example"})).status_code == 400
    app.database.close()

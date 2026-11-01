"""Small Home Assistant REST client (uses the Supervisor proxy inside an add-on)."""
from __future__ import annotations

import os

import aiohttp


class HAError(Exception):
    pass


class HA:
    def __init__(self) -> None:
        token = os.environ.get("SUPERVISOR_TOKEN")
        if token:
            self.base = "http://supervisor/core/api"
        else:  # local development
            self.base = os.environ.get("HA_URL", "http://localhost:8123").rstrip("/") + "/api"
            token = os.environ.get("HA_TOKEN", "")
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self.session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))

    async def close(self) -> None:
        if self.session:
            await self.session.close()

    async def rest(self, method: str, path: str, json=None):
        assert self.session
        try:
            async with self.session.request(method, self.base + path, headers=self.headers, json=json) as r:
                text = await r.text()
                if r.status >= 400:
                    raise HAError(f"{method} {path}: HTTP {r.status} {text[:300]}")
                ctype = r.headers.get("Content-Type", "")
                if "json" in ctype:
                    return await r.json(content_type=None)
                return text
        except aiohttp.ClientError as err:
            raise HAError(f"{method} {path}: {err}") from err

    async def states(self) -> dict[str, dict]:
        data = await self.rest("GET", "/states")
        return {s["entity_id"]: s for s in data}

    async def call(self, domain: str, service: str, data: dict) -> None:
        await self.rest("POST", f"/services/{domain}/{service}", data)

    async def config(self) -> dict:
        return await self.rest("GET", "/config")

    async def services(self) -> set[str]:
        data = await self.rest("GET", "/services")
        return {d["domain"] for d in data}

    async def check_config(self) -> dict:
        return await self.rest("POST", "/config/core/check_config")

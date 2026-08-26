"""Home Assistant state adapter.

Polls Home Assistant Core through the Supervisor proxy and keeps one immutable
`HomeState` snapshot that scenes read. The poll loop is a separate task from
the render loop and the snapshot is swapped atomically, so a slow or failing
Home Assistant can never stall or break rendering — it only makes the snapshot
stale (`HomeState.available` goes False and scenes fall back to neutral values).

Auth: Supervisor injects `SUPERVISOR_TOKEN` into every add-on container, and
proxies Core at `http://supervisor/core/api/...`. No token or user URL is ever
hardcoded; if the token is missing (i.e. we are running standalone), the
adapter stays in an "unavailable" state instead of erroring.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Iterable, Mapping

import aiohttp

from .options import Options
from .scene_api import EntityState, HomeState

_LOGGER = logging.getLogger(__name__)

#: Supervisor's proxy to Home Assistant Core's REST API.
SUPERVISOR_CORE_API = "http://supervisor/core/api"

_MAX_FAILURE_BACKOFF_S = 60.0


class HaStateAdapter:
    """Maintains a cached `HomeState`, refreshed on its own schedule."""

    def __init__(
        self,
        options: Options,
        *,
        base_url: str | None = None,
        token: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._options = options
        self._base_url = (base_url or os.environ.get("MATRIX_STUDIO_CORE_API") or SUPERVISOR_CORE_API).rstrip("/")
        self._token = token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
        self._session = session
        self._owns_session = session is None
        self._snapshot = HomeState(roles=dict(options.entity_roles))
        self._task: asyncio.Task[None] | None = None
        self._consecutive_failures = 0
        self._last_error: str | None = None
        self._poll_count = 0
        self._entity_count = 0

    # ------------------------------------------------------------------ state

    def snapshot(self) -> HomeState:
        """The current snapshot. Cheap, never blocks, never raises."""
        return self._snapshot

    @property
    def configured(self) -> bool:
        """True when we have a token to talk to Core with."""
        return bool(self._token)

    def status(self) -> dict[str, Any]:
        snapshot = self._snapshot
        return {
            "configured": self.configured,
            "available": snapshot.available,
            "updated_at": snapshot.updated_at,
            "age_seconds": (time.time() - snapshot.updated_at) if snapshot.updated_at else None,
            "poll_count": self._poll_count,
            "entity_count": self._entity_count,
            "tracked_entities": len(snapshot.entities),
            "lights_on": snapshot.lights_on,
            "lights_total": snapshot.lights_total,
            "last_error": self._last_error,
        }

    # --------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        if self._task is not None:
            return
        if self._owns_session:
            self._session = aiohttp.ClientSession()
        if not self.configured:
            _LOGGER.warning(
                "SUPERVISOR_TOKEN is not set — running without Home Assistant state. "
                "Scenes will receive an empty, unavailable HomeState."
            )
        self._task = asyncio.create_task(self._run(), name="matrix-studio-ha-state")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def _run(self) -> None:
        while True:
            delay = float(self._options.state_poll_interval)
            if self.configured:
                ok = await self.poll_once()
                if not ok:
                    delay = min(delay * (2 ** min(self._consecutive_failures, 5)), _MAX_FAILURE_BACKOFF_S)
            else:
                delay = max(delay, 30.0)
            await asyncio.sleep(delay)

    # ------------------------------------------------------------------ polling

    async def poll_once(self) -> bool:
        """Fetch all states once. Returns True on success; never raises."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        timeout = aiohttp.ClientTimeout(total=max(5.0, min(float(self._options.state_poll_interval), 15.0)))
        url = f"{self._base_url}/states"
        try:
            async with self._session.get(
                url,
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
                timeout=timeout,
            ) as response:
                if response.status != 200:
                    return self._record_failure(f"HTTP {response.status} from {url}")
                payload = await response.json(content_type=None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a state fetch must never kill the add-on
            return self._record_failure(f"{type(exc).__name__}: {exc}")

        if not isinstance(payload, list):
            return self._record_failure(f"unexpected /states payload type {type(payload).__name__}")

        self._snapshot = self._build_snapshot(payload)
        self._entity_count = len(payload)
        self._poll_count += 1
        if self._consecutive_failures:
            _LOGGER.info("Home Assistant state fetch recovered after %d failure(s)", self._consecutive_failures)
        self._consecutive_failures = 0
        self._last_error = None
        return True

    def _record_failure(self, message: str) -> bool:
        self._consecutive_failures += 1
        self._last_error = message
        # Keep the previous entities (stale but useful) and flag unavailability.
        previous = self._snapshot
        self._snapshot = HomeState(
            entities=previous.entities,
            roles=previous.roles,
            light_entity_ids=previous.light_entity_ids,
            available=False,
            updated_at=previous.updated_at,
        )
        log = _LOGGER.warning if self._consecutive_failures <= 3 else _LOGGER.debug
        log("Home Assistant state fetch failed (%d in a row): %s", self._consecutive_failures, message)
        return False

    def _build_snapshot(self, payload: Iterable[Mapping[str, Any]]) -> HomeState:
        roles = dict(self._options.entity_roles)
        wanted = set(roles.values()) | set(self._options.light_entities)
        auto_lights = self._options.auto_discover_lights and not self._options.light_entities

        entities: dict[str, EntityState] = {}
        discovered_lights: list[str] = []
        for item in payload:
            entity_id = item.get("entity_id")
            if not isinstance(entity_id, str):
                continue
            is_light = entity_id.startswith("light.")
            if is_light and auto_lights:
                discovered_lights.append(entity_id)
            elif entity_id not in wanted:
                continue
            attributes = item.get("attributes")
            entities[entity_id] = EntityState(
                entity_id=entity_id,
                state=str(item.get("state", "")),
                attributes=attributes if isinstance(attributes, Mapping) else {},
            )

        light_ids = (
            tuple(sorted(discovered_lights)) if auto_lights else tuple(self._options.light_entities)
        )
        missing = [eid for eid in wanted if eid not in entities]
        if missing:
            _LOGGER.debug("configured entities not present in Home Assistant: %s", ", ".join(sorted(missing)))

        return HomeState(
            entities=entities,
            roles=roles,
            light_entity_ids=light_ids,
            available=True,
            updated_at=time.time(),
        )

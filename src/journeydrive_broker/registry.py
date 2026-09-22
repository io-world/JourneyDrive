from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field

from websockets.asyncio.server import ServerConnection

from journeydrive_broker.config import Settings

logger = logging.getLogger(__name__)


class MachineNotConnected(Exception):
    pass


class MachineError(Exception):
    """The thin client reported an error executing the command."""


class MachineTimeout(Exception):
    pass


@dataclass
class _Connection:
    websocket: ServerConnection
    monitors: list[dict] = field(default_factory=list)
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    pending_methods: dict[str, str] = field(default_factory=dict)
    # (request_id, metadata) once a screenshot's JSON response has arrived but its
    # binary follow-up frame hasn't yet — see handle_binary_frame.
    awaiting_binary: tuple[str, dict] | None = None


class ConnectionRegistry:
    """Tracks connected thin clients and routes broker->machine requests over their
    websocket, correlating responses back to the HTTP call that's waiting on them.

    Each connection is processed sequentially by both sides (see ws_client.py and
    ws_server.py) — that's what makes a screenshot's binary follow-up frame
    unambiguous without needing to embed a request id in it.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._connections: dict[str, _Connection] = {}

    def register(self, machine_id: str, websocket: ServerConnection, monitors: list[dict] | None = None) -> None:
        self._connections[machine_id] = _Connection(websocket=websocket, monitors=monitors or [])
        logger.info("machine %s connected", machine_id)

    def _current(self, machine_id: str, websocket: ServerConnection) -> _Connection | None:
        """Look up machine_id's connection, but only if `websocket` is still the one
        registered — a stale connection (e.g. a slow-to-notice disconnect that races a
        fast reconnect) must not be allowed to act on a newer connection's state."""
        conn = self._connections.get(machine_id)
        if conn is None or conn.websocket is not websocket:
            return None
        return conn

    def unregister(self, machine_id: str, websocket: ServerConnection) -> None:
        conn = self._current(machine_id, websocket)
        if conn is None:
            return
        del self._connections[machine_id]
        for future in conn.pending.values():
            if not future.done():
                future.set_exception(MachineNotConnected(f"{machine_id} disconnected mid-request"))
        logger.info("machine %s disconnected", machine_id)

    def connected_machines(self) -> list[dict]:
        return [{"machine_id": mid, "monitors": conn.monitors} for mid, conn in self._connections.items()]

    def get_monitors(self, machine_id: str) -> list[dict] | None:
        """Cached monitor layout for one machine, or None if it isn't connected —
        used by GET /machines/{id}/screenshot/monitors to serve without a live call."""
        conn = self._connections.get(machine_id)
        return conn.monitors if conn is not None else None

    async def call(self, machine_id: str, method: str, params: dict) -> tuple[dict, bytes | None]:
        """Send a command to machine_id and wait for its response.

        Returns (result, image_bytes) — image_bytes is only non-None for method
        "screenshot"; every other method returns (result, None).
        """
        conn = self._connections.get(machine_id)
        if conn is None:
            raise MachineNotConnected(f"machine {machine_id!r} is not connected")

        request_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        conn.pending[request_id] = future
        conn.pending_methods[request_id] = method
        try:
            await conn.websocket.send(json.dumps({"id": request_id, "method": method, "params": params}))
            try:
                return await asyncio.wait_for(future, timeout=self._settings.request_timeout)
            except asyncio.TimeoutError:
                raise MachineTimeout(
                    f"machine {machine_id!r} did not respond within {self._settings.request_timeout}s"
                ) from None
        finally:
            conn.pending.pop(request_id, None)
            conn.pending_methods.pop(request_id, None)

    def handle_text_frame(self, machine_id: str, websocket: ServerConnection, message: dict) -> None:
        """Called by ws_server's reader loop for each JSON frame received."""
        conn = self._current(machine_id, websocket)
        if conn is None:
            return
        request_id = message.get("id")
        future = conn.pending.get(request_id)
        if future is None or future.done():
            return

        if "error" in message:
            future.set_exception(MachineError(message["error"].get("message", "unknown error")))
            return

        result = message.get("result")
        if conn.pending_methods.get(request_id) == "screenshot":
            # Metadata only so far — hold the future open until the binary frame
            # (the actual image bytes) arrives next.
            conn.awaiting_binary = (request_id, result)
            return
        future.set_result((result, None))

    def handle_binary_frame(self, machine_id: str, websocket: ServerConnection, data: bytes) -> None:
        """Called by ws_server's reader loop for each binary frame received."""
        conn = self._current(machine_id, websocket)
        if conn is None or conn.awaiting_binary is None:
            logger.warning("unexpected binary frame from %s (no screenshot awaiting one)", machine_id)
            return
        request_id, metadata = conn.awaiting_binary
        conn.awaiting_binary = None
        future = conn.pending.get(request_id)
        if future is not None and not future.done():
            future.set_result((metadata, data))

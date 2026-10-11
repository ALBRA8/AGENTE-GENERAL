"""APIServer — wraps uvicorn to serve a FastAPI app.

uvicorn is LAZY-imported. `APIServer.start()` will raise a clean
ImportError if uvicorn is not installed.

Usage:
    server = APIServer(runtime)
    server.start(host="0.0.0.0", port=8000)   # blocks
    # or, for tests:
    await server.serve_async(host, port)

The server keeps a reference to the FastAPI app so tests can drive the
ASGI app directly (e.g. via httpx.ASGITransport).
"""

from __future__ import annotations

import asyncio
from typing import Any

from albra_seed.api.app import create_app
from albra_seed.runtime.runtime import Runtime


def _require_uvicorn() -> Any:
    try:
        import uvicorn  # type: ignore[import-untyped]
        return uvicorn
    except ImportError as exc:  # pragma: no cover - optional dep
        raise ImportError(
            "uvicorn is required for APIServer.start(); install with "
            "`pip install 'albra-seed[api]'` or `pip install uvicorn`."
        ) from exc


class APIServer:
    """Run a FastAPI app under uvicorn."""

    def __init__(
        self,
        runtime: Runtime,
        *,
        host: str | None = None,
        port: int | None = None,
        app: Any | None = None,
    ) -> None:
        self.runtime = runtime
        self.host = host or runtime.config.api_host
        self.port = port or runtime.config.api_port
        self._app = app
        self._server: Any | None = None
        self._serve_task: asyncio.Task | None = None

    @property
    def app(self) -> Any:
        if self._app is None:
            self._app = create_app(self.runtime)
        return self._app

    # ------------------------------------------------------------------ #
    # Synchronous serve (blocks the calling thread)                       #
    # ------------------------------------------------------------------ #

    def start(self, host: str | None = None, port: int | None = None) -> None:
        """Run uvicorn synchronously. Blocks until interrupted."""
        uvicorn = _require_uvicorn()
        h = host or self.host
        p = port or self.port
        config = uvicorn.Config(
            self.app, host=h, port=p, log_level="info"
        )
        server = uvicorn.Server(config)
        self._server = server
        server.run()

    def stop(self) -> None:
        """Best-effort stop. uvicorn.Server has its own signal handling;
        calling this directly triggers `should_exit = True`."""
        if self._server is not None and hasattr(self._server, "should_exit"):
            self._server.should_exit = True
        if self._serve_task is not None and not self._serve_task.done():
            self._serve_task.cancel()

    # ------------------------------------------------------------------ #
    # Async serve (for tests)                                             #
    # ------------------------------------------------------------------ #

    async def serve_async(
        self, host: str | None = None, port: int | None = None
    ) -> None:
        """Run uvicorn as an asyncio task. Returns immediately. Call
        `stop()` to terminate."""
        uvicorn = _require_uvicorn()
        h = host or self.host
        p = port or self.port
        config = uvicorn.Config(self.app, host=h, port=p, log_level="warning")
        server = uvicorn.Server(config)
        self._server = server
        self._serve_task = asyncio.create_task(server.serve())
        return None

    async def serve_and_wait(
        self,
        host: str | None = None,
        port: int | None = None,
        ready_event: asyncio.Event | None = None,
    ) -> None:
        """Run uvicorn to completion (async)."""
        uvicorn = _require_uvicorn()
        h = host or self.host
        p = port or self.port
        config = uvicorn.Config(self.app, host=h, port=p, log_level="warning")
        server = uvicorn.Server(config)
        self._server = server
        if ready_event is not None:
            ready_event.set()
        await server.serve()


__all__ = ["APIServer"]

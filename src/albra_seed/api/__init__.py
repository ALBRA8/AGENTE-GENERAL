"""API subpackage — REST API (FastAPI lazy-imported).

The API exposes TWO clearly-separated router groups:

    PUBLIC API  — under `/api/v1/` prefix
        GET  /api/v1/health
        GET  /api/v1/readiness
        GET  /api/v1/agent/info
        GET  /api/v1/agent/capabilities
        POST /api/v1/execution
        GET  /api/v1/execution/{execution_id}

    INTERNAL API — under `/internal/` prefix
        GET  /internal/diagnostics
        (audit, security introspection endpoints belong here too)

CRITICAL RULES:
    * fastapi / uvicorn are LAZY-imported so CORE_ONLY mode (no extra
      deps) keeps importing `albra_seed.api` cheap.
    * Secrets are NEVER returned by any endpoint — every response runs
      through the secrets redactor.
"""

from albra_seed.api.app import create_app


def __getattr__(name: str):  # pragma: no cover - re-export glue
    if name == "APIServer":
        from albra_seed.api.server import APIServer
        return APIServer
    raise AttributeError(name)


__all__ = ["create_app", "APIServer"]

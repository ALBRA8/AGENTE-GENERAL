# ─────────────────────────────────────────────────────────────────────
# ALBRA SPECIALIZATION · youtube/client.py
# ─────────────────────────────────────────────────────────────────────
# Cliente HTTP del YouTube Harness (POST /api/harness/v1/execute).
#
# Patrón canónico del seed (a2a/transport.py + audio/provider.py):
# · httpx LAZY (CORE_ONLY preservado si no está instalado)
# · credenciales por entorno: YOUTUBE_HARNESS_URL / YOUTUBE_HARNESS_API_KEY
#   (la API key NUNCA se loguea; SecretsManager la auto-redacta)
# · errores tipados; transporte inyectable (httpx.MockTransport en tests)
#
# La skill/tool es COORDINACIÓN: cero lógica del motor de YouTube aquí.
# El arnés es quien conoce la fábrica (Harness Contract V1).
# ─────────────────────────────────────────────────────────────────────
from __future__ import annotations

import os
import re
import uuid
from typing import Any

CONTRACT_VERSION = "1.0"
CAPABILITY_ID = "youtube_automation"
_DEFAULT_TIMEOUT = 120.0

_EXEC_SAFE = re.compile(r"[^A-Za-z0-9._:\-]")


class HarnessConfigError(RuntimeError):
    """Falta configuración del arnés (URL/clave) o httpx no instalado."""


class HarnessTransportError(RuntimeError):
    """El arnés no respondió (red, timeout, 5xx)."""


class HarnessProtocolError(RuntimeError):
    """El arnés respondió algo fuera del contrato (4xx estructurado)."""


def _gen_execution_id(prefix: str = "exec") -> str:
    raw = f"{prefix}-{uuid.uuid4().hex[:16]}"
    return _EXEC_SAFE.sub("-", raw)[:128]


class YoutubeHarnessClient:
    """Cliente m2m del arnés. Credenciales por entorno; transporte
    inyectable para tests (httpx.MockTransport)."""

    def __init__(self, *, base_url: str | None = None, api_key: str | None = None,
                 timeout: float = _DEFAULT_TIMEOUT,
                 transport: Any = None, requester: str = "agente-general"):
        self.base_url = (base_url or os.environ.get("YOUTUBE_HARNESS_URL")
                         or "").rstrip("/")
        self.api_key = api_key or os.environ.get("YOUTUBE_HARNESS_API_KEY") or ""
        self.timeout = timeout
        self._transport = transport          # inyectable para tests
        self.requester = requester

    # ── configuración ───────────────────────────────────────────────
    def is_configured(self) -> bool:
        return bool(self.base_url)

    def _headers(self) -> dict[str, str]:
        # Authorization: Bearer — el arnés lo acepta vía auth_guard
        # (X-API-Key o Bearer son vías equivalentes del mismo guard).
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _require_httpx(self):
        try:
            import httpx  # lazy: preserva CORE_ONLY
        except ImportError as exc:  # pragma: no cover
            raise HarnessConfigError(
                "httpx no está instalado (extras [api]/[a2a]) — "
                "necesario para consumir el YouTube Harness") from exc
        return httpx

    def _http(self):
        """Cliente httpx por llamada (patrón RestASRProvider)."""
        httpx = self._require_httpx()
        return httpx.Client(transport=self._transport, timeout=self.timeout)

    # ── llamadas ────────────────────────────────────────────────────
    def manifest(self) -> dict[str, Any]:
        if not self.base_url:
            raise HarnessConfigError(
                "YOUTUBE_HARNESS_URL no configurada — el host no sabe "
                "dónde vive el arnés")
        try:
            with self._http() as hc:
                resp = hc.get(f"{self.base_url}/api/harness/v1/manifest",
                              headers=self._headers())
        except HarnessConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HarnessTransportError(f"arnés inalcanzable: {exc}") from exc
        if resp.status_code >= 500:
            raise HarnessTransportError(f"arnés 5xx: {resp.status_code}")
        if resp.status_code != 200:
            raise HarnessProtocolError(
                f"manifest inesperado: HTTP {resp.status_code}")
        return resp.json()

    def execute(self, operation: str, *, input: dict | None = None,
                permissions: list[str] | None = None,
                constraints: dict | None = None,
                execution_id: str | None = None,
                correlation_id: str | None = None,
                metadata: dict | None = None) -> dict[str, Any]:
        """Ejecuta una operación del contrato y devuelve el HarnessResult.

        NO interpreta el negocio: devuelve el dict del contrato tal cual;
        la skill mapea status/outputs a su respuesta de coordinación.
        """
        if not self.base_url:
            raise HarnessConfigError(
                "YOUTUBE_HARNESS_URL no configurada — no se puede ejecutar "
                f"{operation!r}")
        payload: dict[str, Any] = {
            "contract_version": CONTRACT_VERSION,
            "execution_id": execution_id or _gen_execution_id(),
            "requester": self.requester,
            "capability": CAPABILITY_ID,
            "operation": operation,
            "input": dict(input or {}),
        }
        if permissions:
            payload["permissions"] = list(permissions)
        if constraints:
            payload["constraints"] = dict(constraints)
        if correlation_id:
            payload["correlation_id"] = correlation_id
        if metadata:
            payload["metadata"] = dict(metadata)
        try:
            with self._http() as hc:
                resp = hc.post(f"{self.base_url}/api/harness/v1/execute",
                               json=payload, headers=self._headers())
        except Exception as exc:  # noqa: BLE001
            raise HarnessTransportError(f"arnés inalcanzable: {exc}") from exc
        if resp.status_code >= 500:
            raise HarnessTransportError(f"arnés 5xx: {resp.status_code}")
        if resp.status_code in (400, 401, 403, 404, 409):
            detail: Any = {}
            try:
                detail = resp.json().get("detail", {})
            except Exception:  # noqa: BLE001
                detail = {"raw": resp.text[:300]}
            raise HarnessProtocolError(
                f"arnés rechazó la solicitud (HTTP {resp.status_code}): "
                f"{detail}")
        if resp.status_code != 200:
            raise HarnessProtocolError(
                f"respuesta fuera de contrato: HTTP {resp.status_code}")
        return resp.json()

    # ── conveniencias de coordinación (estados §9 ya normalizados) ──
    @staticmethod
    def result_status(result: dict[str, Any]) -> str:
        return str(result.get("status") or "")

    @staticmethod
    def result_errors(result: dict[str, Any]) -> list[dict[str, str]]:
        return list(result.get("errors") or [])

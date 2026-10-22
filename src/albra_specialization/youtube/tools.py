# ─────────────────────────────────────────────────────────────────────
# ALBRA SPECIALIZATION · youtube/tools.py
# ─────────────────────────────────────────────────────────────────────
# Tool EXTERNO del host hacia el arnés. Es el único camino HTTP: las
# skills lo declaran como dependencia y delegan en él (coordinación sin
# motor). Hereda firewall/policy/HITL/dry-run del host por ser Tool.
# ─────────────────────────────────────────────────────────────────────
from __future__ import annotations

from typing import Any

from albra_seed.core.base import Tool, ToolContract
from albra_seed.core.types import RiskLevel, RunContext, ToolResult
from albra_seed.core.base import new_id

from .client import (HarnessConfigError, HarnessProtocolError,
                     HarnessTransportError, YoutubeHarnessClient)

TOOL_NAME = "youtube_harness_execute"


class YoutubeHarnessTool(Tool):
    """Ejecuta una operación del Harness Contract V1 vía HTTP.

    is_external=True (efecto en otro sistema) y is_dry_run_capable=True
    (el dry-run devuelve la solicitud que se enviaría, sin enviarla —
    obligatorio para acción destructiva según el host).
    """

    def __init__(self, client: YoutubeHarnessClient | None = None) -> None:
        self.client = client or YoutubeHarnessClient()
        self.contract = ToolContract(
            name=TOOL_NAME,
            description="Ejecuta una operación del YouTube Harness "
                        "(automatización audiovisual) vía Harness Contract V1.",
            version="1.0.0",
            input_schema={
                "type": "object",
                "required": ["operation"],
                "properties": {
                    "operation": {"type": "string"},
                    "input": {"type": "object"},
                    "permissions": {"type": "array",
                                    "items": {"type": "string"}},
                    "constraints": {"type": "object"},
                    "execution_id": {"type": "string"},
                    "correlation_id": {"type": "string"},
                },
            },
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "outputs": {"type": "object"},
                    "errors": {"type": "array"},
                },
            },
            risk_level=RiskLevel.MEDIUM,
            permissions=["tool.youtube_harness.execute"],
            timeout_seconds=180,
            retry_policy={"max": 1},
            is_dry_run_capable=True,
            is_external=True,
        )

    async def execute(self, arguments: dict[str, Any], *,
                      context: RunContext | None = None,
                      dry_run: bool = False) -> ToolResult:
        call_id = new_id("tc")
        operation = str(arguments.get("operation") or "").strip()
        if not operation:
            return ToolResult(call_id=call_id, success=False,
                              error="falta «operation»")
        request = {
            "operation": operation,
            "input": dict(arguments.get("input") or {}),
            "permissions": list(arguments.get("permissions") or []),
            "constraints": dict(arguments.get("constraints") or {}),
            "execution_id": arguments.get("execution_id"),
            "correlation_id": (getattr(context, "correlation_id", None)
                               if context is not None else None),
        }
        if dry_run:
            # sin efectos: muestra qué se enviaría (el propio host exige
            # dry-run previo para acciones de riesgo)
            return ToolResult(call_id=call_id, success=True, output={
                "dry_run": True,
                "would_execute": operation,
                "configured": self.client.is_configured(),
                "request": {k: v for k, v in request.items()
                            if k != "execution_id" or v},
            })
        try:
            result = self.client.execute(
                operation,
                input=request["input"],
                permissions=request["permissions"] or None,
                constraints=request["constraints"] or None,
                execution_id=request.get("execution_id") or None,
                correlation_id=request.get("correlation_id") or None,
            )
        except HarnessConfigError as exc:
            return ToolResult(call_id=call_id, success=False,
                              error=f"harness no configurado: {exc}")
        except HarnessTransportError as exc:
            return ToolResult(call_id=call_id, success=False,
                              error=f"transporte: {exc}")
        except HarnessProtocolError as exc:
            return ToolResult(call_id=call_id, success=False,
                              error=f"protocolo: {exc}")
        return ToolResult(call_id=call_id, success=True, output=result)

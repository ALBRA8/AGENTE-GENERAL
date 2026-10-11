"""Pydantic request/response schemas for the API.

These schemas are deliberately tiny — they describe the wire format only.
They DO NOT depend on FastAPI and stay importable in CORE_ONLY mode.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# =====================================================================
# Health / readiness
# =====================================================================


class HealthResponse(BaseModel):
    status: str
    phase: str
    core_only: bool
    components: dict[str, str] = Field(default_factory=dict)
    sessions: int = 0
    uptime_seconds: float = 0.0


class ReadinessResponse(BaseModel):
    ready: bool
    phase: str


# =====================================================================
# Agent info / capabilities
# =====================================================================


class AgentInfoResponse(BaseModel):
    """Public agent manifest."""

    agent_id: str
    name: str
    version: str
    seed_version: str
    runtime_version: str
    specialization: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    autonomy_level: str = "L0_OBSERVE"
    risk_profile: str = "low"
    providers: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    memory_domains: list[str] = Field(default_factory=list)
    interfaces: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class CapabilityItem(BaseModel):
    name: str
    version: str | None = None
    enabled: bool = True
    requires_tools: list[str] = Field(default_factory=list)
    requires_providers: list[str] = Field(default_factory=list)
    requires_permissions: list[str] = Field(default_factory=list)
    risk_level: str = "low"
    is_external: bool = False


class CapabilitiesResponse(BaseModel):
    capabilities: list[CapabilityItem] = Field(default_factory=list)
    count: int = 0


# =====================================================================
# Execution
# =====================================================================


class ExecutionRequest(BaseModel):
    """Request body for POST /api/v1/execution."""

    input: str
    agent_id: str | None = None
    session_id: str | None = None
    timeout_seconds: float | None = None


class ExecutionResponse(BaseModel):
    """Response for POST /api/v1/execution and GET /api/v1/execution/{id}."""

    execution_id: str
    correlation_id: str
    content: str
    confidence: float = 0.0
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace: list[dict[str, Any]] = Field(default_factory=list)


class TraceResponse(BaseModel):
    """Response for GET /api/v1/execution/{execution_id}."""

    execution_id: str
    found: bool = False
    trace: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# =====================================================================
# Diagnostics (INTERNAL)
# =====================================================================


class DiagnosticsResponse(BaseModel):
    """Response for GET /internal/diagnostics."""

    generated_at: str
    overall_status: str
    summary: dict[str, Any] = Field(default_factory=dict)
    components: list[dict[str, Any]] = Field(default_factory=list)


# =====================================================================
# Error envelope
# =====================================================================


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
    code: str | None = None


__all__ = [
    "HealthResponse",
    "ReadinessResponse",
    "AgentInfoResponse",
    "CapabilityItem",
    "CapabilitiesResponse",
    "ExecutionRequest",
    "ExecutionResponse",
    "TraceResponse",
    "DiagnosticsResponse",
    "ErrorResponse",
]

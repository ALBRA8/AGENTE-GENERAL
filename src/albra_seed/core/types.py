"""Core types and enums shared across all ALBRA SEED components."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str = "") -> str:
    """Generate a deterministic-prefix UUID for traceability."""
    base = uuid.uuid4().hex
    return f"{prefix}_{base}" if prefix else base


# =====================================================================
# ROLES & MESSAGES
# =====================================================================


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    OBSERVER = "observer"


class Message(BaseModel):
    """A single message in a conversation or execution context."""

    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utc_now)
    correlation_id: str | None = None


class ToolCall(BaseModel):
    """A request from the agent to invoke a tool."""

    id: str = Field(default_factory=lambda: new_id("tc"))
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    requested_at: datetime = Field(default_factory=utc_now)


class ToolResult(BaseModel):
    """The result of executing a tool call."""

    call_id: str
    success: bool
    output: Any = None
    error: str | None = None
    evidence_id: str | None = None
    duration_ms: float | None = None
    completed_at: datetime = Field(default_factory=utc_now)


class AgentResponse(BaseModel):
    """The final response produced by an agent execution."""

    content: str
    role: Role = Role.ASSISTANT
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    execution_id: str
    correlation_id: str
    confidence: float = 0.0
    evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utc_now)


# =====================================================================
# SESSION
# =====================================================================


class Session(BaseModel):
    """A conversation/session context bound to an agent and a user/context."""

    session_id: str = Field(default_factory=lambda: new_id("sess"))
    agent_id: str
    user_id: str | None = None
    context_id: str | None = None
    domain: str = "default"
    messages: list[Message] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    last_active_at: datetime = Field(default_factory=utc_now)
    ttl_seconds: int = 3600

    def touch(self) -> None:
        self.last_active_at = utc_now()


# =====================================================================
# ENUMS — RISK, AUTONOMY, SEVERITY, STATUS
# =====================================================================


class Severity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class AutonomyLevel(int, Enum):
    """Autonomy ≠ Permission. Autonomy describes what the agent is ALLOWED to
    decide on its own. Permissions still gate every concrete action via the
    firewall + policy engine."""

    L0_OBSERVE = 0
    L1_RECOMMEND = 1
    L2_EXECUTE_SAFE = 2
    L3_EXECUTE_EXTERNAL = 3
    L4_EXECUTE_SENSITIVE = 4
    L5_SELF_IMPROVE = 5


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_EVIDENCE = "awaiting_evidence"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"
    DRY_RUN = "dry_run"


class EvidenceType(str, Enum):
    FACT = "fact"
    INFERENCE = "inference"
    ASSUMPTION = "assumption"
    UNKNOWN = "unknown"


class MemoryType(str, Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    FACTUAL = "factual"
    PROCEDURAL = "procedural"
    WORKING = "working"
    # DECISION is a non-destructive addition introduced by GAP-3. It stores
    # structured decision records as MemoryRecord(memory_type=DECISION) with
    # the decision payload embedded in the `content` dict. Existing memory
    # semantics are unchanged.
    DECISION = "decision"


class KnowledgeType(str, Enum):
    CODE = "code"
    FACTUAL = "factual"
    PROCEDURAL = "procedural"
    REFERENCE = "reference"
    POLICY = "policy"


class ComponentStatus(str, Enum):
    """Used by the Doctor. NEVER report VERIFIED unless actually verified."""

    VERIFIED = "verified"
    PARTIAL = "partial"
    MOCK = "mock"
    PLACEHOLDER = "placeholder"
    EXTERNAL_DEPENDENCY = "external_dependency"
    NOT_CONFIGURED = "not_configured"
    NOT_VERIFIED = "not_verified"
    MISSING = "missing"


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_HITL = "require_hitl"
    DRY_RUN = "dry_run"
    UNKNOWN = "unknown"


class HealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    WARNING = "warning"
    ERROR = "error"
    BLOCKED = "blocked"
    NOT_CONFIGURED = "not_configured"
    NOT_VERIFIED = "not_verified"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    ESCALATED = "escalated"


class CommunicationChannel(str, Enum):
    TEXT = "text"
    VOICE = "voice"
    IMAGE = "image"
    DOCUMENT = "document"
    API = "api"


class ModalityType(str, Enum):
    TEXT = "text"
    VOICE = "voice"
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"


# =====================================================================
# CONTRACTS — execution context, evidence, provenance
# =====================================================================


class RunContext(BaseModel):
    """Formal execution context. Every execution must reconstruct from this."""

    execution_id: str = Field(default_factory=lambda: new_id("exec"))
    correlation_id: str = Field(default_factory=lambda: new_id("corr"))
    parent_execution_id: str | None = None
    agent_id: str
    session_id: str | None = None
    task: str
    autonomy_level: AutonomyLevel = AutonomyLevel.L0_OBSERVE
    risk_level: RiskLevel = RiskLevel.UNKNOWN
    permissions: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    providers_used: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    approvals: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    status: ExecutionStatus = ExecutionStatus.PENDING
    # Isolation dimensions (MEM-WRITE). These mirror the Session fields of
    # the same name and are propagated from `session` by `Agent.run()` so the
    # agent loop (RECORD phase in particular) can write memory into the
    # correct (agent_id, domain, user_id, context_id) isolation bucket without
    # re-fetching the Session. Defaults keep existing call sites working — a
    # RunContext constructed without a Session stays in the "default" domain
    # with no user/context, exactly as before.
    user_id: str | None = None
    context_id: str | None = None
    domain: str = "default"
    # Free-form metadata (MEM-READ). Cross-cutting per-run data lives here so
    # phases can record auxiliary provenance (e.g. ``retrieved_memory_ids``)
    # without needing bespoke RunContext fields for every signal. Default
    # empty dict keeps existing call sites working unchanged.
    #
    # SECURITY NOTE (SEC-PERIMETER): ``metadata`` is PUBLIC and WRITABLE by
    # callers. It MUST NEVER be consulted for authorization decisions
    # (callers could otherwise forge ``actor_roles`` / ``actor_id`` by
    # stuffing values into metadata). Identity/role signals travel through
    # the dedicated ``verified_principal`` field below, which only the
    # IdentityResolver populates after authentication.
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Verified principal — set ONLY by the IdentityResolver after the
    # Authenticator verifies the caller. Callers CANNOT set this via
    # ``metadata`` (which is public/writable). This is the secure channel
    # for passing identity to the PolicyEngine. Typed as ``Any`` to avoid a
    # circular import (AuthPrincipal lives in ``albra_seed.security.auth``,
    # which itself imports from ``albra_seed.core.types``).
    verified_principal: Any = None  # AuthPrincipal | None


class Evidence(BaseModel):
    """Evidence contract. Distinguishes FACT / INFERENCE / ASSUMPTION / UNKNOWN.
    UNKNOWN must NEVER silently become FACT."""

    evidence_id: str = Field(default_factory=lambda: new_id("ev"))
    source: str
    source_type: str  # tool | provider | user | agent | external
    timestamp: datetime = Field(default_factory=utc_now)
    content: Any = None
    content_ref: str | None = None  # URL/path/hash reference if content is large
    evidence_type: EvidenceType = EvidenceType.UNKNOWN
    confidence: float = 0.0  # 0.0 .. 1.0
    provenance: list[str] = Field(default_factory=list)  # chain of evidence_ids
    execution_id: str | None = None
    integrity_hash: str | None = None


class Provenance(BaseModel):
    """Provenance record attached to inputs/outputs/memory/decisions."""

    provenance_id: str = Field(default_factory=lambda: new_id("prov"))
    source: str
    source_type: str
    timestamp: datetime = Field(default_factory=utc_now)
    confidence: float = 0.0
    evidence_ids: list[str] = Field(default_factory=list)
    parent_provenance_id: str | None = None
    execution_id: str | None = None
    correlation_id: str | None = None
    modified_at: datetime | None = None
    version: int = 1

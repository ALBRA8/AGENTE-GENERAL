"""CRM dispatch bridge — POST /crm/dispatch (R-AG-1, S4.4).

Specialization-layer hosted integration of AGENTE-GENERAL: implements
operation 1 of the versioned CRM WhatsPower contract
(``CONTEXTO CRM/06_INTEGRATIONS/CONTRATOS_INTEGRACION.md`` §4, mirrored in
``packages/contracts/src/dispatch.ts``): the CRM sends a
``conversation.turn.received`` event; this endpoint authenticates it,
validates the tenant identity, checks firewall/execution policies,
persists the turn into ORGANIZATION-SCOPED memory (T9) and acknowledges
receipt. The agent never answers through this HTTP response — it answers
through the CRM bot API (``POST /api/bot/messages``), as the contract
mandates.

This module lives in ``albra_specialization`` (NOT in ``albra_seed``):
the universal seed stays free of domain knowledge; hosted integrations
install themselves via :func:`install_crm_dispatch` through the generic
``extra_extensions`` hook of ``create_app``.

Security posture (fail-closed at every step):

1. Config: ``ALBRA_CRM_HMAC_SECRET`` + security perimeter + tenant memory
   must be configured, otherwise 503 — the bridge refuses to operate in an
   unverified state.
2. Bearer authentication (same authenticator as every public endpoint).
3. HMAC-SHA256(raw body + X-Timestamp, secret) with a ±300 s replay
   window and timing-safe comparison (contract §2/§8 heritage).
4. Tenant identity: ``X-Org-Id`` MUST equal ``organization.id`` and the
   D-014 ``organization.agentId`` MUST belong to that same organization
   (``org:<organizationId>``). Violations are the typed catalog error
   ``tenant_mismatch`` (403), never a soft warning.
5. RBAC: explicit ``crm:dispatch`` permission via PolicyEngine +
   PermissionRegistry (default-deny).
6. ActionFirewall verdict for the ``crm:dispatch`` action: ALLOW/DRY_RUN
   proceeds, DENY → ``firewall_denied``, no matching rule →
   ``no_matching_rule`` (default-deny), REQUIRE_HITL → ``approval_required``
   (202) so the CRM HITL queue surfaces the retention.
7. Per-organization rate limit → ``rate_limited`` (429) with Retry-After.
8. Idempotency: ``(org, conversation, event_id)`` ledger — replays return
   the original ACK without reprocessing (contract: ``event_id`` unique
   per conversation).

Errors use the FROZEN catalog of CRM WhatsPower (18 codes, D-013) with the
envelope ``{"error": {"code", "message", "details?"}}`` so the CRM adapter
classifies failures without guessing. Authentication-layer failures (401)
keep plain HTTP semantics like every other endpoint of this API.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import os
import time
from collections import deque
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from albra_seed.api.context import RequestContext
from albra_seed.core.types import MemoryType
from albra_seed.memory.isolator import MemoryIsolator

try:  # pragma: no cover - exercised only when fastapi is absent
    from fastapi import Request  # type: ignore[import-untyped]
except ImportError:  # keep the module importable without the [api] extra
    Request = Any  # type: ignore[assignment,misc]

# Replay window — mirrors HMAC_TIMESTAMP_TOLERANCE_SECONDS in
# packages/contracts (§8/§9 bridge heritage).
HMAC_TIMESTAMP_TOLERANCE_SECONDS = 300

# Envelope HTTP status per frozen catalog code (packages/contracts/errors.ts).
CATALOG_STATUS = {
    "tenant_mismatch": 403,
    "unauthorized_scope": 403,
    "firewall_denied": 403,
    "no_matching_rule": 403,
    "validation_failed": 422,
    "approval_required": 202,
    "rate_limited": 429,
}


class CrmDispatchError(Exception):
    """Typed catalog error carried to the HTTP layer by an exception handler.

    ``code`` MUST be a frozen CRM WhatsPower catalog code (D-013). The
    handler in ``albra_seed.api.app`` renders the envelope
    ``{"error": {"code", "message", "details?"}}`` with the catalog status.
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        if code not in CATALOG_STATUS:
            raise ValueError(f"crm dispatch error code not in the frozen catalog: {code!r}")
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers or {}
        super().__init__(message)


def crm_dispatch_error_response(exc: CrmDispatchError) -> Any:
    """Render a CrmDispatchError as the typed envelope JSONResponse."""
    from fastapi.responses import JSONResponse  # lazy

    error: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if exc.details is not None:
        error["details"] = exc.details
    headers = {"Cache-Control": "no-store", **exc.headers}
    return JSONResponse(
        status_code=CATALOG_STATUS[exc.code],
        content={"error": error},
        headers=headers,
    )


async def crm_dispatch_error_handler(request: Any, exc: CrmDispatchError) -> Any:
    """Starlette exception-handler signature (request, exc)."""
    return crm_dispatch_error_response(exc)


# ---------------------------------------------------------------------------
# Wire models — mirror CONVERSATION_TURN_RECEIVED in packages/contracts
# (dispatch.ts) field by field. Attribute names deliberately match the wire
# format (the CRM contract itself mixes snake_case event_id with camelCase
# agentId/messageIds), so no aliasing layer can drift from the contract.
# ---------------------------------------------------------------------------


class _DispatchConversation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    channel: Literal["whatsapp", "instagram", "messenger", "telegram", "tiktok"]
    test: bool


class _DispatchContact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    identity: str = Field(min_length=1)
    name: str | None = None
    stage: str | None = None
    aiPaused: bool | None = None


class _DispatchOrganization(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    agentId: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    locale: str = Field(min_length=1)


class _DispatchTurn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str = ""
    media: list[str] = Field(default_factory=list)
    sinceLastReplyMs: int = Field(ge=0)


class _DispatchCapabilities(BaseModel):
    model_config = ConfigDict(extra="ignore")

    booking: bool
    stageMoves: list[str] = Field(default_factory=list)
    windowOpenUntil: str = Field(min_length=1)


class CrmDispatchEvent(BaseModel):
    """conversation.turn.received — the §4 contract body."""

    model_config = ConfigDict(extra="ignore")

    event: Literal["conversation.turn.received"]
    event_id: str = Field(min_length=1)
    conversation: _DispatchConversation
    contact: _DispatchContact
    organization: _DispatchOrganization
    messageIds: list[str] = Field(min_length=1)
    turn: _DispatchTurn
    capabilities: _DispatchCapabilities
    signature: str | None = None


def _sanitize_validation_error(exc: ValidationError) -> list[dict[str, Any]]:
    """Sanitized field errors — never echo submitted values."""
    safe: list[dict[str, Any]] = []
    for error in exc.errors()[:25]:
        safe.append(
            {
                "loc": [str(item) for item in error.get("loc", ())],
                "msg": str(error.get("msg", "Invalid value")),
                "type": str(error.get("type", "value_error")),
            }
        )
    return safe


class _OrgRateLimiter:
    """Fixed-window per-organization limiter (process-local backstop)."""

    def __init__(self, limit_per_minute: int) -> None:
        self._limit = max(1, int(limit_per_minute))
        self._windows: dict[str, deque[float]] = {}

    def check(self, org_id: str) -> int | None:
        """Return None when allowed, else the Retry-After seconds."""
        now = time.monotonic()
        window = self._windows.setdefault(org_id, deque())
        while window and now - window[0] >= 60.0:
            window.popleft()
        if len(window) >= self._limit:
            retry_after = max(1, int(60.0 - (now - window[0])))
            return retry_after
        window.append(now)
        return None


class _DispatchLedger:
    """Bounded idempotency ledger keyed by (org, conversation, event_id)."""

    def __init__(self, max_entries: int = 8192) -> None:
        self._max = max(16, int(max_entries))
        self._seen: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._order: deque[tuple[str, str, str]] = deque()

    def get(self, key: tuple[str, str, str]) -> dict[str, Any] | None:
        return self._seen.get(key)

    def put(self, key: tuple[str, str, str], ack: dict[str, Any]) -> None:
        if key not in self._seen:
            self._seen[key] = ack
            self._order.append(key)
            while len(self._order) > self._max:
                oldest = self._order.popleft()
                self._seen.pop(oldest, None)


def build_crm_dispatch_router(ctx: RequestContext) -> Any:
    """Return a FastAPI APIRouter for POST /crm/dispatch.

    Mounted at ROOT level (the CRM dispatch URL is
    ``{AGENT_DISPATCH_URL}/crm/dispatch`` — §4), NOT under /api/v1.
    """
    from fastapi import APIRouter, Header, HTTPException  # lazy

    router = APIRouter(tags=["crm-dispatch"])

    secret = os.environ.get("ALBRA_CRM_HMAC_SECRET", "")
    rate_limit = _OrgRateLimiter(
        int(os.environ.get("ALBRA_CRM_DISPATCH_RATE_PER_MINUTE", "240") or 240)
    )
    ledger = _DispatchLedger()

    async def verified_principal(authorization: str | None) -> Any:
        """Authenticate API callers; fail-closed like /execution."""
        if not authorization:
            raise HTTPException(status_code=401, detail="bearer authentication required")
        scheme, separator, credential = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not credential.strip():
            raise HTTPException(status_code=401, detail="invalid authorization header")
        authenticator = ctx.runtime.get_component("authenticator")
        if authenticator is None:
            raise HTTPException(status_code=503, detail="authentication is not configured")
        try:
            principal = await authenticator.verify(credential.strip())
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=401, detail="invalid bearer credential") from exc
        if (
            principal is None
            or getattr(principal, "is_anonymous", True)
            or not getattr(principal, "identity", None)
        ):
            raise HTTPException(status_code=401, detail="authenticated principal required")
        return principal

    async def require_dispatch_permission(principal: Any) -> None:
        """Explicit RBAC grant for crm:dispatch — default deny, typed errors."""
        from albra_seed.core.types import RiskLevel

        policy = ctx.runtime.get_component("policy_engine")
        rule_set = getattr(policy, "rule_set", None)
        permission_registry = getattr(policy, "permission_registry", None)
        if (
            policy is None
            or rule_set is None
            or permission_registry is None
            or not hasattr(permission_registry, "has_any")
            or not hasattr(policy, "evaluate")
        ):
            raise HTTPException(
                status_code=503,
                detail="crm:dispatch authentication/RBAC is not configured",
            )
        roles = list(getattr(principal, "roles", []) or [])
        try:
            rule = rule_set.first_match(
                action_name="crm:dispatch",
                actor_id=principal.identity,
                risk=RiskLevel.UNKNOWN,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=403, detail="crm:dispatch policy lookup failed"
            ) from exc
        if rule is None or getattr(rule, "required_permission", None) != "crm:dispatch":
            raise CrmDispatchError(
                "unauthorized_scope",
                "crm:dispatch requires an explicit policy grant (default-deny)",
            )
        try:
            if not permission_registry.has_any(roles, "crm:dispatch"):
                raise CrmDispatchError(
                    "unauthorized_scope",
                    "crm:dispatch permission denied for the authenticated principal",
                )
            verdict = await policy.evaluate(
                "crm:dispatch",
                actor_roles=roles,
                actor_id=principal.identity,
            )
        except CrmDispatchError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=403, detail="crm:dispatch authorization failed"
            ) from exc
        if not getattr(verdict, "allowed", False):
            raise CrmDispatchError(
                "firewall_denied",
                "crm:dispatch blocked by the PolicyEngine (default-deny)",
            )

    async def check_firewall(principal: Any) -> str:
        """ActionFirewall verdict for the crm:dispatch action.

        Returns "allow" | "require_hitl"; DENY raises the typed catalog
        error. No matching rule is default-deny (no_matching_rule).
        """
        from albra_seed.core.types import PolicyDecision

        firewall = ctx.runtime.get_component("firewall")
        if firewall is None or not hasattr(firewall, "verdict"):
            return "allow"  # policy engine alone already granted RBAC
        try:
            verdict = await firewall.verdict("crm:dispatch", actor=principal.identity)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=403, detail="firewall evaluation failed") from exc
        decision = getattr(verdict, "decision", None)
        reason = str(getattr(verdict, "reason", "") or "")
        if decision in (PolicyDecision.ALLOW, PolicyDecision.DRY_RUN):
            return "allow"
        if decision == PolicyDecision.REQUIRE_HITL:
            return "require_hitl"
        if "no_matching_rule" in reason:
            raise CrmDispatchError(
                "no_matching_rule",
                "no firewall rule matches crm:dispatch (default-deny)",
            )
        raise CrmDispatchError(
            "firewall_denied",
            "crm:dispatch blocked by the ActionFirewall",
            details={"reason": reason} if reason else None,
        )

    def _verify_hmac(raw_body: bytes, signature: str | None, timestamp: str | None) -> None:
        """Timing-safe HMAC verification with a ±300 s replay window."""
        if not signature or not timestamp:
            raise HTTPException(
                status_code=401,
                detail="X-Signature and X-Timestamp are required for dispatch",
            )
        try:
            ts = int(timestamp)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail="invalid X-Timestamp") from exc
        if abs(time.time() - ts) > HMAC_TIMESTAMP_TOLERANCE_SECONDS:
            raise HTTPException(
                status_code=401,
                detail="X-Timestamp outside the tolerated window (±300 s)",
            )
        expected = hmac_mod.new(
            secret.encode("utf-8"),
            raw_body + str(ts).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac_mod.compare_digest(expected, signature.strip().lower()):
            raise HTTPException(status_code=401, detail="invalid request signature")

    async def _persist_audit(org_id: str, actor: str, event: CrmDispatchEvent) -> None:
        """Best-effort audit trail — a failure never blocks the ACK."""
        audit = ctx.runtime.get_component("audit_logger")
        if audit is None:
            return
        try:
            if hasattr(audit, "record_async"):
                await audit.record_async(
                    action="crm.dispatch",
                    actor=actor,
                    org_id=org_id,
                    event_id=event.event_id,
                    conversation_id=event.conversation.id,
                )
            elif hasattr(audit, "append"):
                await audit.append(
                    {
                        "action": "crm.dispatch",
                        "actor": actor,
                        "org_id": org_id,
                        "event_id": event.event_id,
                        "conversation_id": event.conversation.id,
                    }
                )
        except Exception:  # noqa: BLE001
            pass

    @router.post("/crm/dispatch")
    async def crm_dispatch(
        request: Request,
        authorization: str | None = Header(default=None, alias="Authorization"),
        x_signature: str | None = Header(default=None, alias="X-Signature"),
        x_timestamp: str | None = Header(default=None, alias="X-Timestamp"),
        x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
        x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
    ) -> Any:
        # ---- 1. Fail-closed configuration --------------------------------
        if not secret:
            raise HTTPException(
                status_code=503,
                detail=(
                    "crm dispatch bridge is not configured "
                    "(ALBRA_CRM_HMAC_SECRET missing)"
                ),
            )
        firewall = ctx.runtime.get_component("firewall")
        policy = ctx.runtime.get_component("policy_engine")
        if firewall is None and policy is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "crm dispatch requires a security perimeter "
                    "(firewall or policy engine)"
                ),
            )
        memory_manager = ctx.runtime.get_memory_manager()
        if memory_manager is None:
            raise HTTPException(
                status_code=503,
                detail="tenant memory is not configured (T9 isolation required)",
            )

        # ---- 2. Bearer authentication ------------------------------------
        principal = await verified_principal(authorization)

        # ---- 3. HMAC verification over the RAW body ----------------------
        raw_body = await request.body()
        _verify_hmac(raw_body, x_signature, x_timestamp)

        # ---- 4. Payload validation (typed 422) ---------------------------
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CrmDispatchError(
                "validation_failed", "request body is not valid JSON"
            ) from exc
        try:
            event = CrmDispatchEvent.model_validate(payload)
        except ValidationError as exc:
            raise CrmDispatchError(
                "validation_failed",
                "conversation.turn.received payload does not match the contract",
                details=_sanitize_validation_error(exc),
            ) from exc

        # ---- 5. Tenant identity (typed 403 tenant_mismatch) ---------------
        org_id = event.organization.id
        try:
            MemoryIsolator.validate_org_id(org_id)
        except ValueError as exc:
            raise CrmDispatchError(
                "validation_failed",
                "organization.id must match the CRM format 'org_<id>'",
            ) from exc
        if not x_org_id or x_org_id.strip() != org_id:
            raise CrmDispatchError(
                "tenant_mismatch",
                "X-Org-Id does not match organization.id in the signed body",
            )
        try:
            MemoryIsolator.assert_org_consistent(event.organization.agentId, org_id)
        except PermissionError as exc:
            raise CrmDispatchError(
                "tenant_mismatch",
                "organization.agentId belongs to a different organization "
                "(D-014 identity: org:<organizationId>#[agent])",
            ) from exc
        except ValueError as exc:
            raise CrmDispatchError(
                "validation_failed",
                "organization.agentId must use the D-014 format "
                "'org:<organizationId>#[agent]'",
            ) from exc

        # ---- 6. Rate limit per organization -------------------------------
        retry_after = rate_limit.check(org_id)
        if retry_after is not None:
            raise CrmDispatchError(
                "rate_limited",
                "dispatch rate limit reached for this organization",
                headers={"Retry-After": str(retry_after)},
            )

        # ---- 7. RBAC + firewall (execution policies) ----------------------
        await require_dispatch_permission(principal)
        fw_state = await check_firewall(principal)
        if fw_state == "require_hitl":
            # The firewall/policy defers this dispatch to a human decision:
            # 202 + approval_required is the contract's HITL retention
            # signal; the CRM maps it to approvalRequired=true and the turn
            # is NOT persisted until the human resolves it.
            raise CrmDispatchError(
                "approval_required",
                "crm:dispatch requires human approval (HITL retention)",
                details={"event_id": event.event_id, "stage": "dispatch_firewall"},
            )

        # ---- 8. Idempotency ------------------------------------------------
        ledger_key = (org_id, event.conversation.id, event.event_id)
        replayed = ledger.get(ledger_key)
        if replayed is not None:
            return dict(replayed)

        # ---- 9. Persist the turn into ORGANIZATION-SCOPED memory (T9) -----
        correlation_id = x_request_id if x_request_id else None
        try:
            await memory_manager.remember(
                agent_id=event.organization.agentId,
                org_id=org_id,
                domain="crm",
                user_id=event.contact.id,
                context_id=event.conversation.id,
                memory_type=MemoryType.EPISODIC,
                content={
                    "event_id": event.event_id,
                    "conversation": {
                        "id": event.conversation.id,
                        "channel": event.conversation.channel,
                        "test": event.conversation.test,
                    },
                    "contact": {
                        "id": event.contact.id,
                        "identity": event.contact.identity,
                        "name": event.contact.name,
                        "stage": event.contact.stage,
                        "aiPaused": event.contact.aiPaused,
                    },
                    "messageIds": list(event.messageIds),
                    "turn": {
                        "text": event.turn.text,
                        "media": list(event.turn.media),
                        "sinceLastReplyMs": event.turn.sinceLastReplyMs,
                    },
                    "capabilities": {
                        "booking": event.capabilities.booking,
                        "stageMoves": list(event.capabilities.stageMoves),
                        "windowOpenUntil": event.capabilities.windowOpenUntil,
                    },
                },
                confidence=0.3,
                source="crm_bridge",
                source_type="external",
                correlation_id=correlation_id,
            )
        except PermissionError as exc:
            raise CrmDispatchError(
                "tenant_mismatch",
                "memory isolation rejected the tenant context (org/agent mismatch)",
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"tenant memory rejected the event: {type(exc).__name__}",
            ) from exc

        # ---- 10. Audit (best-effort) + ACK ---------------------------------
        await _persist_audit(org_id, principal.identity, event)
        ack: dict[str, Any] = {
            "accepted": True,
            "event_id": event.event_id,
            "approvalRequired": False,
        }
        ledger.put(ledger_key, dict(ack))
        return ack

    return router


__all__ = [
    "build_crm_dispatch_router",
    "install_crm_dispatch",
    "CrmDispatchEvent",
    "CrmDispatchError",
]


def install_crm_dispatch(app: Any, ctx: Any) -> None:
    """Mount the CRM dispatch bridge onto a FastAPI app.

    Adds:
      * ``POST /crm/dispatch`` at ROOT level (the CRM dispatch URL is
        ``{AGENT_DISPATCH_URL}/crm/dispatch`` — §4 of the contract),
      * the exception handler that renders the typed catalog envelope
        (``{"error": {code, message, details?}}``) for the D-013 codes.

    ``ctx`` is the ``RequestContext`` created by ``create_app`` (also
    reachable as ``app.state.ctx``). Configuration checks fail closed
    inside the route itself (503 when ``ALBRA_CRM_HMAC_SECRET`` /
    perimeter / tenant memory are absent), so installing the bridge on an
    unconfigured app is safe: the endpoint simply refuses to operate.
    """
    app.add_exception_handler(CrmDispatchError, crm_dispatch_error_handler)
    app.include_router(build_crm_dispatch_router(ctx))

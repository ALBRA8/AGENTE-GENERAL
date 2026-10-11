"""Universal DNA catalog — the genetic blueprint every ALBRA agent carries.

This module is the **single source of truth** for what makes an ALBRA
agent an ALBRA agent. Every specialized agent (legal, sales, research,
trading, ...) MUST preserve this universal DNA. A specialization MAY
add components (see ``ALLOWED_SPECIALIZATIONS``) but MUST NOT remove or
replace any DNA component.

Hard rules enforced by ``ParityChecker``:

  1. Every entry in ``UNIVERSAL_DNA_COMPONENTS`` MUST be importable as
     ``albra_seed.<component>`` in the target Python environment.
  2. A specialization MAY register additional modules under the
     ``specialization.*`` namespace; those in ``ALLOWED_SPECIALIZATIONS``
     are recognised as benign and do NOT affect the purity flag.
  3. Any ``specialization.*`` module NOT in ``ALLOWED_SPECIALIZATIONS``
     is reported as an extra specialization component and causes
     ``ParityReport.is_pure_seed`` to be False.

NO domain knowledge is encoded here. The DNA names are deliberately
generic (``core.types``, ``runtime.runtime``, ``memory.manager`` ...).
A specialized agent's domain identity, knowledge, skills, tools and
policies live in ``specialization.*`` modules — never inside the seed.

A2A (Agent2Agent): the universal interoperability layer was added on
2026-10-12 by GAP-A2A-IMPL. The 8 A2A modules (``a2a.card``,
``a2a.contract``, ``a2a.adapter``, ``a2a.security``, ``a2a.transport``,
``a2a.server``, ``a2a.client``, ``a2a.registry``) are now part of the
universal DNA. A2A is a pure translation layer over Handoff — no new
business logic, no parallel identity store.

A2A helper modules — NOT in UNIVERSAL_DNA_COMPONENTS (BLOQ-DOCS):
  The following modules live under ``albra_seed.a2a`` but are NOT
  enumerated in ``UNIVERSAL_DNA_COMPONENTS``. They are implementation
  details of the universal A2A contract (the 8 modules above), NOT
  universal capabilities themselves. They are exercised transitively
  via the ``a2a`` audit-matrix probe and the ``tests/a2a/`` evidence
  chain. A specialization may replace any of them as long as the
  universal contract (the 8 modules above) is preserved.

  - ``a2a.wire``       — A2A 1.0 wire-format serialization helpers
                         (``agent_card_to_wire``, ``task_to_wire``,
                         JSON-RPC envelope builders). The universal
                         contract is "speak A2A 1.0" (carried by
                         ``a2a.transport`` / ``a2a.server`` /
                         ``a2a.client``); ``wire.py`` is one binding
                         of that contract. A different binding (e.g.
                         protobuf) could replace it without changing
                         the universal DNA.
  - ``a2a.exposition`` — ``build_a2a_server()`` convenience wrapper
                         that composes ``A2AServer`` +
                         ``HandoffManager`` into a single call. Pure
                         composition helper; callers may construct an
                         ``A2AServer`` directly.
  - ``a2a.observers``  — Audit / provenance / evidence hooks called by
                         ``a2a.server``. The universal infrastructure
                         (``audit.*``, ``provenance.*``,
                         ``evidence.*``) is already in
                         ``UNIVERSAL_DNA_COMPONENTS``;
                         ``observers.py`` is the A2A-specific binding
                         that wires those universal components into the
                         A2A receive / dispatch lifecycle.

  See ``docs/CERTIFICATION.md`` § "A2A Helper Modules" for the
  architectural rationale.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# UNIVERSAL DNA — must be present in EVERY ALBRA agent.
# Each entry corresponds to an importable module: ``albra_seed.<entry>``.
# A specialization MAY add modules but MUST NOT remove any of these.
#
# A2A helper modules NOT enumerated here (BLOQ-DOCS decision):
#   - a2a.wire       (A2A 1.0 wire-format serialization helpers)
#   - a2a.exposition (build_a2a_server() convenience wrapper)
#   - a2a.observers  (audit/provenance/evidence A2A binding)
# These are implementation details of the universal 8-module A2A contract
# below. The universal contract (the 8 modules enumerated as `a2a.card`,
# `a2a.contract`, `a2a.adapter`, `a2a.security`, `a2a.transport`,
# `a2a.server`, `a2a.client`, `a2a.registry`) IS the DNA; the helpers
# may be replaced without breaking that contract. See module docstring
# above and `docs/CERTIFICATION.md` § "A2A Helper Modules".
# ---------------------------------------------------------------------------

UNIVERSAL_DNA_COMPONENTS: list[str] = [
    # Core
    "core.types",
    "core.base",
    "core.identity",
    "core.config",
    "core.constants",
    # Runtime
    "runtime.runtime",
    "runtime.agent",
    "runtime.loop",
    "runtime.lifecycle",
    "runtime.state",
    # Cognitive
    "reasoning.engine",
    "reasoning.default",
    "reasoning.planner",
    "reasoning.prompts",
    "providers.registry",
    "providers.router",
    "providers.mock",
    "context.manager",
    "context.correlation",
    # Memory
    "memory.manager",
    "memory.sqlite_store",
    "memory.inmemory_store",
    "memory.provenance",
    "memory.isolator",
    "memory.consolidation",
    "memory.decisions",
    "memory.decision_store",  # GAP-3
    "provenance.tracker",
    "provenance.chain",
    "evidence.manager",
    "evidence.contract",
    "evidence.citation",
    "evidence.integrity",
    # Security
    "security.auth",
    "security.roles",
    "security.permissions",
    "security.secrets",
    "firewall.firewall",
    "firewall.kill_switch",
    "firewall.cooldown",
    "firewall.rate_limit",
    "policy.engine",
    "policy.evaluator",
    "policy.rules",
    "autonomy.levels",
    "autonomy.manager",
    "approvals.engine",
    # Execution
    "execution.runner",
    "execution.trace",
    "execution.idempotency",
    "workflows.engine",
    "workflows.graph",
    "workflows.executor",
    "jobs.manager",
    "jobs.queue",
    "scheduler.scheduler",
    "resilience.retry",
    "resilience.timeout",
    "resilience.fallback",
    "resilience.circuit_breaker",
    "resilience.degradation",
    # Observability
    "observability.logger",
    "observability.metrics",
    "observability.tracer",
    "observability.health",
    "observability.readiness",
    "audit.normal",
    "audit.tamper_evident",
    "audit.verifier",
    "doctor.doctor",
    "doctor.report",
    "doctor.matrix",
    "recovery.manager",
    "recovery.classifier",
    # Integration
    "mcp.server",
    "mcp.client",
    "mcp.registry",
    "handoff.manager",
    "handoff.contract",
    "communication.router",
    "communication.adapter",
    "multimodal.base",
    "multimodal.text",
    "files.handler",
    # Extensibility
    "tools.registry",
    "tools.executor",
    "capabilities.registry",
    "capabilities.resolver",
    "skills.registry",
    "skills.contract",
    "skills.lifecycle",
    "skills.evolution",  # GAP-4
    "plugins.manager",
    # Interfaces
    "api.app",
    "cli.cli",
    "config.manager",
    # NEW (gap closures)
    "feedback.manager",  # GAP-1
    "learning.manager",  # GAP-2
    # A2A (Agent2Agent) — universal interoperability layer
    # Added 2026-10-12 by GAP-A2A-IMPL. Pure translation layer over
    # Handoff; AgentCard is DERIVED from AgentManifest (single source
    # of truth). CORE_ONLY birth preserved (InProcessTransport always
    # available, HTTPTransport lazy-imports httpx).
    "a2a.card",
    "a2a.contract",
    "a2a.adapter",
    "a2a.security",
    "a2a.transport",
    "a2a.server",
    "a2a.client",
    "a2a.registry",
]

# ---------------------------------------------------------------------------
# ALLOWED SPECIALIZATIONS — a specialization MAY add these (not required).
# These are recognised as benign: their presence does NOT make
# `is_pure_seed` False. Anything NOT in this list (and NOT part of the
# UNIVERSAL_DNA_COMPONENTS) is reported as an extra specialization
# component and DOES make `is_pure_seed` False.
# ---------------------------------------------------------------------------

ALLOWED_SPECIALIZATIONS: list[str] = [
    "specialization.identity",
    "specialization.knowledge",
    "specialization.skills",
    "specialization.tools",
    "specialization.workflows",
    "specialization.policies",
    "specialization.memory",
    "specialization.prompts",
]


__all__ = [
    "UNIVERSAL_DNA_COMPONENTS",
    "ALLOWED_SPECIALIZATIONS",
]

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
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# UNIVERSAL DNA — must be present in EVERY ALBRA agent.
# Each entry corresponds to an importable module: ``albra_seed.<entry>``.
# A specialization MAY add modules but MUST NOT remove any of these.
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

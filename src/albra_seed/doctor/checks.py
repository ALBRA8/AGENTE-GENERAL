"""CheckRegistry + CheckResult — diagnostic check primitives.

A "check" is an async function that probes ONE component and returns
a `CheckResult`. Checks MUST actually run their probe — they MUST NOT
return HEALTHY+IMPLEMENTED based on structural inspection alone. This
is the difference between "the Doctor says it works" and "the Doctor
says it's there."

The 10 built-in checks live in `albra_seed.doctor.builtin_checks`.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from albra_seed.core.types import HealthState
from albra_seed.doctor.report import (
    ComponentDiagnostic,
    ImplementationStatus,
)


@dataclass
class CheckResult:
    """Outcome of running a single diagnostic check."""

    name: str
    status: HealthState
    implementation_status: ImplementationStatus
    details: str = ""
    evidence: list[str] = field(default_factory=list)
    duration_ms: float | None = None

    def to_diagnostic(self) -> ComponentDiagnostic:
        return ComponentDiagnostic(
            name=self.name,
            status=self.status,
            implementation_status=self.implementation_status,
            details=self.details,
            evidence=list(self.evidence),
            duration_ms=self.duration_ms,
        )


CheckFn = Callable[[], Awaitable[CheckResult]]


class CheckRegistry:
    """Registry of named async checks.

    The registry itself is synchronous — only `run` / `run_all` are
    async. Checks are stored by name (one per name); re-registering
    overwrites.
    """

    def __init__(self) -> None:
        self._checks: dict[str, CheckFn] = {}

    def register(self, name: str, fn: CheckFn) -> None:
        self._checks[name] = fn

    def unregister(self, name: str) -> bool:
        return self._checks.pop(name, None) is not None

    def names(self) -> list[str]:
        return sorted(self._checks.keys())

    def get(self, name: str) -> CheckFn | None:
        return self._checks.get(name)

    async def run(self, name: str) -> CheckResult:
        fn = self._checks.get(name)
        if fn is None:
            return CheckResult(
                name=name,
                status=HealthState.NOT_CONFIGURED,
                implementation_status=ImplementationStatus.NOT_CONFIGURED,
                details=f"no check registered for '{name}'",
            )
        start = time.perf_counter()
        try:
            result = await fn()
        except Exception as exc:  # noqa: BLE001 — doctor must not crash
            elapsed = (time.perf_counter() - start) * 1000.0
            return CheckResult(
                name=name,
                status=HealthState.ERROR,
                implementation_status=ImplementationStatus.NOT_VERIFIED,
                details=f"check raised: {exc!r}",
                evidence=[],
                duration_ms=elapsed,
            )
        if result.duration_ms is None:
            result.duration_ms = (time.perf_counter() - start) * 1000.0
        return result

    async def run_all(self) -> list[CheckResult]:
        if not self._checks:
            return []
        tasks = [self.run(name) for name in sorted(self._checks.keys())]
        return await asyncio.gather(*tasks)


__all__ = ["CheckRegistry", "CheckResult", "CheckFn"]

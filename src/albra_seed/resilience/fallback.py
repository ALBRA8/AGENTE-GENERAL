"""FallbackChain — tries operations in order, returns first success.

Each operation is an async callable returning either a value or raising.
If a callable returns None and `treat_none_as_failure` is True, the chain
advances to the next operation. The first successful (non-exceptional)
result wins.

`FallbackChain.run` returns a `FallbackOutcome` capturing which operation
won, the result, and the list of errors encountered.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


@dataclass
class FallbackOutcome:
    success: bool
    value: Any = None
    winner_index: int | None = None
    winner_name: str | None = None
    errors: list[str] = field(default_factory=list)


class FallbackChain:
    """Ordered list of operations. Returns the first success."""

    def __init__(
        self,
        operations: list[Callable[[], Awaitable[Any]]] | None = None,
        *,
        names: list[str] | None = None,
        treat_none_as_failure: bool = True,
    ) -> None:
        self._operations: list[Callable[[], Awaitable[Any]]] = list(operations or [])
        self._names: list[str] = list(names or [])
        if not self._names:
            self._names = [f"op_{i}" for i in range(len(self._operations))]
        self.treat_none_as_failure = treat_none_as_failure

    def add(self, op: Callable[[], Awaitable[Any]], *, name: str = "") -> "FallbackChain":
        self._operations.append(op)
        self._names.append(name or f"op_{len(self._operations) - 1}")
        return self

    async def run(self) -> FallbackOutcome:
        errors: list[str] = []
        for idx, op in enumerate(self._operations):
            name = self._names[idx]
            try:
                value = await op()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            if value is None and self.treat_none_as_failure:
                errors.append(f"{name}: returned None")
                continue
            return FallbackOutcome(
                success=True,
                value=value,
                winner_index=idx,
                winner_name=name,
                errors=errors,
            )
        return FallbackOutcome(success=False, value=None, errors=errors)

    async def call(self) -> Any:
        outcome = await self.run()
        if not outcome.success:
            raise RuntimeError(
                f"all {len(self._operations)} fallback operations failed: "
                + "; ".join(outcome.errors)
            )
        return outcome.value


__all__ = ["FallbackChain", "FallbackOutcome"]

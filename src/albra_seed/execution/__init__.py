"""Execution subpackage — runner, traces, idempotency, replay.

Public API:
    ExecutionRunner   — orchestrates a single execution
    ExecutionTrace    — detailed per-execution trace
    TraceStep         — a single phase entry on a trace
    IdempotencyManager — detects duplicate execution keys (safe retries)
    TraceReplay       — replays an execution from its trace
"""

from albra_seed.execution.runner import ExecutionRunner, CANONICAL_PHASES, PhaseHandler
from albra_seed.execution.trace import ExecutionTrace
from albra_seed.execution.step import TraceStep
from albra_seed.execution.idempotency import IdempotencyManager, IdempotencyRecord
from albra_seed.execution.replay import TraceReplay, ReplayResult, ReplayHandler

__all__ = [
    "ExecutionRunner",
    "ExecutionTrace",
    "TraceStep",
    "IdempotencyManager",
    "IdempotencyRecord",
    "TraceReplay",
    "ReplayResult",
    "ReplayHandler",
    "CANONICAL_PHASES",
    "PhaseHandler",
]

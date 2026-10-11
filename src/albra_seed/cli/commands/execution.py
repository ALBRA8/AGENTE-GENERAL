"""`albra execution` — show last execution trace."""

from __future__ import annotations

import argparse
from typing import Any

from albra_seed.cli.context import CLIContext
from albra_seed.cli.formatter import Formatter


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "execution",
        help="show last execution trace",
        description="Show the last agent execution's phase trace.",
    )
    p.add_argument(
        "--execution-id",
        default=None,
        help="specific execution id to look up",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=10,
        help="max number of trace steps to show (default: 10)",
    )
    p.set_defaults(func=run)


def run(ctx: CLIContext, args: argparse.Namespace) -> int:
    fmt = Formatter(ctx.formatter_name)
    agent = ctx.default_agent
    if agent is None:
        fmt.render_kv({
            "execution": None,
            "note": "no agent registered; cannot show trace",
        })
        return 0
    state = ctx.runtime.get_agent_state(agent.identity.agent_id)
    last_id = (
        getattr(state, "current_execution_id", None) if state else None
    )
    execution_id = args.execution_id or last_id
    if not execution_id:
        fmt.render_kv({
            "execution": None,
            "note": "no execution recorded yet",
        })
        return 0
    # Trace lives on the RunContext produced by Agent.run. For CORE_ONLY
    # CLI usage we expose whatever the agent state recorded.
    trace: list[dict[str, Any]] = []
    if state is not None:
        # Prefer the trace steps recorded on the current RunContext's
        # outputs; fall back to a single summary entry if unavailable.
        run_ctx = getattr(state, "current_context", None)
        outs = getattr(run_ctx, "outputs", {}) if run_ctx else {}
        for step in list(outs.get("trace", []))[-args.limit:]:
            trace.append(step)
        if not trace and getattr(state, "last_response", None) is not None:
            trace.append({
                "execution_id": execution_id,
                "status": "succeeded" if state.last_response.errors == [] else "failed",
                "note": "summary only — phase trace unavailable",
            })
    payload = {
        "execution_id": execution_id,
        "agent_id": agent.identity.agent_id,
        "trace": trace,
        "count": len(trace),
    }
    fmt.render(payload, title="execution trace")
    return 0


__all__ = ["register", "run"]

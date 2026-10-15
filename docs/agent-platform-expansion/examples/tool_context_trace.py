"""Runnable offline example: validated tool call with context and trace.

Run from the repository root after installing the project:
    python docs/agent-platform-expansion/examples/tool_context_trace.py
"""
from __future__ import annotations

import asyncio

from albra_seed.core.types import RunContext, ToolResult, new_id
from albra_seed.observability.tracer import Tracer
from albra_seed.toolkit import ToolDefinition, ToolRegistryV2


async def main() -> None:
    context = RunContext(agent_id="example-agent", task="format a local demo response")
    tracer = Tracer(correlation_id=context.correlation_id)
    span = tracer.start_span(
        "tool.execute",
        tool="format_response",
        execution_id=context.execution_id,
    )
    registry = ToolRegistryV2()

    async def format_response(arguments, *, context=None):
        return ToolResult(
            call_id=new_id("tc"),
            success=True,
            output={"response": f"{context.task}: {arguments['text']}"},
        )

    registry.register(
        ToolDefinition(
            name="format_response",
            description="Formats a response using the supplied execution context",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"response": {"type": "string"}},
                "required": ["response"],
            },
        ),
        format_response,
    )
    try:
        result = await registry.execute(
            "format_response", {"text": "hello"}, context=context
        )
        if not result.success:
            tracer.end_span(span, error=result.error or "tool failed")
            raise RuntimeError(result.error or "tool failed")
        tracer.end_span(span)
        print({"result": result.output, "trace": tracer.collect()})
    except Exception as exc:
        if span.end_ns is None:
            tracer.end_span(span, error=type(exc).__name__)
        raise


if __name__ == "__main__":
    asyncio.run(main())

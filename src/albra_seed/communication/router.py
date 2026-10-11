"""Communication router — routes messages by channel via registered adapters.

The router never hardcodes any specific transport (no WhatsApp / Telegram /
email / SMTP dependencies). It resolves the adapter for the message's
channel via the `ChannelRegistry` and delegates send/receive to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from albra_seed.core.types import CommunicationChannel
from albra_seed.communication.adapter import ChannelAdapter, TextChannelAdapter
from albra_seed.communication.message import CommunicationMessage
from albra_seed.communication.registry import ChannelRegistry

# Audit sink for routing decisions.
RouteAuditSink = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class RouteResult:
    """Outcome of a single routing operation."""

    channel: CommunicationChannel
    adapter_name: str
    sent: bool
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class CommunicationRouter:
    """Routes messages to the appropriate channel adapter.

    The TEXT adapter is registered by default; additional channels
    (voice/image/document/api) can be registered by specialisation layers
    after construction.
    """

    def __init__(
        self,
        registry: ChannelRegistry | None = None,
        *,
        text_adapter: TextChannelAdapter | None = None,
        audit_sink: RouteAuditSink | None = None,
    ) -> None:
        self.registry = registry or ChannelRegistry()
        self._audit_sink = audit_sink
        # Always register a TEXT adapter — it is the universal default.
        self._text = text_adapter or TextChannelAdapter()
        self.registry.register(self._text)

    @property
    def text_adapter(self) -> TextChannelAdapter:
        return self._text

    # -- registration ------------------------------------------------
    def register_adapter(self, adapter: ChannelAdapter) -> None:
        self.registry.register(adapter)

    def unregister_adapter(
        self, name_or_channel: str | CommunicationChannel
    ) -> bool:
        # Never allow unregistering the default TEXT adapter.
        adapter = self.registry.get(name_or_channel)
        if adapter is None or adapter is self._text:
            return False
        return self.registry.unregister(name_or_channel)

    # -- routing -----------------------------------------------------
    async def route(self, message: CommunicationMessage) -> RouteResult:
        """Route `message` to its channel adapter and dispatch it."""
        adapter = self.registry.get(message.channel)
        if adapter is None:
            result = RouteResult(
                channel=message.channel,
                adapter_name="",
                sent=False,
                error=f"no adapter registered for channel: {message.channel.value}",
            )
            await self._audit(message, result)
            return result
        if not adapter.available:
            result = RouteResult(
                channel=message.channel,
                adapter_name=adapter.name,
                sent=False,
                error=f"adapter not available: {adapter.name}",
            )
            await self._audit(message, result)
            return result
        try:
            sent = await adapter.send(message)
        except Exception as exc:
            result = RouteResult(
                channel=message.channel,
                adapter_name=adapter.name,
                sent=False,
                error=f"adapter raised: {exc}",
            )
            await self._audit(message, result)
            return result
        result = RouteResult(
            channel=message.channel,
            adapter_name=adapter.name,
            sent=sent,
        )
        await self._audit(message, result)
        return result

    async def receive_all(self) -> list[CommunicationMessage]:
        """Pull inbound messages from every registered adapter."""
        out: list[CommunicationMessage] = []
        for adapter in self.registry.list_adapters():
            if not adapter.available:
                continue
            try:
                out.extend(await adapter.receive())
            except Exception:
                # A failing adapter must not block the others.
                continue
        return out

    # -- audit -------------------------------------------------------
    async def _audit(
        self, message: CommunicationMessage, result: RouteResult
    ) -> None:
        if self._audit_sink is None:
            return
        entry = {
            "message_id": message.message_id,
            "channel": message.channel.value,
            "sender": message.sender,
            "recipient": message.recipient,
            "adapter": result.adapter_name,
            "sent": result.sent,
            "error": result.error,
        }
        try:
            await self._audit_sink(entry)
        except Exception:
            pass


__all__ = ["CommunicationRouter", "RouteResult", "RouteAuditSink"]

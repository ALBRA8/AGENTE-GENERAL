"""Channel adapters — abstract interface + default TEXT adapter.

Adapters convert between `CommunicationMessage` instances and the
transport-specific envelope of a channel (REST call, SMTP payload, voice
frame, etc.). The SEED ships only `TextChannelAdapter`; concrete adapters
for voice/image/document/api are registered by specialisation layers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable

from albra_seed.core.types import CommunicationChannel
from albra_seed.communication.message import CommunicationMessage

# A sink is an async callable that receives messages from external systems
# (e.g. webhook handler) and forwards them into the agent.
InboundSink = Callable[[CommunicationMessage], Awaitable[None]]


class ChannelAdapter(ABC):
    """Abstract channel adapter.

    Subclasses implement `send` (outbound), `receive` (inbound pull) and
    `health`. All adapters MUST be safe to instantiate even when their
    transport is unavailable — they should simply report `available=False`.
    """

    channel: CommunicationChannel
    name: str

    def __init__(self, *, available: bool = True) -> None:
        self._available = available
        self._inbound_sinks: list[InboundSink] = []

    @property
    def available(self) -> bool:
        return self._available

    def set_available(self, available: bool) -> None:
        self._available = available

    @abstractmethod
    async def send(self, message: CommunicationMessage) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def receive(self) -> list[CommunicationMessage]:
        raise NotImplementedError

    @abstractmethod
    async def health(self) -> bool:
        raise NotImplementedError

    # -- inbound sinks ----------------------------------------------
    def subscribe(self, sink: InboundSink) -> None:
        self._inbound_sinks.append(sink)

    def unsubscribe(self, sink: InboundSink) -> bool:
        try:
            self._inbound_sinks.remove(sink)
            return True
        except ValueError:
            return False

    async def _dispatch_inbound(self, message: CommunicationMessage) -> None:
        for sink in list(self._inbound_sinks):
            try:
                await sink(message)
            except Exception:
                # A single failing sink must not block the others.
                continue


class TextChannelAdapter(ChannelAdapter):
    """Default TEXT adapter — always available.

    Outbound messages are echoed into an in-memory ring buffer; inbound
    messages can be injected via `inject_inbound` for tests.
    """

    channel = CommunicationChannel.TEXT
    name = "text"

    def __init__(
        self,
        *,
        buffer_size: int = 256,
        available: bool = True,
    ) -> None:
        super().__init__(available=available)
        self._buffer_size = buffer_size
        self._sent: list[CommunicationMessage] = []
        self._inbound: list[CommunicationMessage] = []

    async def send(self, message: CommunicationMessage) -> bool:
        if not self._available:
            return False
        if message.channel != self.channel:
            return False
        self._sent.append(message)
        if len(self._sent) > self._buffer_size:
            self._sent = self._sent[-self._buffer_size :]
        return True

    async def receive(self) -> list[CommunicationMessage]:
        messages = list(self._inbound)
        self._inbound.clear()
        return messages

    async def health(self) -> bool:
        return self._available

    # -- test helper -------------------------------------------------
    async def inject_inbound(self, message: CommunicationMessage) -> None:
        """Simulate an inbound message arriving from the channel."""
        self._inbound.append(message)
        await self._dispatch_inbound(message)

    def sent_messages(self) -> list[CommunicationMessage]:
        return list(self._sent)


__all__ = [
    "ChannelAdapter",
    "TextChannelAdapter",
    "InboundSink",
]

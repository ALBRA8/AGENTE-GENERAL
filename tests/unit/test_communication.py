"""Unit tests for the communication subpackage."""

import asyncio

import pytest

from albra_seed.communication import (
    Attachment,
    ChannelAdapter,
    ChannelRegistry,
    CommunicationMessage,
    CommunicationRouter,
    RouteResult,
    TextChannelAdapter,
)
from albra_seed.core.types import CommunicationChannel, Message, Role


def _msg(
    channel: CommunicationChannel = CommunicationChannel.TEXT,
    content: str = "hello",
) -> CommunicationMessage:
    return CommunicationMessage(
        channel=channel,
        sender="A",
        recipient="B",
        body=Message(role=Role.ASSISTANT, content=content),
    )


# -------------------------------------------------------------------
# 1. Router routes text messages via the default TEXT adapter
# -------------------------------------------------------------------


def test_communication_router_routes_text():
    router = CommunicationRouter()
    msg = CommunicationMessage.text(
        sender="agent-A", recipient="agent-B", content="ping"
    )
    result = asyncio.run(router.route(msg))
    assert isinstance(result, RouteResult)
    assert result.channel == CommunicationChannel.TEXT
    assert result.adapter_name == "text"
    assert result.sent is True
    assert router.text_adapter.sent_messages()[0].body.content == "ping"


# -------------------------------------------------------------------
# 2. TextChannelAdapter is always available by default
# -------------------------------------------------------------------


def test_text_channel_adapter_default():
    adapter = TextChannelAdapter()
    assert adapter.channel == CommunicationChannel.TEXT
    assert adapter.name == "text"
    assert adapter.available is True

    msg = CommunicationMessage.text(
        sender="alice", recipient="bob", content="hello"
    )
    sent = asyncio.run(adapter.send(msg))
    assert sent is True

    inbound = CommunicationMessage.text(
        sender="bob", recipient="alice", content="hi"
    )
    asyncio.run(adapter.inject_inbound(inbound))

    # The injected message is available for receive() to drain.
    received = asyncio.run(adapter.receive())
    assert len(received) == 1
    assert received[0].body.content == "hi"
    # Drain semantics: second receive returns empty.
    assert asyncio.run(adapter.receive()) == []

    # An unavailable adapter refuses to send.
    adapter.set_available(False)
    assert asyncio.run(adapter.send(msg)) is False
    assert asyncio.run(adapter.health()) is False


# -------------------------------------------------------------------
# 3. ChannelRegistry registers + resolves adapters
# -------------------------------------------------------------------


def test_channel_registry_register():
    registry = ChannelRegistry()
    text = TextChannelAdapter()
    registry.register(text)
    assert len(registry) == 1

    # Resolve by enum.
    assert registry.get(CommunicationChannel.TEXT) is text
    # Resolve by name string.
    assert registry.get("text") is text

    assert registry.is_available("text") is True
    assert registry.list_channels() == [CommunicationChannel.TEXT]
    assert registry.unregister("text") is True
    assert registry.get("text") is None


# -------------------------------------------------------------------
# Extra: router rejects unknown channel
# -------------------------------------------------------------------


def test_router_rejects_unknown_channel():
    router = CommunicationRouter()
    msg = _msg(channel=CommunicationChannel.VOICE, content="audio")
    result = asyncio.run(router.route(msg))
    assert result.sent is False
    assert result.adapter_name == ""
    assert "no adapter registered" in (result.error or "")


# -------------------------------------------------------------------
# Extra: router never lets TEXT adapter be unregistered
# -------------------------------------------------------------------


def test_router_text_adapter_is_protected():
    router = CommunicationRouter()
    assert router.unregister_adapter(CommunicationChannel.TEXT) is False
    assert router.registry.get("text") is not None


# -------------------------------------------------------------------
# Extra: router routes to a custom adapter
# -------------------------------------------------------------------


class _StubDocumentAdapter(ChannelAdapter):
    channel = CommunicationChannel.DOCUMENT
    name = "document"

    def __init__(self) -> None:
        super().__init__(available=True)
        self._sent: list[CommunicationMessage] = []

    async def send(self, message: CommunicationMessage) -> bool:
        self._sent.append(message)
        return True

    async def receive(self) -> list[CommunicationMessage]:
        return []

    async def health(self) -> bool:
        return self.available


def test_router_routes_custom_adapter():
    router = CommunicationRouter()
    stub = _StubDocumentAdapter()
    router.register_adapter(stub)
    msg = _msg(channel=CommunicationChannel.DOCUMENT, content="doc")
    result = asyncio.run(router.route(msg))
    assert result.sent is True
    assert result.adapter_name == "document"
    assert stub._sent[0] is msg


def test_attachment_roundtrip():
    a = Attachment(
        name="file.txt", uri="file:///tmp/file.txt", mime_type="text/plain"
    )
    assert a.name == "file.txt"
    msg = CommunicationMessage(
        sender="A",
        recipient="B",
        body=Message(role=Role.USER, content="see attached"),
        attachments=[a],
    )
    assert msg.attachments[0].uri == "file:///tmp/file.txt"

"""Channel registry — registers adapters and resolves them by channel name.

The registry is intentionally minimal: `register(adapter)`, `get(channel)`,
`list_channels()`. Adapters must declare both a `name` (string) and a
`channel` (CommunicationChannel enum) so callers can resolve by either.
"""

from __future__ import annotations

from typing import Iterable

from albra_seed.core.types import CommunicationChannel
from albra_seed.communication.adapter import ChannelAdapter


class ChannelRegistry:
    """Registry of channel adapters keyed by both `name` and `channel`."""

    def __init__(self) -> None:
        self._by_name: dict[str, ChannelAdapter] = {}
        self._by_channel: dict[CommunicationChannel, ChannelAdapter] = {}

    def register(self, adapter: ChannelAdapter) -> None:
        if not isinstance(adapter, ChannelAdapter):
            raise TypeError("adapter must be a ChannelAdapter")
        self._by_name[adapter.name] = adapter
        self._by_channel[adapter.channel] = adapter

    def unregister(self, name_or_channel: str | CommunicationChannel) -> bool:
        adapter = self.get(name_or_channel)
        if adapter is None:
            return False
        self._by_name.pop(adapter.name, None)
        self._by_channel.pop(adapter.channel, None)
        return True

    def get(
        self, name_or_channel: str | CommunicationChannel
    ) -> ChannelAdapter | None:
        if isinstance(name_or_channel, CommunicationChannel):
            return self._by_channel.get(name_or_channel)
        # Allow str lookups by name OR by enum value.
        adapter = self._by_name.get(name_or_channel)
        if adapter is not None:
            return adapter
        try:
            ch = CommunicationChannel(name_or_channel)
        except ValueError:
            return None
        return self._by_channel.get(ch)

    def list_channels(self) -> list[CommunicationChannel]:
        return list(self._by_channel.keys())

    def list_adapters(self) -> list[ChannelAdapter]:
        return list(self._by_name.values())

    def __iter__(self) -> Iterable[ChannelAdapter]:
        return iter(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)

    def is_available(self, name_or_channel: str | CommunicationChannel) -> bool:
        adapter = self.get(name_or_channel)
        return adapter is not None and adapter.available


__all__ = ["ChannelRegistry"]

"""Communication message model.

Extends the core `Message` with channel + sender + recipient + attachment
metadata, without depending on any concrete transport (WhatsApp/Telegram/
email/etc.). Attachments are passed as URI strings so large payloads are
not embedded directly.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import CommunicationChannel, Message, Role, utc_now


class Attachment(BaseModel):
    """A reference to an external payload attached to a message."""

    name: str
    uri: str  # path, URL, data URI, etc. — never the bytes themselves
    mime_type: str = "application/octet-stream"
    size_bytes: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CommunicationMessage(BaseModel):
    """A universal communication message.

    This is intentionally NOT a subclass of `core.types.Message` so the
    communication layer can evolve independently. It composes the core
    Message inside `body` for back-compat with the rest of the SEED."""

    message_id: str = Field(default_factory=lambda: f"msg_{utc_now().timestamp()}")
    channel: CommunicationChannel = CommunicationChannel.TEXT
    sender: str
    recipient: str
    body: Message
    attachments: list[Attachment] = Field(default_factory=list)
    in_reply_to: str | None = None
    correlation_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def text(
        cls,
        *,
        sender: str,
        recipient: str,
        content: str,
        name: str | None = None,
        correlation_id: str | None = None,
    ) -> "CommunicationMessage":
        """Convenience constructor for the default TEXT channel."""
        return cls(
            channel=CommunicationChannel.TEXT,
            sender=sender,
            recipient=recipient,
            body=Message(role=Role.USER, content=content, name=name),
            correlation_id=correlation_id,
        )

    def to_text(self) -> str:
        """Returns a plain-text rendering of the message body."""
        return self.body.content


__all__ = ["CommunicationMessage", "Attachment"]

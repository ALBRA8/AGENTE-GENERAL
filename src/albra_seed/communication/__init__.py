"""Communication subpackage — universal communication router.

Routes `CommunicationMessage` instances to channel adapters. The default
`TextChannelAdapter` is always available. NO domain transports
(WhatsApp/Telegram/email/SMTP) are required — those would be added by
specialisation layers as additional `ChannelAdapter` subclasses.
"""

from albra_seed.communication.adapter import (
    ChannelAdapter,
    InboundSink,
    TextChannelAdapter,
)
from albra_seed.communication.message import Attachment, CommunicationMessage
from albra_seed.communication.registry import ChannelRegistry
from albra_seed.communication.router import (
    CommunicationRouter,
    RouteAuditSink,
    RouteResult,
)

__all__ = [
    # message
    "CommunicationMessage",
    "Attachment",
    # adapter
    "ChannelAdapter",
    "TextChannelAdapter",
    "InboundSink",
    # registry
    "ChannelRegistry",
    # router
    "CommunicationRouter",
    "RouteResult",
    "RouteAuditSink",
]

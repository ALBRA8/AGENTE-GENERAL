"""MCP subpackage — Model Context Protocol server/client/registry/validator.

The SEED ships only the protocol + an in-process server/client implementation
and a registry that records every call in an audit log. NO domain-specific
servers are included: specialisation layers register their own capabilities.
"""

from albra_seed.mcp.client import (
    MCPClient,
    InMemoryMCPClient,
    MCPInvocationError,
)
from albra_seed.mcp.protocol import (
    ERR_INTERNAL_ERROR,
    ERR_INVALID_PARAMS,
    ERR_INVALID_REQUEST,
    ERR_METHOD_NOT_FOUND,
    ERR_PARSE_ERROR,
    ERR_TIMEOUT,
    ERR_UNREACHABLE,
    MCPCapability,
    MCPRequest,
    MCPResponse,
)
from albra_seed.mcp.registry import AuditRecord, MCPRegistry
from albra_seed.mcp.server import CapabilityHandler, InMemoryMCPServer, MCPServer
from albra_seed.mcp.validator import MCPValidator, ValidationResult

__all__ = [
    # protocol
    "MCPCapability",
    "MCPRequest",
    "MCPResponse",
    "ERR_PARSE_ERROR",
    "ERR_INVALID_REQUEST",
    "ERR_METHOD_NOT_FOUND",
    "ERR_INVALID_PARAMS",
    "ERR_INTERNAL_ERROR",
    "ERR_TIMEOUT",
    "ERR_UNREACHABLE",
    # server
    "MCPServer",
    "InMemoryMCPServer",
    "CapabilityHandler",
    # client
    "MCPClient",
    "InMemoryMCPClient",
    "MCPInvocationError",
    # registry
    "MCPRegistry",
    "AuditRecord",
    # validator
    "MCPValidator",
    "ValidationResult",
]

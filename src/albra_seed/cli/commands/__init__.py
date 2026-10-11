"""Commands subpackage — one file per CLI command group.

Each module exposes:
    register(subparsers, ctx_factory) -> None

`ctx_factory` is a callable returning a `CLIContext` (built lazily on
first invocation so `--help` doesn't trigger runtime startup).
"""

from albra_seed.cli.commands.agent import register as register_agent
from albra_seed.cli.commands.audit import register as register_audit
from albra_seed.cli.commands.capabilities import register as register_capabilities
from albra_seed.cli.commands.config import register as register_config
from albra_seed.cli.commands.doctor import register as register_doctor
from albra_seed.cli.commands.execution import register as register_execution
from albra_seed.cli.commands.health import register as register_health
from albra_seed.cli.commands.memory import register as register_memory
from albra_seed.cli.commands.providers import register as register_providers
from albra_seed.cli.commands.skills import register as register_skills
from albra_seed.cli.commands.tools import register as register_tools

__all__ = [
    "register_agent",
    "register_audit",
    "register_capabilities",
    "register_config",
    "register_doctor",
    "register_execution",
    "register_health",
    "register_memory",
    "register_providers",
    "register_skills",
    "register_tools",
]

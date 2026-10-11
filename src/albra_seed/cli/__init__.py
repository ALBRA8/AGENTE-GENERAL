"""CLI subpackage — command-line interface for ALBRA SEED.

Built on `argparse` (stdlib only) so CORE_ONLY mode requires zero extra
dependencies. Each command group lives in `commands/<group>.py` and
exposes a `register(subparsers)` function.

NO domain commands — only universal introspection (health, doctor,
capabilities, providers, tools, skills, memory, execution, audit, config).
"""

from albra_seed.cli.cli import main


def __getattr__(name: str):  # pragma: no cover - re-export glue
    if name == "build_parser":
        from albra_seed.cli.cli import build_parser
        return build_parser
    raise AttributeError(name)


__all__ = ["main", "build_parser"]

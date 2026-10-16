#!/usr/bin/env python3
"""Portable, read-only preflight for a local ALBRA deployment.

This script reports environment readiness only. It does not start services,
read or print secret values, contact external providers, or modify configuration.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


def report(label: str, ok: bool, detail: str) -> bool:
    print(f"{'PASS' if ok else 'BLOCKED'}: {label} — {detail}")
    return ok


def main() -> int:
    hard_ok = True
    py_ok = sys.version_info >= (3, 10)
    hard_ok &= report(
        "Python version",
        py_ok,
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        + (" (>= 3.10 required)" if not py_ok else ""),
    )

    for module, label, required in (
        ("albra_seed", "ALBRA package", True),
        ("fastapi", "FastAPI API dependency", False),
        ("uvicorn", "Uvicorn server dependency", False),
    ):
        available = importlib.util.find_spec(module) is not None
        if required:
            hard_ok &= report(label, available, "installed" if available else "not installed in this Python")
        else:
            report(label, available, "installed" if available else "not installed; required only for HTTP API")

    # Report only presence/validity, never values, for security-related settings.
    token = os.environ.get("ALBRA_PROVIDER_SETTINGS_TOKEN", "")
    if token:
        report(
            "Dashboard settings token",
            len(token) >= 32,
            "configured with sufficient length" if len(token) >= 32 else "configured but shorter than 32 characters",
        )
    else:
        print("INFO: Dashboard settings token — not configured (needed only when enabling provider-settings endpoints)")

    encryption_key = os.environ.get("SETTINGS_ENCRYPTION_KEY", "")
    if encryption_key:
        print("INFO: Settings encryption key — configured (value not displayed)")
    else:
        print("INFO: Settings encryption key — not configured (required before storing provider credentials)")

    data_dir = Path(os.environ.get("ALBRA_DATA_DIR", "data")).expanduser()
    if data_dir.exists():
        writable = data_dir.is_dir() and os.access(data_dir, os.W_OK)
        report("Data directory", writable, "directory is writable" if writable else "not a writable directory")
    else:
        print(f"INFO: Data directory — {data_dir} does not exist yet; create it only after choosing a local data location")

    if hard_ok:
        print("RESULT: PREFLIGHT_BASE_PASS — this does not prove the service starts or is production-ready")
        return 0
    print("RESULT: PREFLIGHT_BLOCKED — resolve the required blockers above")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

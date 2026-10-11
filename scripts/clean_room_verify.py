#!/usr/bin/env python3
"""clean_room_verify.py — Clean-room verification of ALBRA AGENT SEED V1.0.

Implements spec sections 58 & 70. Runs the FULL clean-room verification
process, simulating a fresh clone (no prior state, no caches, no env
vars beyond PYTHONPATH) and exercising every layer end-to-end in
CORE_ONLY mode (no external credentials).

The script depends on the Python stdlib only — it bootstraps `sys.path`
to point at the project's `src/` directory so it is runnable from ANY cwd.

Each of the 16 required checks prints `[PASS]` or `[FAIL]` with details; the
script exits 1 if ANY required check fails. `CLEAN-ROOM: PASS` is
printed only when ALL required checks pass. Check 17 (A2A-CHECK) is
OPTIONAL (required=False): it probes the A2A translation layer
(AgentCard derivation from AgentManifest) but a SKIP there does NOT
fail the clean-room verification, because A2A is not needed for a
CORE_ONLY birth.

Usage:
    python scripts/clean_room_verify.py
"""

from __future__ import annotations

import asyncio
import importlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

# Bootstrap: make `albra_seed` importable from ANY cwd (clone-like).
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_PROVIDER_ENV_KEYS = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY",
    "ZAI_API_KEY", "OLLAMA_BASE_URL",
)
REQUIRED_DEPS = ("pydantic", "structlog", "dotenv")
OPTIONAL_DEPS = ("openai", "anthropic", "fastapi", "uvicorn", "httpx")


# --------------------------------------------------------------------------- #
# Reporting                                                                   #
# --------------------------------------------------------------------------- #


@dataclass
class CheckResult:
    name: str
    passed: bool
    required: bool = True
    details: str = ""

    def label(self) -> str:
        tag = "PASS" if self.passed else "FAIL"
        if not self.passed and not self.required:
            tag = "SKIP"
        return f"[{tag}]"


@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, r: CheckResult) -> None:
        self.results.append(r)
        opt = "" if r.required else " (optional)"
        print(f"  {r.label():<8} {r.name}{opt}")
        if r.details:
            for line in r.details.splitlines():
                print(f"           {line}")

    def all_required_passed(self) -> bool:
        return all(r.passed for r in self.results if r.required)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _sanitize_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Return env copy with ALBRA_* and provider keys removed (clean-room)."""
    env = dict(base if base is not None else os.environ)
    for k in list(env.keys()):
        if k.startswith("ALBRA_") or k in _PROVIDER_ENV_KEYS:
            env.pop(k, None)
    return env


def _can_import(name: str) -> tuple[bool, str]:
    try:
        mod = importlib.import_module(name)
        return True, f"v={getattr(mod, '__version__', '?')}"
    except ImportError as exc:
        return False, f"ImportError: {exc}"


def _subproc(code: str, env: dict[str, str] | None = None,
             cwd: str | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env=env if env is not None else os.environ,
        cwd=cwd, capture_output=True, text=True, timeout=120,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _run_async(coro: Awaitable[Any]) -> tuple[bool, str]:
    """Run a coroutine returning (ok, details). Captures any exception."""
    try:
        return asyncio.run(coro)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


async def _bootstrap_runtime() -> Any:
    """Build a fresh Runtime + ProviderRouter (CORE_ONLY)."""
    from albra_seed.core.config import SeedConfig
    from albra_seed.providers.mock import MockProvider
    from albra_seed.providers.registry import ProviderRegistry
    from albra_seed.providers.router import ProviderRouter
    from albra_seed.runtime.runtime import (
        COMPONENT_PROVIDER_ROUTER, Runtime,
    )
    rt = Runtime(SeedConfig.from_env())
    await rt.startup()
    reg = ProviderRegistry()
    reg.register(MockProvider())
    rt.register(COMPONENT_PROVIDER_ROUTER,
                ProviderRouter(registry=reg, default_provider="mock"))
    return rt


# --------------------------------------------------------------------------- #
# Checks                                                                      #
# --------------------------------------------------------------------------- #


def check_clone_like(rep: Report) -> None:
    """1. CLONE-LIKE: fresh interpreter, no env beyond PYTHONPATH."""
    env = {"PATH": os.environ.get("PATH", ""),
           "PYTHONPATH": str(_SRC),
           "HOME": os.environ.get("HOME", "")}
    rc, out, err = _subproc(
        "import albra_seed; print('OK', albra_seed.__seed_version__)",
        env=env, cwd=str(_PROJECT_ROOT),
    )
    rep.add(CheckResult(
        "1. CLONE-LIKE (fresh interpreter, sanitized env)",
        rc == 0 and "OK" in out,
        details=(out.strip() or err.strip())[:200],
    ))


def check_install_like(rep: Report) -> None:
    """2. INSTALL-LIKE: required deps must import; optional are reported."""
    req_lines, req_ok = [], True
    for n in REQUIRED_DEPS:
        ok, info = _can_import(n)
        req_lines.append(f"{n}: {'OK' if ok else 'MISSING'} ({info})")
        req_ok = req_ok and ok
    opt_lines = [f"{n}: {'OK' if _can_import(n)[0] else 'NOT_INSTALLED'}"
                 for n in OPTIONAL_DEPS]
    rep.add(CheckResult("2. INSTALL-LIKE (required deps)", req_ok,
                        details="\n".join(req_lines)))
    rep.add(CheckResult("2b. INSTALL-LIKE (optional deps)", True, required=False,
                        details="\n".join(opt_lines)))


def check_config_from_scratch(rep: Report) -> None:
    """3. CONFIG-FROM-SCRATCH: SeedConfig.from_env() with no env → CORE_ONLY."""
    code = ("from albra_seed.core.config import SeedConfig; "
            "c = SeedConfig.from_env(); "
            "print('CORE_ONLY', c.is_core_only())")
    rc, out, err = _subproc(code, env=_sanitize_env())
    rep.add(CheckResult(
        "3. CONFIG-FROM-SCRATCH (from_env → CORE_ONLY)",
        rc == 0 and "CORE_ONLY True" in out,
        details=(out.strip() or err.strip())[:200],
    ))


def check_init_from_scratch(rep: Report) -> None:
    """4. INIT-FROM-SCRATCH: Runtime.startup + readiness=True."""

    async def _r() -> tuple[bool, str]:
        rt = await _bootstrap_runtime()
        ready = rt.readiness()
        phase = rt.lifecycle.phase.value
        await rt.shutdown()
        return ready is True, f"phase={phase} readiness={ready}"

    ok, det = _run_async(_r())
    rep.add(CheckResult("4. INIT-FROM-SCRATCH (startup + readiness=True)",
                        ok, details=det))


def check_test_run(rep: Report) -> None:
    """5. TEST-RUN: invoke pytest and parse the summary line."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(_PROJECT_ROOT / "tests"),
         "--tb=no", "-q", "--no-header", "--color=no"],
        cwd=str(_PROJECT_ROOT),
        env=_sanitize_env({**os.environ, "PYTHONPATH": str(_SRC)}),
        capture_output=True, text=True, timeout=300,
    )
    summary, passed, failed = "", 0, 0
    for line in proc.stdout.splitlines()[::-1]:
        m = re.search(r"(\d+) passed(?:.*?(\d+) failed)?", line)
        if m:
            summary, passed = line.strip(), int(m.group(1))
            failed = int(m.group(2) or 0)
            break
    rep.add(CheckResult(
        f"5. TEST-RUN (pytest: {passed} passed, {failed} failed)",
        proc.returncode == 0 and passed > 0 and failed == 0,
        details=summary or proc.stderr.strip()[:200],
    ))


def check_build(rep: Report) -> None:
    """6. BUILD-CHECK: `import albra_seed` reports __seed_version__."""
    rc, out, err = _subproc(
        "import albra_seed; print(albra_seed.__seed_version__)",
        env=_sanitize_env({**os.environ, "PYTHONPATH": str(_SRC)}),
    )
    rep.add(CheckResult(
        "6. BUILD-CHECK (import albra_seed; __seed_version__)",
        rc == 0 and "ALBRA_SEED_V1.0" in out,
        details=(out.strip() or err.strip())[:200],
    ))


def check_startup(rep: Report) -> None:
    """7. STARTUP-CHECK: Agent.run('hello') returns a response."""

    async def _r() -> tuple[bool, str]:
        from albra_seed.core.identity import AgentIdentity
        from albra_seed.core.types import AutonomyLevel
        from albra_seed.runtime.agent import Agent
        rt = await _bootstrap_runtime()
        agent = Agent(AgentIdentity(
            name="clean-room-probe",
            autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
            providers=["mock"], permissions=["tool.echo"],
        ), rt)
        resp = await agent.run("hello")
        await rt.shutdown()
        return bool(resp.content), f"content={resp.content!r}"

    ok, det = _run_async(_r())
    rep.add(CheckResult("7. STARTUP-CHECK (Agent.run('hello'))", ok, details=det))


def check_health_readiness(rep: Report) -> None:
    """8 + 9. HEALTH + READINESS checks."""

    async def _r() -> tuple[bool, str, bool, str]:
        rt = await _bootstrap_runtime()
        h, rdy = rt.health(), rt.readiness()
        await rt.shutdown()
        return (isinstance(h, dict) and "status" in h,
                f"status={h.get('status')}",
                rdy is True, f"readiness={rdy}")

    h_ok, h_det, r_ok, r_det = _run_async(_r())
    rep.add(CheckResult("8. HEALTH-CHECK (runtime.health() has 'status')",
                        h_ok, details=h_det))
    rep.add(CheckResult("9. READINESS-CHECK (readiness()=True)",
                        r_ok, details=r_det))


def check_golden_flows(rep: Report) -> None:
    """10. GOLDEN-FLOWS-CHECK: run every golden-flow test via asyncio."""
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))
    try:
        from tests.golden import test_golden_flows as gf  # type: ignore
    except ImportError as exc:
        rep.add(CheckResult("10. GOLDEN-FLOWS-CHECK (import)", False,
                            details=f"ImportError: {exc}"))
        return
    fns = sorted(
        ((n, getattr(gf, n)) for n in dir(gf)
         if n.startswith("test_golden_") and callable(getattr(gf, n))),
        key=lambda t: t[0],
    )
    passed, failed = 0, []
    for name, fn in fns:
        try:
            asyncio.run(fn())
            passed += 1
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{name}: {type(exc).__name__}: {exc}")
    rep.add(CheckResult(
        f"10. GOLDEN-FLOWS-CHECK ({passed}/{len(fns)} passed)",
        not failed,
        details=("\n".join(failed) if failed else "all golden flows OK"),
    ))


def check_api(rep: Report) -> None:
    """11. API-CHECK: create_app + GET /api/v1/health (skip if no fastapi)."""
    if not _can_import("fastapi")[0]:
        rep.add(CheckResult(
            "11. API-CHECK (fastapi NOT_INSTALLED — skip)", True,
            required=False, details="fastapi is an optional dependency",
        ))
        return
    try:
        from fastapi.testclient import TestClient  # type: ignore
        from albra_seed.api.app import create_app
        rt = asyncio.run(_bootstrap_runtime())
        client = TestClient(create_app(rt))
        r = client.get("/api/v1/health")
        ok = r.status_code == 200 and "status" in (r.json() or {})
        rep.add(CheckResult(
            "11. API-CHECK (FastAPI create_app + GET /api/v1/health)",
            ok, details=f"status_code={r.status_code} body={r.text[:120]}",
        ))
    except Exception as exc:  # noqa: BLE001
        rep.add(CheckResult("11. API-CHECK (FastAPI create_app)", False,
                            details=f"{type(exc).__name__}: {exc}"))


def check_cli(rep: Report) -> None:
    """12. CLI-CHECK: `albra health` returns 0."""
    try:
        from albra_seed.cli.cli import main
        rc = main(["health"])
        rep.add(CheckResult("12. CLI-CHECK (albra health → exit 0)",
                            rc == 0, details=f"return_code={rc}"))
    except Exception as exc:  # noqa: BLE001
        rep.add(CheckResult("12. CLI-CHECK", False,
                            details=f"{type(exc).__name__}: {exc}"))


def check_memory(rep: Report) -> None:
    """13. MEMORY-CHECK: write+read via MemoryManager; verify isolation."""

    async def _r() -> tuple[bool, str]:
        from albra_seed.memory.inmemory_store import InMemoryMemoryStore
        from albra_seed.memory.manager import MemoryManager
        mgr = MemoryManager(store=InMemoryMemoryStore())
        a = await mgr.remember(agent_id="A", content="hello-A", user_id="u1")
        b = await mgr.remember(agent_id="B", content="hello-B", user_id="u2")
        ha = await mgr.recall(agent_id="A", user_id="u1")
        hb = await mgr.recall(agent_id="B", user_id="u2")
        leak = [r for r in ha if r.record_id == b.record_id]
        ok = (len(ha) == 1 and len(hb) == 1 and a.record_id in
              {r.record_id for r in ha} and not leak)
        return ok, f"A_recalled={len(ha)} B_recalled={len(hb)} leaks={len(leak)}"

    ok, det = _run_async(_r())
    rep.add(CheckResult("13. MEMORY-CHECK (write+read+isolation)", ok, details=det))


def check_audit(rep: Report) -> None:
    """14. AUDIT-CHECK: tamper-evident chain validates + detects tampering."""
    try:
        from albra_seed.audit.tamper_evident import TamperEvidentAuditLog
        log = TamperEvidentAuditLog()
        for i in range(3):
            log.append({"action": f"step-{i}", "actor": "probe"})
        before = log.verify_chain()
        # Mutate one entry's content in place.
        log.all()[1].action = "TAMPERED"
        after = log.verify_chain()
        ok = before.valid and before.chain_length == 3 and not after.valid
        rep.add(CheckResult(
            "14. AUDIT-CHECK (append + verify + tamper detected)", ok,
            details=(f"before.valid={before.valid} len={before.chain_length}; "
                     f"after.valid={after.valid} kind={after.kind}"),
        ))
    except Exception as exc:  # noqa: BLE001
        rep.add(CheckResult("14. AUDIT-CHECK", False,
                            details=f"{type(exc).__name__}: {exc}"))


def check_firewall(rep: Report) -> None:
    """15. FIREWALL-CHECK: default-deny blocks unknown action."""

    async def _r() -> tuple[bool, str]:
        from albra_seed.firewall.firewall import ActionFirewall
        v = await ActionFirewall().check("totally_unknown_action")
        return (v.allowed is False and v.decision.value == "deny"
                and v.reason == "no_matching_rule"), \
               f"allowed={v.allowed} decision={v.decision.value} reason={v.reason}"

    ok, det = _run_async(_r())
    rep.add(CheckResult("15. FIREWALL-CHECK (default-deny)", ok, details=det))


def check_doctor(rep: Report) -> None:
    """16. DOCTOR-CHECK: Doctor.diagnose returns DiagnosticReport."""

    async def _r() -> tuple[bool, str]:
        from albra_seed.doctor.doctor import Doctor
        from albra_seed.doctor.report import DiagnosticReport
        report = await Doctor().diagnose()
        if not isinstance(report, DiagnosticReport):
            return False, f"got {type(report).__name__}"
        names = {c.name for c in report.components}
        expected = {"runtime", "memory", "providers", "tools", "audit", "security"}
        missing = expected - names
        return (not missing, f"components={len(names)} missing={sorted(missing) or 'none'} "
                             f"overall={report.overall_status.value}")

    ok, det = _run_async(_r())
    rep.add(CheckResult("16. DOCTOR-CHECK (Doctor.diagnose → DiagnosticReport)",
                        ok, details=det))


def check_a2a(rep: Report) -> None:
    """17. A2A-CHECK (OPTIONAL): AgentCard derivable from AgentManifest.

    A2A is an OPTIONAL universal interoperability layer over Handoff. A
    CORE_ONLY birth does NOT require A2A — the InProcessTransport is
    always available but A2A itself is a translation layer that may be
    absent in specialised subsets of the seed. Hence ``required=False``:
    a SKIP here does NOT fail the clean-room verification.
    """
    try:
        from albra_seed.a2a import AgentCard, manifest_to_card
        from albra_seed.core.identity import AgentIdentity
    except ImportError as exc:
        rep.add(CheckResult(
            "17. A2A-CHECK (import albra_seed.a2a)", False,
            required=False,
            details=f"ImportError: {exc}",
        ))
        return

    try:
        identity = AgentIdentity(
            name="clean-room-a2a-probe",
            version="0.0.1",
            endpoint="inprocess://clean-room",
        )
        card: AgentCard = manifest_to_card(identity.to_manifest())
        ok = (
            getattr(card, "name", None) == "clean-room-a2a-probe"
            and getattr(card, "version", None) == "0.0.1"
            and getattr(card, "protocol_version", None) == "1.0"
        )
        rep.add(CheckResult(
            "17. A2A-CHECK (AgentCard derived from AgentManifest)",
            ok, required=False,
            details=(
                f"name={card.name!r} version={card.version!r} "
                f"protocol_version={card.protocol_version!r} "
                f"endpoint={card.endpoint!r}"
            ),
        ))
    except Exception as exc:  # noqa: BLE001
        rep.add(CheckResult(
            "17. A2A-CHECK (AgentCard derivation)", False,
            required=False,
            details=f"{type(exc).__name__}: {exc}",
        ))


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #


def main() -> int:
    print("=" * 72)
    print("ALBRA AGENT SEED V1.0 — CLEAN-ROOM VERIFICATION (spec §58 & §70)")
    print("=" * 72)
    print(f"project_root: {_PROJECT_ROOT}")
    print(f"src_path:     {_SRC}")
    print(f"python:       {sys.executable}")
    print("-" * 72)

    rep = Report()
    for fn in (check_clone_like, check_install_like, check_config_from_scratch,
               check_init_from_scratch, check_test_run, check_build,
               check_startup, check_health_readiness, check_golden_flows,
               check_api, check_cli, check_memory, check_audit,
               check_firewall, check_doctor, check_a2a):
        fn(rep)

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"{'#':<4}{'CHECK':<58}{'RESULT':<10}")
    print("-" * 72)
    for i, r in enumerate(rep.results, 1):
        tag = "PASS" if r.passed else ("SKIP" if not r.required else "FAIL")
        print(f"{i:<4}{r.name[:57]:<58}{tag:<10}")
    print("-" * 72)
    req_total = sum(1 for r in rep.results if r.required)
    req_pass = sum(1 for r in rep.results if r.required and r.passed)
    opt_total = sum(1 for r in rep.results if not r.required)
    opt_pass = sum(1 for r in rep.results if not r.required and r.passed)
    print(f"required: {req_pass}/{req_total} passed; "
          f"optional: {opt_pass}/{opt_total} passed")

    if rep.all_required_passed():
        print("\nCLEAN-ROOM: PASS")
        return 0
    print("\nCLEAN-ROOM: FAIL (one or more required checks failed)")
    return 1


if __name__ == "__main__":
    sys.exit(main())

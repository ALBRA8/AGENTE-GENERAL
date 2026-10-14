# ALBRA AGENT SEED V1.0

> **Universal DNA for the ALBRA agent ecosystem.**
>
> This repository is **NOT a specialized agent**. It is the **seed** — the transversal
> technical, operational, cognitive, security, memory, observability, execution and
> integration infrastructure that every future ALBRA agent (legal, sales, research,
> trading, marketing, ...) will inherit by cloning.

---

## ⚠️ What this is NOT

- ❌ Not a lawyer / legal agent
- ❌ Not a sales / CRM agent
- ❌ Not a research agent
- ❌ Not a trading agent
- ❌ Not a marketing / YouTube / KREA agent
- ❌ Not a SECOP / radar agent

The Seed contains **zero domain knowledge**. It only knows **HOW to be an ALBRA agent**:
how to think, execute, remember, observe, protect itself, integrate, delegate, audit,
recover, and receive specialization — but **NOT** what to do professionally.

---

## ✅ What this IS

A modular, clonable, specializable foundation providing **40 audit-matrix
probes** (33 original + 7 gap closures) backed by **106 universal DNA
entries** in `src/albra_seed/parity/dna.py` (plus **3 documented A2A
helper modules** `a2a.wire` / `a2a.exposition` / `a2a.observers` — NOT in
the DNA, see `docs/CERTIFICATION.md` § "A2A Helper Modules"). The
components are organized in 8 pillars plus an immutable core:

| Pillar | Components |
|---|---|
| **Technical Core** | `core`, `config`, `identity` |
| **Cognitive** | `reasoning`, `providers`, `context`, `contracts` |
| **Memory & Knowledge** | `memory` (MemoryDV), `provenance`, `evidence` |
| **Security** | `security`, `firewall`, `policy`, `autonomy`, `approvals` |
| **Execution** | `execution`, `workflows`, `jobs`, `scheduler`, `resilience` |
| **Observability** | `observability`, `audit` (normal + tamper-evident), `doctor`, `recovery` |
| **Integration** | `mcp`, `handoff`, `communication`, `multimodal`, `files` |
| **Extensibility** | `tools`, `capabilities`, `skills`, `plugins` |
| **Interfaces** | `api`, `cli` |

---

## 🚀 Quick Start

### Clean-room verified

```bash
git clone https://github.com/ALBRA8/AGENTE-GENERAL.git
cd AGENTE-GENERAL
pip install -e .[dev]            # or: pip install -e .  (CORE_ONLY)
python scripts/clean_room_verify.py     # 17/17 required + 2/2 optional PASS
python scripts/audit_matrix.py          # 40/40 VERIFIED
python scripts/parity_check.py          # exit 0, 106/106 DNA
python scripts/contamination_scan.py    # exit 0, is_clean=True
python -m pytest tests/ -q              # see docs/CERTIFICATION.md for current CI count
```

Expected last line of clean_room: `CLEAN-ROOM: PASS` (17/17 required + 2/2 optional).
The 17 required = 16 real PASS/FAIL gates + 1 informational scope-doc
check R (always passes; documents what the script does and does NOT
verify — see `docs/CLEANROOM.md` §10).

Optional CI workflow (does NOT make the seed depend on CI):

The workflow at `.github/workflows/clean-room.yml` runs the clean-room
verification on a fresh checkout + fresh virtualenv + multiple Python
versions (3.10, 3.11, 3.12). Trigger on push to `main` or PR.

### Durable approvals (optional SQLite)

Human-in-the-loop approvals remain in-memory by default. To enable durable
approval records and state recovery, configure a file-backed SQLite database
before constructing the runtime:

```bash
export ALBRA_APPROVAL_DB_PATH="./data/approvals.sqlite3"
```

```python
import asyncio
from albra_seed import Runtime, SeedConfig

async def main():
    runtime = Runtime(config=SeedConfig.from_env())
    await runtime.startup()
    try:
        approval_engine = runtime.get_component("approval_engine")
        # Use approval_engine in your HITL integration.
    finally:
        await runtime.shutdown()

asyncio.run(main())
```

If `ALBRA_APPROVAL_DB_PATH` is set, startup creates the SQLite-backed
`ApprovalEngine` unless the application registered its own approval engine.
If SQLite initialization fails, startup fails rather than silently falling back
to volatile approvals. Keep the database on a local filesystem accessible to all cooperating worker
processes on the same host; do not use separate database files per worker or
place a WAL-mode database on a network filesystem.

The SQLite dispatch ledger exposes atomic `prepare_dispatch`,
`claim_dispatch`, completion, and uncertain-outcome operations for dispatch
adapters. **Configuring SQLite does not automatically wrap arbitrary external
tool handlers.** A dispatcher must use the ledger before calling a provider,
pass the persisted `call_id` as the provider's idempotency key when supported,
and treat an interrupted/uncertain dispatch as requiring reconciliation rather
than automatic retry. SQLite alone cannot guarantee exactly-once effects in a
remote service.

### HITL production operations

Before deploying durable approvals, follow [the production operations runbook](docs/HITL_PRODUCTION_OPERATIONS.md). It defines encryption-at-rest and backup key separation, retention, restore drills, auth/RBAC/firewall checks, live acceptance testing, and reconciliation for uncertain external effects. File permissions alone are not encryption, and CI does not certify a real deployment.

A read-only deployment smoke test checks anonymous/invalid-token rejection and the approval-list permission boundary. Run it only against a staging/canary deployment with short-lived test credentials:

```bash
export ALBRA_HITL_BASE_URL="https://agent.example.internal"
export ALBRA_HITL_VALID_TOKEN="<short-lived staging token with approval:list>"
export ALBRA_HITL_NO_LIST_TOKEN="<optional valid token without approval:list>"
python scripts/hitl_acceptance_check.py
```

The script never prints tokens and does not create, decide, or resume approvals. It cannot verify encryption, backup restoration, infrastructure firewall rules, or provider side effects; the full runbook requires separate evidence for those controls.

### Minimal agent run (CORE_ONLY — no external credentials)

```python
import asyncio
from albra_seed import Agent, AgentIdentity, Runtime, SeedConfig

async def main():
    config = SeedConfig()                    # CORE_ONLY mode by default
    runtime = Runtime(config=config)
    await runtime.startup()

    identity = AgentIdentity(name="my-agent")
    agent = Agent(identity=identity, runtime=runtime)

    response = await agent.run("hello")
    print(response.content)

    await runtime.shutdown()

asyncio.run(main())
```

### CLI

```bash
albra health              # runtime health snapshot
albra doctor              # run diagnostics
albra capabilities        # list capabilities
albra tools               # list registered tools
albra config              # show config (SECRETS REDACTED)
albra --format json audit # recent audit entries as JSON
```

### REST API

```python
from albra_seed import create_app, Runtime, SeedConfig

runtime = Runtime(config=SeedConfig())
app = create_app(runtime)
# Run with uvicorn: uvicorn my_module:app
```

Endpoints:
- `GET /api/v1/health` — public health snapshot
- `GET /api/v1/readiness` — boolean readiness
- `GET /api/v1/agent/info` — agent manifest
- `GET /api/v1/agent/capabilities` — capability list
- `POST /api/v1/execution` — run an agent; requires a verified Bearer principal, explicit `execution:run` policy rule, RBAC grant, and configured security perimeter
- `GET /api/v1/execution/{id}` — execution trace; requires verified Bearer authentication and `execution:read` RBAC; traces are owner-scoped unless `execution:read:any` is explicitly granted
- `GET /internal/diagnostics` — Doctor report (internal)

---

## 🧬 Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full architecture map.

```
┌─────────────────────────────────────────────────────────────────┐
│                         ALBRA AGENT SEED                         │
├─────────────┬─────────────┬─────────────┬─────────────────────┤
│  COGNITIVE  │   MEMORY    │  SECURITY   │     EXECUTION       │
│  reasoning  │  MemoryDV   │  firewall   │  execution runner   │
│  providers  │  provenance │  policy     │  workflows           │
│  context    │  evidence   │  autonomy   │  jobs / scheduler    │
│  contracts  │             │  approvals  │  resilience          │
├─────────────┼─────────────┼─────────────┼─────────────────────┤
│  INTEGRATION│  EXTENSIB.  │   INTERFACES│   OBSERVABILITY     │
│  mcp        │  tools      │  api        │  audit (tamper-ev.)  │
│  handoff    │  skills     │  cli        │  doctor              │
│  multimodal │  plugins    │  config     │  recovery            │
│  files      │  capabilit. │             │  traces              │
│  comm. router│            │             │                     │
├─────────────┴─────────────┴─────────────┴─────────────────────┤
│                       CORE (immutable)                         │
│  types · identity · config · constants · base abstractions     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🔒 Core Principles

1. **NO DOMAIN KNOWLEDGE** — Seed knows HOW to be an agent, not WHAT to do.
2. **CORE_ONLY mode** — fully functional with zero external credentials (Mock provider).
3. **DEFAULT DENY** — every external action passes through ActionFirewall; no rule → deny.
4. **AUTONOMY ≠ PERMISSION** — high autonomy does not bypass permission checks.
5. **UNKNOWN never becomes FACT** — evidence type discipline enforced in code + tests.
6. **Tamper-evident audit** — separate from normal audit log; hash-chained.
7. **Immutable core** — runtime cannot modify its own DNA.
8. **Modular & clonable** — `git clone` + add specialization layer = new agent.
9. **Clean-room verified** — `scripts/clean_room_verify.py` runs 16 real PASS/FAIL checks + 1 informational scope-doc check (R) end-to-end. See `docs/CLEANROOM.md` §10 for the honest scope (what is and is NOT verified).
10. **Honest status reporting** — Doctor distinguishes IMPLEMENTED / MOCK / PLACEHOLDER / EXTERNAL_DEPENDENCY.

---

## 🧪 Testing

```bash
# Run all tests
pytest tests/

# Run only unit tests
pytest tests/unit/

# Run A2A tests (81 tests across 5 files: 28 main + 16 wire + 10 routes + 18 interop + 9 security perimeter)
pytest tests/a2a/

# Run golden flows (13 scenarios)
pytest tests/golden/

# Run genetic parity + contamination
pytest tests/genetic/ tests/contamination/

# Run clean-room verification
python scripts/clean_room_verify.py

# Generate audit matrix
python scripts/audit_matrix.py

# Run genetic parity check
python scripts/parity_check.py

# Run genetic contamination scan
python scripts/contamination_scan.py
```

**Test counts** (mirrored from [`docs/CERTIFICATION.md`](docs/CERTIFICATION.md)):

- 337 unit tests (incl. 8 birth/readiness + 11 factual integrity + 10 forget/audit BLOQ-MEM)
- 81 A2A tests (28 main + 16 wire + 10 routes + 18 external interop + 9 security perimeter BLOQ-A2A-SEC)
- 13 golden flow tests (end-to-end scenarios)
- 5 integration tests (multi-subsystem)
- 4 contract tests (interface compliance)
- 5 security tests (secrets, firewall, tamper detection)
- 17 genetic parity tests (106 DNA entries verified)
- 12 genetic contamination tests (249 files scanned, 0 critical)
- **Current test totals and CI evidence:** see [`docs/CERTIFICATION.md`](docs/CERTIFICATION.md); numbers in this historical example are not certification evidence.

---

## 🌱 Specialization Contract

To create a new specialized ALBRA agent:

```bash
git clone https://github.com/ALBRA8/AGENTE-GENERAL.git AGENTE-LEGAL
cd AGENTE-LEGAL
```

Then add ONLY specialization layers (see [`docs/SPECIALIZATION.md`](docs/SPECIALIZATION.md)):

```
src/albra_specialization/
├── identity/      # specialized AgentIdentity (name, domain, version)
├── knowledge/     # domain facts (with provenance + evidence)
├── skills/        # domain procedures (SkillContract-compliant)
├── tools/         # domain tools (ToolContract-compliant)
├── providers/     # domain-specific LLM/provider configs (optional)
├── workflows/     # domain workflows (WorkflowGraph-compliant)
├── policies/      # domain PolicyRuleSet
├── memory/        # domain memory domains
└── prompts/       # domain prompt templates
```

**You must NOT**:
- Modify any file under `src/albra_seed/`
- Reimplement runtime, memory, doctor, security, firewall, audit, evidence,
  provenance, MCP, handoff, observability, autonomy, resilience, workflow engine
- Hardcode external providers as required dependencies

---

## 📚 Documentation

- **[Certification Status](docs/CERTIFICATION.md)** — **canonical single source of truth** for all numbers and gate results.
- [Architecture](docs/ARCHITECTURE.md) — full architecture map
- [Extending Guide](docs/EXTENDING.md) — how to add tools/skills/providers/plugins
- [Specialization Contract](docs/SPECIALIZATION.md) — how to build a specialized agent
- [Audit Matrix](docs/AUDIT_MATRIX.md) — 40-component status matrix (auto-generated)
- [LIPO Report](docs/LIPO_REPORT.md) — KEEP / REFACTOR / MOVE / classification (40 components)
- [Universal DNA Gap Closure](docs/UNIVERSAL_DNA_GAP_CLOSURE.md) — 7-gap closure record
- [A2A Integration](docs/A2A_INTEGRATION.md) — A2A 1.0 adapter design (72 tests)
- [Birth Contract](docs/BIRTH_CONTRACT.md) — CORE_ONLY birth contract
- [Final Report](docs/FINAL_REPORT.md) — final implementation report
- [Security](docs/SECURITY.md) — firewall, policy, HITL, kill switch
- [Memory](docs/MEMORY.md) — MemoryDV, provenance, evidence
- [Clean-Room](docs/CLEANROOM.md) — verification protocol (16 real PASS/FAIL + 1 informational scope-doc + 2 optional; honest scope §10)

---

## 🛡️ Security Reminders

- **Never commit `.env`** — it's git-ignored by default.
- **Never log secrets** — `StructuredLogger` redacts known secret keys, but you should still be careful. Browser dashboards must not persist provider keys; see [`docs/WEB_DASHBOARD_SECURITY.md`](docs/WEB_DASHBOARD_SECURITY.md).
- **Default deny** — firewall blocks everything unless explicitly allowed.
- **Kill switch** — `KillSwitch.activate(reason)` blocks ALL external actions globally.
- **HITL** — high-risk actions require approval via `ApprovalEngine`.

---

## 📦 Dependencies

**Required (CORE_ONLY mode):**
- `pydantic>=2.5.0` — data models
- `python-dotenv>=1.0.0` — env loading
- `structlog>=23.2.0` — structured logging

**Optional (per feature):**
- `openai`, `anthropic`, `groq` — LLM providers
- `fastapi`, `uvicorn` — REST API
- `chromadb`, `sentence-transformers` — vector memory
- `opentelemetry-sdk` — OTLP tracing
- `hvac` — Vault secrets backend
- `Pillow`, `whisper`, `pypdf`, `python-docx` — multimodal/files

Install everything: `pip install -e .[all]`

---

## 📄 License

MIT — see [LICENSE](LICENSE).

---

## 🧭 Project Status

| Aspect                       | Status                                    |
| ---------------------------- | ----------------------------------------- |
| Seed version                 | `ALBRA_SEED_V1.0`                         |
| Runtime version              | `1.0.0`                                   |
| Audit-matrix probes          | **40** (33 original + 7 gap closures)    |
| Universal DNA entries         | **106** (`src/albra_seed/parity/dna.py`)   |
| A2A helper modules (NOT DNA)| **3** (`a2a.wire` / `a2a.exposition` / `a2a.observers`) |
| Allowed specializations       | 8                                         |
| Total tests                   | See [`docs/CERTIFICATION.md`](docs/CERTIFICATION.md) for current verified CI evidence |
| A2A tests                    | 81 (28 main + 16 wire + 10 routes + 18 interop + 9 security perimeter) |
| Clean-room                   | PASS (17/17 required + 2/2 optional; 16 real gates + 1 informational scope-doc R) |
| Genetic parity               | PASS (`is_pure_seed=True`, 106/106 DNA)   |
| Genetic contamination         | PASS (`is_clean=True`, 0 critical, 249 files) |
| A2A security perimeter        | CLOSED for ALL 3 inbound ops (message/send + tasks/get + tasks/cancel) — BLOQ-A2A-SEC |
| Memory FACT rule              | UNIFIED via `MemoryProvenance.validate_fact()` — BLOQ-MEM |
| Final status                 | **`CODE_GATES_PASS — PRODUCTION_NOT_VERIFIED`** |

> Canonical certification record: **[`docs/CERTIFICATION.md`](docs/CERTIFICATION.md)**.

---

**PRINCIPLE MAESTRO:**
> *NO CONSTRUYAS OTRO AGENTE. CONSTRUYE LA SEMILLA DE LA QUE PUEDAN NACER TODOS LOS AGENTES ALBRA.*

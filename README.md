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

A modular, clonable, specializable foundation providing 33 verified transversal
components organized in 8 pillars:

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
python scripts/clean_room_verify.py
```

Expected last line: `CLEAN-ROOM: PASS` (16/16 checks).

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
- `POST /api/v1/execution` — run an agent
- `GET /api/v1/execution/{id}` — execution trace
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
9. **Clean-room verified** — `scripts/clean_room_verify.py` runs 16 checks end-to-end.
10. **Honest status reporting** — Doctor distinguishes IMPLEMENTED / MOCK / PLACEHOLDER / EXTERNAL_DEPENDENCY.

---

## 🧪 Testing

```bash
# Run all tests
pytest tests/

# Run only unit tests
pytest tests/unit/

# Run golden flows (13 scenarios)
pytest tests/golden/

# Run clean-room verification
python scripts/clean_room_verify.py

# Generate audit matrix
python scripts/audit_matrix.py
```

**Test counts:**
- 253 unit tests (per module)
- 13 golden flow tests (end-to-end scenarios)
- 5 integration tests (multi-subsystem)
- 4 contract tests (interface compliance)
- 5 security tests (secrets, firewall, tamper detection)
- **Total: 280 tests passing in ~6s**

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

- [Architecture](docs/ARCHITECTURE.md) — full architecture map
- [Extending Guide](docs/EXTENDING.md) — how to add tools/skills/providers/plugins
- [Specialization Contract](docs/SPECIALIZATION.md) — how to build a specialized agent
- [Audit Matrix](docs/AUDIT_MATRIX.md) — component status matrix (auto-generated)
- [Security](docs/SECURITY.md) — firewall, policy, HITL, kill switch
- [Memory](docs/MEMORY.md) — MemoryDV, provenance, evidence
- [Clean-Room](docs/CLEANROOM.md) — verification protocol

---

## 🛡️ Security Reminders

- **Never commit `.env`** — it's git-ignored by default.
- **Never log secrets** — `StructuredLogger` redacts known secret keys, but you should still be careful.
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

| Aspect | Status |
|---|---|
| Seed version | `ALBRA_SEED_V1.0` |
| Runtime version | `1.0.0` |
| Components | 33 (all VERIFIED) |
| Unit tests | 253 |
| Golden flows | 13 |
| Integration tests | 5 |
| Contract tests | 4 |
| Security tests | 5 |
| Clean-room | PASS (16/16) |
| Final status | `INTEGRATION_READY` |

---

**PRINCIPLE MAESTRO:**
> *NO CONSTRUYAS OTRO AGENTE. CONSTRUYE LA SEMILLA DE LA QUE PUEDAN NACER TODOS LOS AGENTES ALBRA.*

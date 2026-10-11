# Specialization Contract — How to Build a Specialized ALBRA Agent

This document explains how to clone the Seed and add specialization WITHOUT
modifying the core DNA.

---

## 1. The Contract

A specialization is a **layer on top of the Seed**. It adds domain-specific
identity, knowledge, skills, tools, providers, workflows, policies and memory
domains — but it MUST NOT modify or reimplement any component of the Seed.

### 1.1 What a specialization MAY add

| Layer | Path | What |
|---|---|---|
| Identity | `src/albra_specialization/identity/` | Specialized `AgentIdentity` (name, version, specialization field) |
| Knowledge | `src/albra_specialization/knowledge/` | Domain facts (with provenance + evidence) |
| Skills | `src/albra_specialization/skills/` | Domain procedures (implementing `SkillContract`) |
| Tools | `src/albra_specialization/tools/` | Domain tools (implementing `ToolContract`) |
| Providers | `src/albra_specialization/providers/` | Domain-specific provider configs (optional, never required) |
| Workflows | `src/albra_specialization/workflows/` | Domain workflows (using `WorkflowGraph`) |
| Policies | `src/albra_specialization/policies/` | Domain `PolicyRuleSet` extensions |
| Memory | `src/albra_specialization/memory/` | Domain memory domains (isolated namespaces) |
| Prompts | `src/albra_specialization/prompts/` | Domain prompt templates |
| Interfaces | `src/albra_specialization/interfaces/` | Optional specialized API/CLI commands |

### 1.2 What a specialization MUST NOT do

- ❌ Modify any file under `src/albra_seed/`
- ❌ Reimplement runtime, memory, doctor, security, firewall, audit, evidence, provenance, MCP, handoff, observability, autonomy, resilience, or workflow engine
- ❌ Hardcode external providers as required dependencies
- ❌ Store secrets in code (use `SecretsManager`)
- ❌ Bypass the ActionFirewall or PolicyEngine
- ❌ Auto-promote UNKNOWN evidence to FACT
- ❌ Disable the tamper-evident audit log

---

## 2. Step-by-Step: Building AGENTE-LEGAL

### Step 1: Clone the seed

```bash
git clone https://github.com/ALBRA8/AGENTE-GENERAL.git AGENTE-LEGAL
cd AGENTE-LEGAL
git remote remove origin
git remote add origin https://github.com/ALBRA8/AGENTE-LEGAL.git
```

### Step 2: Create specialization package

```bash
mkdir -p src/albra_specialization/{identity,knowledge,skills,tools,workflows,policies,memory,prompts}
touch src/albra_specialization/__init__.py
```

### Step 3: Define specialized identity

`src/albra_specialization/identity/legal_identity.py`:

```python
from albra_seed import AgentIdentity, AutonomyLevel, RiskLevel

def build_legal_identity() -> AgentIdentity:
    return AgentIdentity(
        name="albra-legal-agent",
        version="1.0.0",
        specialization="legal",
        capabilities=["document_analysis", "citation_check"],
        permissions=["tool.doc_reader.execute", "skill.legal_brief_draft.execute"],
        autonomy_level=AutonomyLevel.L1_RECOMMEND,  # never execute autonomously
        risk_profile=RiskLevel.HIGH,
        providers=["openai", "mock"],  # mock as fallback
        tools=["doc_reader", "citation_finder"],
        skills=["legal_brief_draft", "contract_review"],
        memory_domains=["legal", "case_law"],
        constraints=["HITL_REQUIRED_FOR_FINAL_OUTPUT"],
    )
```

### Step 4: Register domain tools

`src/albra_specialization/tools/doc_reader.py`:

```python
from albra_seed import Tool, ToolContract, ToolResult, RiskLevel

class DocReaderTool(Tool):
    def __init__(self):
        self.contract = ToolContract(
            name="doc_reader",
            description="Reads a document and returns structured content",
            input_schema={"path": {"type": "string", "required": True}},
            output_schema={"content": {"type": "string"}},
            risk_level=RiskLevel.LOW,
            permissions=["tool.doc_reader.execute"],
            is_external=False,
        )

    async def execute(self, arguments, *, context=None, dry_run=False):
        # implementation here
        return ToolResult(call_id=..., success=True, output={"content": "..."})
```

### Step 5: Register domain skills

`src/albra_specialization/skills/legal_brief_draft.py`:

```python
from albra_seed import Skill

class LegalBriefDraftSkill(Skill):
    skill_id: str = "skill.legal_brief_draft"
    name: str = "legal_brief_draft"
    objective: str = "Draft a legal brief based on case facts and applicable law"
    inputs: dict = {"case_facts": "string", "jurisdiction": "string"}
    outputs: dict = {"brief": "string"}
    dependencies: list[str] = ["tool.doc_reader"]
    permissions: list[str] = ["skill.legal_brief_draft.execute"]
    risk_level: RiskLevel = RiskLevel.HIGH
    evidence_required: bool = True
    # ... implementation
```

### Step 6: Wire it all up

`src/albra_specialization/__init__.py`:

```python
from albra_seed import Runtime, SeedConfig
from albra_specialization.identity.legal_identity import build_legal_identity
from albra_specialization.tools.doc_reader import DocReaderTool
from albra_specialization.skills.legal_brief_draft import LegalBriefDraftSkill

async def build_legal_agent():
    config = SeedConfig.from_env()
    runtime = Runtime(config=config)
    await runtime.startup()

    # Register specialized components
    runtime.register_tool(DocReaderTool())
    runtime.register_skill(LegalBriefDraftSkill(...))

    identity = build_legal_identity()
    from albra_seed import Agent
    return Agent(identity=identity, runtime=runtime)
```

### Step 7: Verify clean-room still passes

```bash
python scripts/clean_room_verify.py
# Should still print: CLEAN-ROOM: PASS
```

### Step 8: Run the specialized agent

```python
import asyncio
from albra_specialization import build_legal_agent

async def main():
    agent = await build_legal_agent()
    response = await agent.run("Analyze this contract: ...")
    print(response.content)

asyncio.run(main())
```

---

## 3. Specialization Checklist

Before declaring a specialized agent ready:

- [ ] Specialization lives under `src/albra_specialization/` (NOT `src/albra_seed/`)
- [ ] No file under `src/albra_seed/` was modified
- [ ] All domain tools implement `ToolContract`
- [ ] All domain skills implement `SkillContract`
- [ ] All domain workflows use `WorkflowGraph`
- [ ] All domain policies extend `PolicyRuleSet`
- [ ] All factual knowledge has provenance + evidence
- [ ] No UNKNOWN evidence promoted to FACT
- [ ] No secrets hardcoded (use `SecretsManager`)
- [ ] External providers are OPTIONAL (CORE_ONLY still works)
- [ ] Agent runs end-to-end in CORE_ONLY mode (with Mock provider fallback)
- [ ] Clean-room verification still passes
- [ ] Audit log captures all domain actions
- [ ] HITL engaged for HIGH/CRITICAL risk actions
- [ ] Kill switch still works
- [ ] Doctor reports specialized components correctly

---

## 4. Domain Memory Isolation

Each domain MUST use isolated memory namespaces:

```python
# In specialization
memory.write(MemoryRecord(
    agent_id="albra-legal-agent",
    domain="legal",          # isolated from "sales", "research", etc.
    user_id=user_id,
    context_id=case_id,
    memory_type=MemoryType.FACTUAL,
    content={"jurisdiction": "CO", "rule": "..."},
    confidence=0.95,
    source="law_database",
    evidence_ids=["ev_xxx"],
))
```

The `MemoryIsolator` enforces composite key `(agent_id, domain, user_id, context_id)`.
A legal agent CANNOT read memory from a sales agent's domain.

---

## 5. Domain Policy Rules

Extend `PolicyRuleSet` with domain-specific rules:

```python
from albra_seed.policy import PolicyRule, PolicyRuleSet
from albra_seed import RiskLevel, PolicyDecision, AutonomyLevel

legal_policy_rules = PolicyRuleSet(rules=[
    PolicyRule(
        name="require_hitl_for_final_legal_opinion",
        action_pattern="skill.legal_opinion.execute",
        actor_pattern="*",
        risk_threshold=RiskLevel.HIGH,
        autonomy_required=AutonomyLevel.L4_EXECUTE_SENSITIVE,
        decision=PolicyDecision.REQUIRE_HITL,
        priority=100,
    ),
    # ... more rules
])
```

The `PolicyEngine` is composable: seed-level rules + domain rules are merged,
with domain rules taking priority for domain-specific actions.

---

## 6. Specialization Tests

Create `tests/specialization/` with:

- Identity tests (verify specialization metadata)
- Tool contract tests (every domain tool has valid `ToolContract`)
- Skill contract tests
- Workflow tests (domain workflows execute correctly)
- Integration tests (specialization + seed run end-to-end)
- Security tests (HITL triggers, firewall blocks, audit captures)
- Clean-room test (run `scripts/clean_room_verify.py` after specialization)

---

## 7. Specialization Distribution

A specialization is just a Python package. Distribute via:

- `pyproject.toml` with `dependencies = ["albra-seed>=1.0.0"]`
- Optional extras for domain-specific providers (e.g., `legal = ["langchain"]`)
- Plugins via `albra_seed.plugins.PluginManager` for hot-loadable extensions

---

## 8. Specialization Anti-Patterns

❌ **DON'T** copy `src/albra_seed/` into `src/albra_specialization/albra_seed/`
and modify files there. Import from `albra_seed` directly.

❌ **DON'T** reimplement the agent loop. Use `Agent.run()` and add hooks via
`runtime.add_phase_hook(fn)` if you need custom phase logic.

❌ **DON'T** bypass the firewall. If you need an action allowed, register a
`FirewallRule` for it.

❌ **DON'T** log secrets. Use `StructuredLogger` which redacts known secret keys.

❌ **DON'T** write factual memory without provenance + evidence. The
`MemoryManager.remember()` method enforces this automatically.

❌ **DON'T** make external providers required. CORE_ONLY mode must always work
via Mock provider fallback.

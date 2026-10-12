# ALBRA Ecosystem Conformity Matrix

> **Document ID:** `ECOSYSTEM_CONFORMITY.md`
> **Scope:** Conceptual comparison between `AGENTE-GENERAL` (the universal
> Seed) and the nine known ALBRA specialization repos enumerated in spec §1.
> **Important honesty caveat:** This matrix is derived from the **spec
> description** of each repo (its name + the domain keyword catalog in
> `src/albra_seed/contamination/domains.py::FORBIDDEN_DOMAIN_KEYWORDS`).
> The AGENTE-GENERAL Seed has **NOT inspected the source code** of the
> nine repos below; the matrix therefore reports the **expected shape** of
> each repo, not a verified implementation status. Where a field cannot
> be determined from the spec, the entry is `UNKNOWN` — not "yes" or "no".
> **Cross-reference:** [`docs/MASTER_DNA.md`](MASTER_DNA.md) is the
> authoritative contract; this matrix only maps the contract onto the
> ecosystem.

---

## 1. Why this matrix exists

The Seed's purpose (see `README.md`) is to be the **transversal DNA** that
every future ALBRA agent inherits by cloning. The `parity/dna.py` module
enumerates 106 `UNIVERSAL_DNA_COMPONENTS` that a specialization MUST
preserve, and 8 `ALLOWED_SPECIALIZATIONS` it MAY add. The
`contamination_scan.py` script enforces the inverse: no domain keyword may
appear inside `src/albra_seed/`.

This matrix answers a different question: **for each known ALBRA repo,
what is the expected fit against the Seed's contract**? It is a planning
artefact, not a certification. When a repo is actually inspected, the
"UNKNOWN" cells below should be replaced with verified findings and the
matrix entry should cite the inspection commit.

---

## 2. The nine known ALBRA repos

The spec §1 enumerates the following repos (the same names appear as
forbidden domain keywords in `src/albra_seed/contamination/domains.py`):

| # | Repo | Domain keyword(s) | Inferred domain |
|---|---|---|---|
| 1 | `AGENTE-LEADS` | `leads`, `lead_gen`, `sales_pipeline` | Sales / lead-generation agent |
| 2 | `CRM-ALBRA` | `crm`, `customer_relationship` | Customer-relationship-management agent |
| 3 | `NEX-SCOPE` | (no specific keyword; "scope" suggests research/scoping) | Research / intelligence-scoping agent (inferred) |
| 4 | `AGENTE-KREA` | `krea` | Creative / marketing-content agent |
| 5 | `AGENTE-CHISMOSO` | `chismoso` (Colombian Spanish: "gossip") | Social-listening / news-monitoring agent |
| 6 | `AGENTE-TRADING` | `trading`, `trader`, `financial_advice`, `investment_recommendation` | Trading / financial-markets agent |
| 7 | `RADAR-SECOP2` | `secop`, `secop2`, `radar_secop` | Colombian-government-procurement (SECOP II) radar agent |
| 8 | `YOUTUBE-AUTOMATION` | `youtube`, `yt_automation`, `marketing_campaign` | YouTube-channel automation agent |
| 9 | `DESPACHO-JURIDICO` | `legal`, `lawyer`, `abogado`, `abogada`, `juridico`, `juridica`, `contract_drafting` | Legal-desk / law-firm agent |

> "Inferred domain" for NEX-SCOPE is the only cell where the spec gives no
> explicit keyword. The matrix below treats its domain as
> `UNKNOWN` for keyword-mapped fields and `Research / scoping` for
> description fields. An actual inspection must replace this.

---

## 3. Conformity matrix

Legend:
- **Type** — Inferred agent type from the spec.
- **Could consume core?** — Whether the repo could in principle be built
  by cloning AGENTE-GENERAL + adding specialization. `YES` for all (the
  Seed's whole purpose is to be cloned); `UNKNOWN` only if the repo
  predates the Seed and is structured incompatible.
- **Independent impl?** — Whether the repo is known to be a standalone
  implementation (not built on the Seed). `UNKNOWN` unless the spec
  states it.
- **Specialization needed?** — Whether the repo needs the full
  `src/albra_specialization/` layer (identity + knowledge + skills +
  tools + workflows + policies + memory + prompts). `YES` for all
  domain-bearing repos.
- **Components reusable?** — Which Seed components the repo would reuse
  if it consumed the core (the list is non-exhaustive — every repo
  inherits the full 106 DNA modules; this column highlights the most
  relevant).
- **Compatibility risk** — `LOW` / `MEDIUM` / `HIGH` based on domain
  risk profile. HIGH risk domains (legal, trading) require HITL on more
  actions and tighter autonomy caps; this raises integration friction
  but is by design.
- **Migration path** — High-level steps to bring the repo into
  conformity with the Seed.

| Repo | Type | Could consume core? | Independent impl? | Specialization needed? | Components reusable? | Compatibility risk | Migration path |
|---|---|---|---|---|---|---|---|
| **AGENTE-LEADS** | Sales / lead-gen | YES | UNKNOWN | YES (knowledge + tools + workflows) | RBAC, firewall, memory (isolated `leads` domain), workflows (lead-nurture DAG), HITL for outbound contact, audit | MEDIUM (outbound actions to humans; PII) | Move domain skills/tools to `src/albra_specialization/`; register `leads` memory domain; add PolicyRule `require_hitl_for_outbound_contact`; verify contamination_scan is clean. |
| **CRM-ALBRA** | CRM | YES | UNKNOWN | YES (knowledge + tools + workflows + memory) | RBAC, firewall, memory (isolated `crm` domain), provenance (customer-record lineage), evidence (verified CRM facts only), audit, HITL for delete/export | HIGH (customer PII, GDPR-style deletion) | Move domain code to `src/albra_specialization/`; enforce FACT gate on customer records; wire `MemoryManager.forget()` to right-to-erasure; add audit-only policy for destructive ops. |
| **NEX-SCOPE** | Research / scoping (inferred) | YES | UNKNOWN | YES (knowledge + tools) | RBAC, memory (isolated `research` domain), provenance, evidence (cite sources), reasoning, MCP (external data tools) | LOW–MEDIUM (mostly read-only) | Move domain code; register `research` memory domain; add `evidence_required=True` on every factual claim; verify unknown sources stay `EvidenceType.UNKNOWN`. |
| **AGENTE-KREA** | Creative / marketing content | YES | UNKNOWN | YES (knowledge + tools + prompts) | RBAC, memory (isolated `krea` domain), multimodal (image/text), reasoning, providers (optional external LLMs) | MEDIUM (IP / brand voice; potential copyright) | Move domain prompts/tools to specialization; register `krea` memory domain; add PolicyRule `require_hitl_for_published_content`; keep CORE_ONLY mode working (Mock fallback). |
| **AGENTE-CHISMOSO** | Social listening / news monitoring | YES | UNKNOWN | YES (knowledge + tools + scheduler) | RBAC, memory (isolated `chismoso` domain), scheduler (interval triggers), provenance (source attribution), evidence (verify before storing as FACT), audit | MEDIUM (defamation risk; unverified claims) | Move domain code; register scheduler interval triggers; enforce `EvidenceType.INFERENCE` ceiling for unverified social posts; HITL on publish. |
| **AGENTE-TRADING** | Trading / financial markets | YES | UNKNOWN | YES (knowledge + tools + workflows + policies) | RBAC, firewall (rate-limit + cooldown on exchange API), policy (L4_EXECUTE_SENSITIVE floor), autonomy (cap at L3 max), HITL for order placement, evidence (trade records), audit (tamper-evident), kill switch | HIGH (financial loss; regulator scrutiny) | Move domain code; cap autonomy at L3_EXECUTE_EXTERNAL; add PolicyRule `require_hitl_for_order_placement`; enable kill switch on exchange API; FACT gate on market data with `source_type=external`. |
| **RADAR-SECOP2** | Colombian SECOP II procurement radar | YES | UNKNOWN | YES (knowledge + tools + scheduler) | RBAC, memory (isolated `secop` domain), scheduler (cron), provenance, evidence (verified `source_type=external` for SECOP data), MCP (SECOP API as data tool), audit | MEDIUM (public-source data; low PII) | Move domain code; register `secop` memory domain; add SECOP API as MCP data tool with `evidence_required=True`; verify `EvidenceType.FACT` only for verified SECOP records. |
| **YOUTUBE-AUTOMATION** | YouTube channel automation | YES | UNKNOWN | YES (knowledge + tools + workflows + scheduler) | RBAC, memory (isolated `youtube` domain), multimodal (video metadata), scheduler (publish triggers), workflows (publish DAG with HITL INTERRUPT), audit, kill switch | MEDIUM (platform ToS; copyright; brand voice) | Move domain code; register `youtube` memory domain; add `WorkflowGraph.INTERRUPT` before publish; HITL on every public-publish action; kill switch on YouTube API. |
| **DESPACHO-JURIDICO** | Legal-desk / law firm | YES | UNKNOWN | YES (knowledge + skills + tools + workflows + policies + prompts) | RBAC (lawyer / paralegal / client roles), firewall, policy (L1_RECOMMEND default — never execute autonomously), HITL on every legal-opinion output, evidence (cite case law + statutes), provenance (document lineage), tamper-evident audit, memory (isolated `legal` + `case_law` domains) | HIGH (legal liability; attorney-client privilege) | Move domain code; cap autonomy at L1_RECOMMEND; add PolicyRule `require_hitl_for_final_legal_opinion`; FACT gate on case-law citations; tamper-evident audit for privilege log; secret redaction on client PII. |

### 3.1 Reading the matrix

- **"Could consume core?" is YES for all nine repos.** That is the Seed's
  design intent: every ALBRA agent is born by cloning. The interesting
  column is the next one.
- **"Independent impl?" is UNKNOWN for all nine.** The spec does not say
  whether these repos were built before the Seed existed (in which case
  they may have their own runtime / memory / security implementations
  that would need to be migrated) or after (in which case they may
  already consume the Seed directly). This MUST be verified by
  inspection before any migration claim is made.
- **"Compatibility risk" is highest where the domain has regulatory or
  financial exposure** (`DESPACHO-JURIDICO`, `AGENTE-TRADING`,
  `CRM-ALBRA`). The Seed's HITL + autonomy cap + tamper-evident audit
  are designed exactly for these domains — but a specialization author
  must explicitly wire the PolicyRules and AutonomyProfile; the Seed
  does not infer them from the domain keyword.
- **The "Migration path" column is a sketch, not a checklist.** Every
  migration MUST end with the six-step verification protocol in
  [`docs/MASTER_DNA.md` §C.3](MASTER_DNA.md#c3-specialization-verification-protocol).

---

## 4. Conceptual comparison: AGENTE-GENERAL DNA vs DESPACHO-JURIDICO

This section answers a common question: *what does it mean for
DESPACHO-JURIDICO (the legal desk) to "conform" to the AGENTE-GENERAL
DNA*? The two repos have different *purposes* but must share the same
*contract*. The comparison below is **conceptual**, not numeric — it
does NOT equate test counts or DNA module counts between the two repos.

### 4.1 Purpose

| | AGENTE-GENERAL (Seed) | DESPACHO-JURIDICO (specialization) |
|---|---|---|
| **What it is** | Universal DNA — transversal infrastructure. | A specialized legal-desk agent. |
| **Domain knowledge** | Zero. Carries only HOW to be an agent. | Full legal domain: case law, statutes, jurisdictions, contract templates. |
| **Who clones whom** | Is the source repo. | Clones the Seed; adds `src/albra_specialization/`. |
| **Tested by** | `tests/` (732 tests) + clean-room + parity + contamination. | The Seed's tests + `tests/specialization/` (its own legal-domain tests). |

### 4.2 Shared contract (what MUST be identical)

These are the Section-A guarantees from `MASTER_DNA.md`. DESPACHO-JURIDICO
inherits them unchanged:

| Guarantee | AGENTE-GENERAL enforcement | DESPACHO-JURIDICO inheritance |
|---|---|---|
| DEFAULT-DENY | `ActionFirewall` returns DENY when no rule matches. | Same firewall; adds a domain PolicyRule `require_hitl_for_final_legal_opinion` (priority 100, risk_threshold HIGH). |
| Fail-closed | try/except wraps every gate. | Same wrapping; cannot be unwound by specialization. |
| RBAC | `PermissionRegistry` (Role → Permission → Capability). | Adds roles `lawyer`, `paralegal`, `client`; maps them to permissions `tool.doc_reader.execute`, `skill.legal_brief_draft.execute`, etc. |
| HITL | `ApprovalEngine` pauses workflow on REQUIRE_HITL. | Default autonomy `L1_RECOMMEND` — every output requires human sign-off before delivery. |
| Evidence discipline | FACT requires `confidence ≥ 0.9` + verified `source_type`. | Case-law citations MUST be FACT with `source_type=external` (verified legal DB); otherwise INFERENCE. |
| Tamper-evident audit | `TamperEvidentAuditLog` (hash-chained). | Same log; used for the privilege log (attorney-client communications). |
| Immutable core | `parity/dna.py::UNIVERSAL_DNA_COMPONENTS` (106). | Same 106 modules MUST be importable; `parity_check.py` reports `is_pure_seed=True`. |
| Contamination | `contamination_scan.py` rejects domain keywords in `src/albra_seed/`. | DESPACHO-JURIDICO's legal code lives in `src/albra_specialization/`; `src/albra_seed/` remains clean. |
| Secret redaction | `StructuredLogger` + `MemorySanitizer` (synced `_SECRET_KEY_SUBSTRINGS`). | Same redaction; applies to client PII (names, IDs, case numbers). |
| Learning HITL gate | `LearningProposal` starts PROPOSED; `apply` raises if not APPROVED. | Legal learnings (e.g. "this jurisdiction rejected this argument") require lawyer approval before being added to the `legal` memory domain. |

### 4.3 What DESPACHO-JURIDICO adds (the specialization layer)

| Layer | Example |
|---|---|
| **Identity** | `AgentIdentity(name="albra-legal-agent", specialization="legal", autonomy_level=L1_RECOMMEND, risk_profile=HIGH)`. |
| **Knowledge** | Case-law database (with provenance: court, date, citation; evidence: FACT after verification). Statutes (FACT after verified source). Contract templates (PROCEDURAL memory). |
| **Skills** | `legal_brief_draft`, `contract_review`, `citation_check` — each implements `SkillContract` with `evidence_required=True`. |
| **Tools** | `doc_reader`, `citation_finder`, `case_law_search` — each implements `ToolContract` with `risk_level=LOW/MEDIUM`, `is_external=False/True`. |
| **Workflows** | `WorkflowGraph` for case intake → research → draft → review → deliver, with `INTERRUPT` nodes at every review gate. |
| **Policies** | `PolicyRule(name="require_hitl_for_final_legal_opinion", action_pattern="skill.legal_opinion.execute", decision=REQUIRE_HITL, priority=100)`. |
| **Memory domains** | `legal` and `case_law` — isolated from every other ALBRA agent's memory via `MemoryIsolator`. |
| **Prompts** | Domain-specific prompt templates for legal reasoning, citation enforcement, jurisdiction scoping. |

### 4.4 What DESPACHO-JURIDICO MUST NOT do

The Section-C.2 prohibitions in `MASTER_DNA.md` apply in full. The most
legally-relevant ones:

- ❌ **Cannot bypass HITL on legal opinions.** A "trusted lawyer" flag
  does not exist in the public API; if added by the specialization, it
  would be flagged by `contamination_scan.py` and `parity_check.py`.
- ❌ **Cannot auto-promote an inference to a FACT.** A model-generated
  legal claim stays `EvidenceType.INFERENCE` unless a verified legal
  database (with `source_type=external`) confirms it.
- ❌ **Cannot access another client's memory.** `MemoryIsolator`'s
  composite key `(agent_id, domain="legal", user_id, context_id=case_id)`
  is enforced in code; a specialization cannot relax it.
- ❌ **Cannot modify the immutable core.** Even a "small fix" to
  `src/albra_seed/policy/engine.py` would be reported as
  `is_pure_seed=False` by `parity_check.py`.
- ❌ **Cannot auto-apply legal learnings.** A proposal like "this
  jurisdiction tends to reject argument X" stays in `PROPOSED` until a
  lawyer reviews it; `LearningManager.apply()` raises otherwise.

### 4.5 Conceptual equivalence, not numerical equivalence

The two repos are **contract-equivalent**, not **size-equivalent**:

- The Seed's 732 tests measure the *universal* surface; DESPACHO-JURIDICO
  is conformant when those same 732 tests pass on its working tree PLUS
  its own `tests/specialization/` pass.
- The Seed's 106 `UNIVERSAL_DNA_COMPONENTS` are a *floor*, not a ceiling.
  DESPACHO-JURIDICO adds modules under `specialization.*` (the 8
  `ALLOWED_SPECIALIZATIONS`); it cannot add modules under any other
  namespace without `parity_check.py` reporting impurity.
- The Seed's `EvidenceType.FACT` gate is a *universal rule*; the
  *specific* evidence sources (case-law DB, statute DB) are domain
  knowledge that lives in `src/albra_specialization/`.

In short: **AGENTE-GENERAL and DESPACHO-JURIDICO share the same contract
(Section A of `MASTER_DNA.md`); they differ in the specialization layer
(Section C).** Conformity is verified by running the Seed's own
verification scripts against DESPACHO-JURIDICO's working tree.

---

## 5. Cross-repo integration patterns

When two specialized ALBRA agents need to talk to each other, three
patterns are available in the universal DNA:

| Pattern | When | Module | Test |
|---|---|---|---|
| **Handoff** (internal ALBRA delegation) | Same trust boundary, both agents are ALBRA seeds. | `handoff/manager.py`, `handoff/contract.py`. | `tests/unit/test_handoff.py`. |
| **A2A** (external agent interoperability, A2A 1.0 wire format) | Cross-vendor / cross-trust-boundary. | `a2a/*` (8 universal modules + 3 helper modules). | `tests/a2a/*` (81 tests). |
| **MCP** (tool / resource / data exchange) | One agent exposes a tool another agent calls. | `mcp/server.py`, `mcp/client.py`. | `tests/unit/test_mcp.py`. |

For the nine-repo ecosystem, this means:

- `DESPACHO-JURIDICO` + `CRM-ALBRA` could integrate via Handoff
  (law-firm agent delegates client-intake to CRM agent).
- `AGENTE-TRADING` + an external non-ALBRA broker could integrate via A2A
  (A2A 1.0 wire format; security perimeter closed for all 3 inbound ops:
  `message/send`, `tasks/get`, `tasks/cancel`).
- `RADAR-SECOP2` + `AGENTE-LEADS` could integrate via MCP (SECOP radar
  exposes a `search_secop` tool that the leads agent consumes).

In all three cases, the **security guarantees in `MASTER_DNA.md` §A**
travel with the request: kill switch, RBAC, firewall, policy, HITL, and
tamper-evident audit apply on both ends. A specialization cannot relax
them on its end without breaking `parity_check.py`.

---

## 6. Open questions (honestly acknowledged)

This matrix cannot answer the following without inspecting each repo:

1. **Were the nine repos built before or after the Seed was frozen?** If
   before, they may have independent runtime / memory / security
   implementations that need migration. If after, they may already
   consume the Seed directly.
2. **Does each repo's specialization layer live under
   `src/albra_specialization/`?** The Seed's parity checker expects
   exactly that path. A repo using a different convention (e.g.
   `src/legal/`, `src/crm/`) would report impure.
3. **Does each repo's test suite include the Seed's 732 tests plus its
   own?** The Seed does not prescribe a specialization's test count; it
   only prescribes that the universal tests pass.
4. **For DESPACHO-JURIDICO specifically: are legal-domain FACTs sourced
   from a verified legal database with `source_type=external`?** The
   Seed enforces the gate; the specialization must source the evidence.
5. **For AGENTE-TRADING specifically: is the autonomy cap actually
   wired to L3 max, or does the specialization rely on a higher cap?**
   The Seed provides the matrix; the specialization must populate it.

These questions are the input to the next audit round. They are NOT
blockers for the Seed's own certification (the Seed is certified in
`docs/CERTIFICATION.md`); they are blockers for any claim that a given
repo *conforms* to the Seed.

---

## 7. Authority & cross-references

- **Canonical contract:** [`docs/MASTER_DNA.md`](MASTER_DNA.md).
- **Canonical numbers:** [`docs/CERTIFICATION.md`](CERTIFICATION.md).
- **Specialization step-by-step:** [`docs/SPECIALIZATION.md`](SPECIALIZATION.md).
- **Architecture map:** [`docs/ARCHITECTURE.md`](ARCHITECTURE.md).
- **Security model:** [`docs/SECURITY.md`](SECURITY.md).
- **Universal DNA catalog (code):** `src/albra_seed/parity/dna.py`
  (`UNIVERSAL_DNA_COMPONENTS`, `ALLOWED_SPECIALIZATIONS`).
- **Forbidden domain keywords (code):** `src/albra_seed/contamination/domains.py`
  (`FORBIDDEN_DOMAIN_KEYWORDS`, `FORBIDDEN_CODE_PATTERNS`).

---

**PRINCIPLE MAESTRO:**
> *La conformidad no se declara; se verifica. Esta matriz es un mapa
> conceptual — la conformidad real la dicta `parity_check.py` y
> `contamination_scan.py` ejecutados contra el árbol de trabajo del
> repositorio especializado.*

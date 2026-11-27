# CRM Dispatch Bridge (`/crm/dispatch`) — hosted integration (S4.4)

Versioned bridge of **CRM WhatsPower** into AGENTE-GENERAL: implements
operation 1 of the platform contract
(`CONTEXTO CRM/06_INTEGRATIONS/CONTRATOS_INTEGRACION.md` §4, mirrored in
`packages/contracts/src/dispatch.ts`). Lives in the SPECIALIZATION layer so
the universal seed stays domain-neutral (contamination invariant enforced by
`tests/contamination`).

```
CRM (Vestro)                                            AGENTE-GENERAL
  services/agent-gateway
    POST {AGENT_DISPATCH_URL}/crm/dispatch  ──HMAC──▶  albra_specialization/crm/bridge.py
    Authorization: Bearer AGENT_API_KEY                  1. config fail-closed (503)
    X-Signature: HMAC-SHA256(body+ts)                    2. bearer (401)
    X-Timestamp: unix (±300 s)                           3. HMAC timing-safe (401)
    X-Org-Id: org_<id>                                   4. contract validation (422 envelope)
    X-Request-Id: uuid                                   5. tenant identity echo + D-014 (403 tenant_mismatch)
    body: conversation.turn.received                     6. per-org rate limit (429 rate_limited)
                                                         7. RBAC crm:dispatch (403 unauthorized_scope)
                                                         8. ActionFirewall verdict
                                                            (403 firewall_denied / no_matching_rule / 202 approval_required)
                                                         9. idempotency ledger (org, conversation, event_id)
                                                        10. tenant memory write (org bucket, domain crm)
                                                        11. ACK 200 {accepted, event_id, approvalRequired:false}
```

The agent NEVER answers through this HTTP response — it answers through the
CRM bot API (`POST /api/bot/messages`), as the contract mandates. Errors use
the FROZEN catalog envelope `{"error": {code, message, details?}}` (D-013).

## Files

| File | Role |
|---|---|
| `src/albra_specialization/crm/bridge.py` | Router builder + wire models + HMAC + tenant checks + idempotency ledger + org rate limiter + `install_crm_dispatch(app, ctx)` |
| `src/albra_seed/api/app.py` | Generic `extra_extensions` hook — the seed never imports the specialization |
| `render_app.py` | Dev entry point: installs the bridge, grants `crm:dispatch` to the reviewer role, registers the org-scoped memory store |
| `tests/integration/test_crm_dispatch.py` | 25 integration tests against the real ASGI app |
| `tests/unit/test_memory_org_isolation.py` | 15 T9 unit tests (cross-org negatives, legacy migration) |

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `ALBRA_CRM_HMAC_SECRET` | Shared HMAC secret with the CRM. **Required** — without it the endpoint answers 503 and never processes anything (fail-closed). Use a strong secret in production; the dev token `albra-dev-token` is dev-only and never works when `APP_ENV=production` / `RENDER` / `DYNO`. | — |
| `ALBRA_MEMORY_DB` | SQLite path for the org-scoped memory store (`:memory:` keeps dev runs hermetic). | `:memory:` |
| `ALBRA_CRM_DISPATCH_RATE_PER_MINUTE` | Per-organization fixed-window dispatch limit. | `240` |
| `ALBRA_DEV_TOKEN` | Dev bearer token (local development only). | `albra-dev-token` |

`render_app.py` grants `crm:dispatch` to the `reviewer` role and adds the
matching policy rule — without an explicit grant the endpoint answers
403 `unauthorized_scope` (default-deny).

## Tenant memory (T9, S4.4)

Every memory operation carries `org_id` as the FIRST component of the
isolation key `(org_id, agent_id, domain, user_id, context_id)`:

* identity validation on EVERY operation — an `agent_id` with the D-014
  prefix `org:<X>` rejects a mismatching `org_id` with `PermissionError`;
* cross-org recall returns **0 rows**; cross-org read/delete by record id
  raises `PermissionError`;
* retrieval (`MemoryRetriever`), agent-loop writes (`Session.org_id` →
  `RunContext.org_id`) and working-memory cleanup are org-scoped;
* legacy databases migrate additively (`ALTER TABLE ADD COLUMN org_id`);
  pre-S4.4 records stay in the legacy `""` bucket, never mixing with org
  buckets — no data loss, full backward compatibility.

The CRM bridge persists each turn with `domain="crm"` and the organization of
the signed event, BEFORE acknowledging.

## Running the real cross-repo integration test

From a checkout of CRM WhatsPower (sibling of this repo or via
`AGENTE_GENERAL_DIR`):

```bash
# inside CRM WhatsPower
pnpm --filter @vestro/agent-gateway test
```

`cerebro-real.test.ts` spawns the REAL uvicorn server of this repo
(`render_app.py`, `ALBRA_CRM_HMAC_SECRET` shared with the adapter) and
verifies: health with memory configured, a real dispatch ACK (200),
idempotent replay, tampered HMAC → 401 typed, cross-org identity →
403 `tenant_mismatch`, and the HITL pending-approvals queue (200).

## Production checklist (do not skip)

1. Strong `ALBRA_CRM_HMAC_SECRET` shared with the CRM adapter.
2. Persistent `ALBRA_MEMORY_DB` (file path on a durable volume).
3. A REAL authenticator registered on the runtime — the dev authenticator is
   never registered in production (every authenticated endpoint returns 503
   instead: safe-by-default).
4. Firewall/policy with explicit default-deny rules — `render_app.py` ships a
   demo allow-all `*` firewall for local development only.

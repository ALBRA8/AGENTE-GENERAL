# Deployment Checklist

**Status:** procedure documented. NOT YET executed against a staging
environment — staging host is not provisioned.

**Scope:** step-by-step deployment of ALBRA SEED to a staging host.
Covers installation, configuration, startup, health verification, and
rollback.

**NOT in scope:** application code, tests, CI workflows, HITL engine
implementation (owned by the other engineer).

---

## 1. Prerequisites

### 1.1 Infrastructure (must be provisioned BEFORE starting)

| Item | Requirement | Verified? |
|---|---|---|
| Host OS | Linux x86_64 or arm64, kernel 5.x+ | ☐ |
| Python | 3.10, 3.11, or 3.12 installed | ☐ |
| Encrypted persistent volume | Mounted at `/var/lib/albra/` (or equivalent) | ☐ |
| Backup target | Object storage or independent volume reachable | ☐ |
| KMS / envelope-encryption key | Provisioned; key ID recorded (NOT key material) | ☐ |
| TLS-terminating reverse proxy | nginx/Caddy/ALB configured | ☐ |
| Egress firewall | Allowlist for model providers + IdP + telemetry | ☐ |
| Log aggregation | JSON logs shipped to aggregator | ☐ |
| Service account | `albra` user with least privilege; owns `/var/lib/albra/` | ☐ |

### 1.2 Secrets (must be in secret manager, NOT in code/env files)

| Secret | Purpose | Rotation |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI provider (optional) | per provider policy |
| `ANTHROPIC_API_KEY` | Anthropic provider (optional) | per provider policy |
| `GROQ_API_KEY` | Groq provider (optional) | per provider policy |
| `ZAI_API_KEY` | Z.AI provider (optional) | per provider policy |
| `OLLAMA_BASE_URL` | Ollama provider (optional) | n/a |
| Authenticator signing key | Token verification | quarterly |
| Backup encryption key | Envelope encryption for backups | annually + after incident |
| IdP client secret | Token issuance | per IdP policy |

### 1.3 Code

| Item | Requirement | Verified? |
|---|---|---|
| Commit SHA | Recorded (e.g. `24ab004c7a42ed842dc09133f959a7c505be5da7`) | ☐ |
| Branch | `main` or release branch | ☐ |
| CI status | Green on the chosen commit | ☐ |

---

## 2. Installation

### 2.1 Clone the repository

```bash
# As the albra service user
cd /opt
git clone https://github.com/ALBRA8/AGENTE-GENERAL.git albra
cd albra
git checkout <COMMIT_SHA>
```

**Expected:** working tree clean; `git log --oneline -1` shows the
chosen commit.

### 2.2 Create a virtualenv

```bash
python3 -m venv /opt/albra/.venv
source /opt/albra/.venv/bin/activate
```

### 2.3 Install the package with required extras

```bash
# Minimum for staging (FastAPI + A2A):
pip install -e '.[dev,a2a,api]'

# If using real LLM providers, add the corresponding extras:
# pip install -e '.[dev,a2a,api,openai]'        # OpenAI
# pip install -e '.[dev,a2a,api,anthropic]'      # Anthropic
# pip install -e '.[dev,a2a,api,groq]'          # Groq
# pip install -e '.[dev,a2a,api,vector]'        # ChromaDB vector store
# pip install -e '.[dev,a2a,api,otel]'          # OpenTelemetry export
# pip install -e '.[dev,a2a,api,all]'           # Everything
```

**Expected:** `pip install` succeeds; `python -c "import albra_seed"`
returns without error.

### 2.4 Verify the installation

```bash
python scripts/clean_room_verify.py
# Expected: 17/17 required + 2/2 optional PASS, "CLEAN-ROOM: PASS"

python scripts/audit_matrix.py
# Expected: 40/40 VERIFIED rows

python scripts/parity_check.py
# Expected: is_pure_seed=True, 0 behavioral failures

python scripts/contamination_scan.py
# Expected: 0 critical findings

python -m pytest tests/ --ignore=tests/llm_real -q
# Expected: all tests pass (648+ at time of writing)
```

If any gate fails, STOP. Do not proceed to configuration. Investigate
the failure; it may indicate a code or environment issue.

---

## 3. Configuration

### 3.1 Environment variables

Create a deployment environment file at `/etc/albra/albra.env` (owned
by `root:albra`, mode `0640`). This file is sourced by the systemd unit.

```bash
# /etc/albra/albra.env  — EXAMPLE (no real secrets here)
# Real secrets must be injected by the secret manager at runtime.

ALBRA_API_HOST=127.0.0.1
ALBRA_API_PORT=8000
ALBRA_LOG_LEVEL=INFO
ALBRA_LOG_FORMAT=json
ALBRA_LOG_PATH=/var/log/albra/agent.log
ALBRA_AUDIT_LOG_PATH=/var/log/albra/audit.jsonl
ALBRA_METRICS_ENABLED=true
ALBRA_TRACING_ENABLED=false

# Durable HITL approval storage (REQUIRED for production)
ALBRA_APPROVAL_DB_PATH=/var/lib/albra/approvals.db

# Memory backend (sqlite for persistence, or memory for ephemeral)
ALBRA_MEMORY_BACKEND=sqlite
ALBRA_MEMORY_PATH=/var/lib/albra/memory.db

# Default autonomy level (L0_OBSERVE is safest)
ALBRA_DEFAULT_AUTONOMY_LEVEL=0

# Provider keys — INJECT FROM SECRET MANAGER, NOT FROM THIS FILE
# OPENAI_API_KEY=...      (inject at runtime)
# ANTHROPIC_API_KEY=...   (inject at runtime)
# GROQ_API_KEY=...        (inject at runtime)
# ZAI_API_KEY=...         (inject at runtime)
# OLLAMA_BASE_URL=...     (inject at runtime)
```

### 3.2 Filesystem permissions

```bash
# Database directory — owned by albra, mode 0700
sudo mkdir -p /var/lib/albra
sudo chown albra:albra /var/lib/albra
sudo chmod 0700 /var/lib/albra

# Log directory — owned by albra, mode 0750
sudo mkdir -p /var/log/albra
sudo chown albra:albra /var/log/albra
sudo chmod 0750 /var/log/albra

# Config file — owned by root:albra, mode 0640
sudo chown root:albra /etc/albra/albra.env
sudo chmod 0640 /etc/albra/albra.env
```

**Verify:**
- `/var/lib/albra/` is on the encrypted persistent volume.
- `/var/lib/albra/` is NOT a symlink (the SQLite store rejects symlinks).
- `/var/lib/albra/` is NOT on a network filesystem (SQLite WAL locking
  is unreliable on NFS).

### 3.3 Systemd unit

Create `/etc/systemd/system/albra-api.service`:

```ini
[Unit]
Description=ALBRA SEED API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=albra
Group=albra
EnvironmentFile=/etc/albra/albra.env
WorkingDirectory=/opt/albra
ExecStart=/opt/albra/.venv/bin/uvicorn albra_seed.api.server:app \
    --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/albra /var/log/albra
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictNamespaces=true
LockPersonality=true
MemoryDenyWriteExecute=false
RestrictRealtime=true
RestrictSUIDSGID=true
SystemCallArchitectures=native
CapabilityBoundingSet=
AmbientCapabilities=

[Install]
WantedBy=multi-user.target
```

### 3.4 Reverse proxy (nginx example)

```nginx
server {
    listen 443 ssl http2;
    server_name albra-staging.example.internal;

    ssl_certificate     /etc/ssl/certs/albra.pem;
    ssl_certificate_key /etc/ssl/private/albra.key;

    # Allow only approved source ranges or identity-aware gateway
    allow 10.0.0.0/8;
    deny  all;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 300s;
    }
}
```

---

## 4. Startup

### 4.1 First start

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable + start
sudo systemctl enable albra-api
sudo systemctl start albra-api

# Check status
sudo systemctl status albra-api
```

**Expected:** `active (running)`, no errors in `journalctl -u albra-api`.

### 4.2 Health verification

```bash
# Health
curl -sS https://albra-staging.example.internal/api/v1/health | jq
# Expected: { "status": "healthy", "phase": "running", ... }

# Readiness
curl -sS https://albra-staging.example.internal/api/v1/readiness | jq
# Expected: { "ready": true, "phase": "running" }

# Agent info
curl -sS https://albra-staging.example.internal/api/v1/agent/info | jq
# Expected: { "agent_id": "...", "name": "...", ... }
```

### 4.3 Authenticator verification

```bash
# WITHOUT a token — must return 401
curl -sS -o /dev/null -w "%{http_code}\n" \
    https://albra-staging.example.internal/api/v1/approvals/pending
# Expected: 401

# WITH an invalid token — must return 401
curl -sS -o /dev/null -w "%{http_code}\n" \
    -H "Authorization: Bearer invalid-token" \
    https://albra-staging.example.internal/api/v1/approvals/pending
# Expected: 401
```

### 4.4 HITL smoke test (read-only)

```bash
# Use the read-only acceptance check script.
# Requires ALBRA_HITL_BASE_URL + ALBRA_HITL_VALID_TOKEN in env.
export ALBRA_HITL_BASE_URL=https://albra-staging.example.internal
export ALBRA_HITL_VALID_TOKEN=$(secret-manager get staging/albra/list-token)

python3 scripts/hitl_acceptance_check.py
# Expected: PASS (anonymous rejected, valid token accepted, no-list token 403)
```

---

## 5. Persistence verification

### 5.1 Verify DB exists with correct permissions

```bash
ls -la /var/lib/albra/approvals.db
# Expected: -rw------- 1 albra albra ... approvals.db
#           mode 0600, owned by albra:albra

# Verify it's not a symlink
test ! -L /var/lib/albra/approvals.db && echo "OK: not a symlink"
```

### 5.2 Verify schema version

```bash
sqlite3 /var/lib/albra/approvals.db "PRAGMA user_version;"
# Expected: a known schema version (currently 1)
```

### 5.3 Verify persistence across restart

```bash
# Create a test approval (requires decide permission + a canary tool)
# ... (per ACCEPTANCE_EVIDENCE.md §4 Durability test)

# Restart the API
sudo systemctl restart albra-api

# Verify the approval still exists with the same ID, status, and arg digest
curl -sS -H "Authorization: Bearer $TOKEN" \
    https://albra-staging.example.internal/api/v1/approvals/pending | jq
```

---

## 6. Rollback procedure

### 6.1 Code rollback

```bash
# Stop the service
sudo systemctl stop albra-api

# Checkout the previous known-good commit
cd /opt/albra
git fetch origin
git checkout <PREVIOUS_COMMIT_SHA>

# Reinstall
source .venv/bin/activate
pip install -e '.[dev,a2a,api]'

# Start
sudo systemctl start albra-api
```

### 6.2 Database rollback

**WARNING:** rolling back the database is a destructive operation.
Only do this if the current DB is corrupt or the schema version is
incompatible. Prefer rolling forward (upgrade code) when possible.

```bash
# 1. Stop all workers (per BACKUP_RESTORE_RUNBOOK.md §4)
sudo systemctl stop albra-api

# 2. Preserve the current DB
cp -p /var/lib/albra/approvals.db /var/lib/albra/approvals.db.rollback-$(date +%s)

# 3. Restore from backup per BACKUP_RESTORE_RUNBOOK.md §4

# 4. Verify schema version matches the code
sqlite3 /var/lib/albra/approvals.db "PRAGMA user_version;"

# 5. Start the service
sudo systemctl start albra-api
```

### 6.3 Configuration rollback

```bash
# Config is in /etc/albra/albra.env (or secret manager)
# Keep the previous version:
sudo cp /etc/albra/albra.env /etc/albra/albra.env.previous
# To rollback:
sudo cp /etc/albra/albra.env.previous /etc/albra/albra.env
sudo systemctl restart albra-api
```

---

## 7. Acceptance criteria

Deployment is accepted ONLY when ALL of the following are TRUE:

- [ ] Clean install succeeds on Python 3.10/3.11/3.12.
- [ ] All clean-room gates pass (17/17 + 2/2).
- [ ] Test suite passes (`pytest tests/ --ignore=tests/llm_real`).
- [ ] API starts and responds to `/api/v1/health` with `status=healthy`.
- [ ] `/api/v1/readiness` returns `ready=true`.
- [ ] Anonymous + invalid-token requests to `/api/v1/approvals/pending` return 401.
- [ ] Valid token without `approval:list` permission returns 403.
- [ ] DB file is mode 0600, owned by the service account.
- [ ] DB is NOT a symlink and NOT on a network filesystem.
- [ ] DB schema version matches the code's expected version.
- [ ] Approval persists across API restart.
- [ ] Backup/restore drill executed on the staging DB (per `BACKUP_RESTORE_RUNBOOK.md`).
- [ ] No secrets in logs, env files, or Git.
- [ ] TLS terminates at the reverse proxy; Uvicorn not directly exposed.
- [ ] Egress firewall allows only model providers + IdP + telemetry.
- [ ] Logs are shipped to the aggregator.
- [ ] Staging is isolated from production (separate DB, secrets, network).

Record the result of each criterion in `ACCEPTANCE_EVIDENCE.md`.

---

## 8. Known limits

- The deployment does NOT auto-resume a dead `Agent.run()` coroutine.
  Only the explicit `/api/v1/approvals/{id}/resume` endpoint can
  dispatch an approved call after a crash.
- SQLite is a single-writer database. All workers MUST share the same
  DB file on the same host with reliable filesystem locking.
- The HITL approval API is NOT cross-worker safe if each worker points
  at a different DB file.
- External side effects are NOT atomic with the DB commit. A crash
  after an external call may leave the dispatch in `uncertain` state.
  Reconcile manually per `BACKUP_RESTORE_RUNBOOK.md` §6.
- No automated retention/purge job exists. Manual purge after
  product/legal sign-off per `BACKUP_RESTORE_RUNBOOK.md` §5.

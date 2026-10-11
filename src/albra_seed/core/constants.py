"""ALBRA AGENT SEED constants. Versioning and defaults."""

SEED_VERSION = "ALBRA_SEED_V1.0"
RUNTIME_VERSION = "1.0.0"

# Default model / provider — Mock is always available without external deps
DEFAULT_PROVIDER = "mock"
DEFAULT_MODEL = "albra-mock-v1"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 2048

# Memory
DEFAULT_MEMORY_MODE = "short_term"  # short_term | long_term | hybrid
DEFAULT_MEMORY_BACKEND = "sqlite"
DEFAULT_MEMORY_PATH = "./data/memory.db"

# Security / audit
DEFAULT_GUARDRAILS_ENABLED = True
DEFAULT_AUDIT_LOG_PATH = "./logs/audit.jsonl"

# Observability
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_LOG_FORMAT = "json"
DEFAULT_LOG_PATH = "./logs/agent.log"
DEFAULT_METRICS_ENABLED = True
DEFAULT_TRACING_ENABLED = False

# Runtime
DEFAULT_API_HOST = "0.0.0.0"
DEFAULT_API_PORT = 8000
DEFAULT_SESSION_TTL_SECONDS = 3600
DEFAULT_MAX_CONCURRENT_SESSIONS = 100
DEFAULT_AUTONOMY_LEVEL = 0  # L0_OBSERVE

# Timeouts & retries (defaults for resilience layer)
DEFAULT_TOOL_TIMEOUT_SECONDS = 30
DEFAULT_PROVIDER_TIMEOUT_SECONDS = 60
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_BASE = 0.5  # seconds
DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 5
DEFAULT_CIRCUIT_BREAKER_RESET_SECONDS = 60

# Audit / integrity
DEFAULT_AUDIT_HASH_ALGORITHM = "sha256"

# Kill switch cooldown
DEFAULT_KILL_SWITCH_COOLDOWN_SECONDS = 300

# Approval engine
DEFAULT_APPROVAL_TTL_SECONDS = 600

# Hit-tracking
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60

# Self-healing
DEFAULT_RECOVERY_MAX_ATTEMPTS = 2

# Capability permissions (universal defaults)
DEFAULT_TOOL_RISK_LEVEL = "low"

# Immutability marker — never modified at runtime
CORE_IMMUTABLE_TAG = "ALBRA_CORE_IMMUTABLE_V1"

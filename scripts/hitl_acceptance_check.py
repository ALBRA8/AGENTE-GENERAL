#!/usr/bin/env python3
"""Read-only deployment smoke test for the HITL approval-list API.

Required environment:
  ALBRA_HITL_BASE_URL          e.g. https://agent.example.internal
  ALBRA_HITL_VALID_TOKEN       staging/canary token with approval:list
Optional:
  ALBRA_HITL_NO_LIST_TOKEN     valid token without approval:list (must get 403)

Tokens are read only from the environment and never printed. This script does
not create/decide/resume approvals and cannot verify firewall rules, encryption,
database permissions, or the real provider; use the production runbook too.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse


def request(url: str, token: str | None, timeout: float = 8.0) -> tuple[int, str]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # Do not include response body: a misconfigured service might echo secrets.
        return exc.code, ""
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"request failed ({type(exc).__name__})") from None


def main() -> int:
    base = os.environ.get("ALBRA_HITL_BASE_URL", "").rstrip("/")
    valid_token = os.environ.get("ALBRA_HITL_VALID_TOKEN", "")
    no_list_token = os.environ.get("ALBRA_HITL_NO_LIST_TOKEN", "")
    if not base or not valid_token:
        print("FAIL: set ALBRA_HITL_BASE_URL and ALBRA_HITL_VALID_TOKEN")
        return 2
    parsed = urlparse(base)
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        print("FAIL: HTTPS is required except for loopback-only test deployments")
        return 2
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        print("FAIL: base URL must not contain credentials, query, or fragment")
        return 2

    url = f"{base}/api/v1/approvals/pending"
    checks: list[tuple[str, int, int]] = []
    try:
        checks.append(("anonymous request denied", request(url, None)[0], 401))
        checks.append(("invalid bearer denied", request(url, "invalid-smoke-test-token")[0], 401))
        status, body = request(url, valid_token)
        checks.append(("authorized list allowed", status, 200))
        if status == 200:
            try:
                payload = json.loads(body)
                if not isinstance(payload, (dict, list)):
                    raise ValueError
            except (json.JSONDecodeError, ValueError):
                print("FAIL: authorized list returned an unexpected JSON shape")
                return 1
        if no_list_token:
            checks.append(("valid principal without list permission denied",
                           request(url, no_list_token)[0], 403))
    except RuntimeError as exc:
        print(f"FAIL: {exc}")
        return 1

    failed = False
    for name, actual, expected in checks:
        ok = actual == expected
        print(f"{'PASS' if ok else 'FAIL'}: {name} (HTTP {actual}, expected {expected})")
        failed |= not ok
    if not no_list_token:
        print("SKIP: RBAC negative test (ALBRA_HITL_NO_LIST_TOKEN not configured)")
    print("LIMIT: this read-only smoke test does not verify decide/resume RBAC, TLS certificate policy,")
    print("       storage encryption, backups/restores, infrastructure firewall, or provider side effects.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

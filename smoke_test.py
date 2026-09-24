#!/usr/bin/env python3
"""
Smoke test -- verifies each pipeline role can reach its configured model.

Uses the same config resolution and adapters as orchestrate.py, so a pass here
means the pipeline's own code path works.

Run: python3 ~/ai-workspace/.shared/smoke_test.py
"""
import sys
from pathlib import Path

from dotenv import load_dotenv

SHARED_DIR = Path(__file__).parent
load_dotenv(SHARED_DIR / ".env")
sys.path.insert(0, str(SHARED_DIR))

from models import (  # noqa: E402
    ROLES, ConfigError, GenerationRequest, build_client, describe, resolve_role,
    validate_unique_models,
)

# Thinking and reasoning share the output budget with visible text on current
# models, so a tight ceiling returns no text at all. 2000 leaves room for both.
REQUEST = GenerationRequest(system=None, user="Reply with only: OK", max_tokens=2000)


def check(role: str) -> bool:
    try:
        cfg = resolve_role(role)
        label = f"{role:<14} {describe(cfg)}"
    except ConfigError as e:
        print(f"  [FAIL] {role}: {e}")
        return False
    try:
        # Low effort keeps the test cheap where the role allows setting it.
        request = REQUEST
        if cfg.preflight_effort:
            request = GenerationRequest(
                system=None, user=REQUEST.user, max_tokens=REQUEST.max_tokens,
                effort=cfg.preflight_effort,
            )
        text = build_client(cfg).generate(request).text.strip()
        if not text:
            raise ValueError("empty response -- token ceiling too low for thinking/reasoning")
        print(f"  [OK]   {label}: {text[:60]}")
        return True
    except Exception as e:
        print(f"  [FAIL] {label}: {e}")
        return False


def check_unique() -> bool:
    try:
        validate_unique_models({role: resolve_role(role) for role in ROLES})
        return True
    except ConfigError as e:
        print(f"  [FAIL] {e}")
        return False


print("\nSmoke Test -- Pipeline Roles\n")
results = [check(role) for role in ROLES] + [check_unique()]
print()
if all(results):
    print("All roles reachable. Pipeline is ready.\n")
    sys.exit(0)

print("One or more roles failed.")
print("Check the role settings and API keys in ~/ai-workspace/.shared/.env")
print("Common causes: missing or invalid key, insufficient credits, wrong model ID,")
print("or an openai_compatible role missing its BASE_URL or TOKEN_PARAM.\n")
sys.exit(1)

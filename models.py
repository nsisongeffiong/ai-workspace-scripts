"""
Provider adapters and role configuration for the pipeline.

This is the only module that imports provider SDKs, and each SDK is imported
lazily inside its adapter, so an unused provider never blocks startup.

Each pipeline role (implementation, quality, security) is configured in .env:

  <ROLE>_PROVIDER           anthropic | openai | openai_compatible | gemini
  <ROLE>_MODEL              model ID, passed through unchanged
  <ROLE>_MAX_OUTPUT_TOKENS  optional; output ceiling for the role's main stage
  <ROLE>_REASONING_EFFORT   optional; passed through unchanged; omit for the model default
  <ROLE>_BASE_URL           optional; required for openai_compatible
  <ROLE>_API_KEY_ENV        optional; name of the env var holding the key
  <ROLE>_TIMEOUT_SECONDS    optional; per-request timeout
  <ROLE>_TOKEN_PARAM        optional; openai_compatible only: max_tokens | max_completion_tokens

  IMPLEMENTATION_PREFLIGHT_EFFORT  optional; effort for brand pre-flight calls

If a role sets neither PROVIDER nor MODEL, it falls back to the legacy
CLAUDE_MODEL / GPT_MODEL / GEMINI_MODEL settings with the pipeline's original
defaults. Setting only one of the two is an error.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

ROLES = ("implementation", "quality", "security")

PROVIDERS = ("anthropic", "openai", "openai_compatible", "gemini")

DEFAULT_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GOOGLE_API_KEY",
}

# Legacy fallback: reproduces the pre-refactor pipeline exactly.
LEGACY = {
    "implementation": dict(
        provider="anthropic", model_env="CLAUDE_MODEL", model="claude-opus-5",
        max_output_tokens=None, reasoning_effort="xhigh",
        preflight_effort="low", timeout=None,
    ),
    "quality": dict(
        provider="openai", model_env="GPT_MODEL", model="gpt-5.6-sol",
        max_output_tokens=16000, reasoning_effort=None,
        preflight_effort=None, timeout=120,
    ),
    "security": dict(
        provider="gemini", model_env="GEMINI_MODEL", model="gemini-3.6-flash",
        max_output_tokens=16000, reasoning_effort=None,
        preflight_effort=None, timeout=None,
    ),
}


class ConfigError(Exception):
    """Role configuration is missing, incomplete or invalid."""


class RateLimited(Exception):
    """Provider rate limit. The only error the orchestrator retries."""


# -- Configuration ------------------------------------------------------------

@dataclass(frozen=True)
class RoleConfig:
    role: str
    provider: str
    model: str
    max_output_tokens: int
    reasoning_effort: Optional[str]
    preflight_effort: Optional[str]
    base_url: Optional[str]
    api_key_env: str
    timeout: Optional[float]
    token_param: str
    legacy: bool


def _env(name: str) -> Optional[str]:
    val = os.getenv(name)
    if val is None:
        return None
    val = val.strip()
    return val or None


def _int_env(name: str, default: Optional[int]) -> Optional[int]:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got '{raw}'")


def _float_env(name: str, default: Optional[float]) -> Optional[float]:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got '{raw}'")


def resolve_role(role: str) -> RoleConfig:
    """Resolve one role's configuration from the environment."""
    if role not in ROLES:
        raise ConfigError(f"Unknown role '{role}'")
    prefix = role.upper()
    provider = _env(f"{prefix}_PROVIDER")
    model = _env(f"{prefix}_MODEL")

    if provider is None and model is None:
        legacy = LEGACY[role]
        provider = legacy["provider"]
        model = _env(legacy["model_env"]) or legacy["model"]
        # The implementation role's ceiling has always come from MAX_OUTPUT_TOKENS.
        default_tokens = legacy["max_output_tokens"] or _int_env("MAX_OUTPUT_TOKENS", 64000)
        effort = legacy["reasoning_effort"]
        preflight_effort = legacy["preflight_effort"]
        default_timeout = legacy["timeout"]
        is_legacy = True
    elif provider is None or model is None:
        missing = f"{prefix}_PROVIDER" if provider is None else f"{prefix}_MODEL"
        raise ConfigError(
            f"Incomplete config for the {role} role: {missing} is not set.\n"
            f"Set both {prefix}_PROVIDER and {prefix}_MODEL, or remove both to use "
            f"the legacy CLAUDE_MODEL / GPT_MODEL / GEMINI_MODEL settings."
        )
    else:
        provider = provider.lower()
        default_tokens = _int_env("MAX_OUTPUT_TOKENS", 64000) if role == "implementation" else 16000
        effort = None
        preflight_effort = None
        default_timeout = None
        is_legacy = False

    if provider not in PROVIDERS:
        raise ConfigError(
            f"Unknown provider '{provider}' for the {role} role. "
            f"Supported: {', '.join(PROVIDERS)}"
        )

    base_url = _env(f"{prefix}_BASE_URL")
    if provider == "openai_compatible" and not base_url:
        raise ConfigError(f"{prefix}_BASE_URL is required when {prefix}_PROVIDER=openai_compatible")

    key_env = _env(f"{prefix}_API_KEY_ENV") or DEFAULT_KEY_ENV.get(provider)
    if not key_env:
        raise ConfigError(
            f"{prefix}_API_KEY_ENV is required for provider '{provider}' "
            f"(the name of the env var that holds the API key)"
        )

    token_param = (_env(f"{prefix}_TOKEN_PARAM") or "max_tokens").lower()
    if token_param not in ("max_tokens", "max_completion_tokens"):
        raise ConfigError(f"{prefix}_TOKEN_PARAM must be max_tokens or max_completion_tokens")

    timeout = _float_env(f"{prefix}_TIMEOUT_SECONDS", default_timeout)

    return RoleConfig(
        role=role,
        provider=provider,
        model=model,
        max_output_tokens=_int_env(f"{prefix}_MAX_OUTPUT_TOKENS", default_tokens),
        reasoning_effort=_env(f"{prefix}_REASONING_EFFORT") or effort,
        preflight_effort=_env(f"{prefix}_PREFLIGHT_EFFORT") or preflight_effort,
        base_url=base_url,
        api_key_env=key_env,
        timeout=timeout,
        token_param=token_param,
        legacy=is_legacy,
    )


def validate_unique_models(configs: dict) -> None:
    """Require a different model for each role.

    The pipeline's value comes from independent models reviewing each other's
    work. Model IDs are compared case-insensitively regardless of provider, so
    the same model reached through two providers is still caught when the IDs
    match. A gateway that renames a model (e.g. "vendor/model") cannot be
    detected. Stage 4 reusing the implementation model is by design.
    """
    seen = {}
    for role, cfg in configs.items():
        key = cfg.model.lower()
        if key in seen:
            raise ConfigError(
                f"The {seen[key]} and {role} roles both use {cfg.model}. "
                f"Each role needs a different model -- change "
                f"{role.upper()}_MODEL (and {role.upper()}_PROVIDER if needed)."
            )
        seen[key] = role


# -- .env migration -------------------------------------------------------------

def legacy_role_lines(role: str, model: str) -> list:
    """Role settings equivalent to what the legacy fallback does for this role."""
    legacy = LEGACY[role]
    prefix = role.upper()
    lines = [f"{prefix}_PROVIDER={legacy['provider']}", f"{prefix}_MODEL={model}"]
    if legacy["reasoning_effort"]:
        lines.append(f"{prefix}_REASONING_EFFORT={legacy['reasoning_effort']}")
    if legacy["preflight_effort"]:
        lines.append(f"{prefix}_PREFLIGHT_EFFORT={legacy['preflight_effort']}")
    if legacy["timeout"]:
        lines.append(f"{prefix}_TIMEOUT_SECONDS={int(legacy['timeout'])}")
    return lines


def migrate_env_file(path, fill_defaults: bool) -> list:
    """Replace CLAUDE_MODEL / GPT_MODEL / GEMINI_MODEL with role settings.

    Each legacy line is replaced in place by the equivalent role settings, so
    the pipeline behaves exactly as before. A role that is already configured
    in this file is left alone, and its legacy line (which was being ignored)
    is removed. With fill_defaults (the shared .env), roles with no legacy line
    are written out with the default model so the file is fully role-based;
    project .env files only convert lines they actually have.

    Returns human-readable descriptions of the changes; an empty list means the
    file was not modified. Safe to run repeatedly.
    """
    import re as _re
    import tempfile
    from pathlib import Path as _Path

    path = _Path(path)
    lines = path.read_text(encoding="utf-8").splitlines()

    def assigned(name):
        pat = _re.compile(rf"^\s*(?:export\s+)?{name}\s*=")
        return [i for i, ln in enumerate(lines) if pat.match(ln)]

    changes, replace_at, append = [], {}, []
    for role in ROLES:
        prefix = role.upper()
        legacy_var = LEGACY[role]["model_env"]
        legacy_idx = assigned(legacy_var)
        configured = assigned(f"{prefix}_PROVIDER") or assigned(f"{prefix}_MODEL")
        if configured:
            for i in legacy_idx:
                replace_at[i] = []
                changes.append(f"removed unused {lines[i].strip()} ({role} role already configured)")
            continue
        if legacy_idx:
            model = lines[legacy_idx[-1]].split("=", 1)[1].strip().strip('"').strip("'")
            model = model or LEGACY[role]["model"]
            block = legacy_role_lines(role, model)
            replace_at[legacy_idx[-1]] = block
            for i in legacy_idx[:-1]:
                replace_at[i] = []
            changes.append(f"{legacy_var}={model} -> {prefix}_PROVIDER/{prefix}_MODEL")
        elif fill_defaults:
            append.extend(legacy_role_lines(role, LEGACY[role]["model"]))
            changes.append(f"added {prefix}_* with default model {LEGACY[role]['model']}")

    if not changes:
        return []
    out = []
    for i, ln in enumerate(lines):
        out.extend(replace_at.get(i, [ln]))
    if append:
        out.extend([""] + append)
    # Write to a private temp file beside the original, then swap it in with a
    # single rename: the original is never left half-written, and the temp copy
    # (which holds API keys) is removed if anything fails.
    mode = path.stat().st_mode & 0o777
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".env.", suffix=".tmp")
    tmp_path = _Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write("\n".join(out) + "\n")
        tmp_path.chmod(mode)
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return changes


# -- Requests and results -----------------------------------------------------

@dataclass(frozen=True)
class GenerationRequest:
    system: Optional[str]
    user: str
    max_tokens: int
    effort: Optional[str] = None
    thinking: bool = False  # Anthropic only: request adaptive thinking explicitly


@dataclass(frozen=True)
class GenerationResult:
    text: str
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    truncated: bool


# -- Adapters -----------------------------------------------------------------

class AnthropicClient:
    def __init__(self, cfg: RoleConfig, api_key: str):
        import anthropic
        self._sdk = anthropic
        kwargs = {"api_key": api_key}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        if cfg.timeout is not None:
            kwargs["timeout"] = cfg.timeout
        self._client = anthropic.Anthropic(**kwargs)
        self.model = cfg.model

    def generate(self, req: GenerationRequest) -> GenerationResult:
        kwargs = {"model": self.model, "max_tokens": req.max_tokens}
        if req.thinking:
            kwargs["thinking"] = {"type": "adaptive"}
        if req.effort:
            kwargs["output_config"] = {"effort": req.effort}
        if req.system:
            kwargs["system"] = req.system
        kwargs["messages"] = [{"role": "user", "content": req.user}]
        try:
            # Always stream: the SDK rejects non-streaming requests whose
            # estimated duration exceeds ~10 minutes, and streaming is harmless
            # for short calls. get_final_message() returns the full Message.
            with self._client.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
        except self._sdk.RateLimitError as e:
            raise RateLimited(str(e)) from e
        # Never index content[0]: the first block may be a thinking block.
        text = "".join(b.text for b in msg.content if b.type == "text")
        usage = getattr(msg, "usage", None)
        return GenerationResult(
            text=text,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            truncated=getattr(msg, "stop_reason", None) == "max_tokens",
        )


class OpenAIClient:
    """OpenAI Chat Completions, and any OpenAI-compatible endpoint."""

    def __init__(self, cfg: RoleConfig, api_key: str):
        import openai
        self._sdk = openai
        kwargs = {"api_key": api_key}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        self._client = openai.OpenAI(**kwargs)
        self.model = cfg.model
        self._timeout = cfg.timeout
        # OpenAI reasoning models reject max_tokens; many compatible endpoints
        # only accept it. The compatible default is overridable per role.
        self._token_param = (
            "max_completion_tokens" if cfg.provider == "openai" else cfg.token_param
        )

    def generate(self, req: GenerationRequest) -> GenerationResult:
        messages = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.user})
        kwargs = {"model": self.model, self._token_param: req.max_tokens}
        if self._timeout is not None:
            kwargs["timeout"] = self._timeout
        if req.effort:
            kwargs["reasoning_effort"] = req.effort
        kwargs["messages"] = messages
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except self._sdk.RateLimitError as e:
            raise RateLimited(str(e)) from e
        choice = resp.choices[0]
        usage = getattr(resp, "usage", None)
        return GenerationResult(
            text=choice.message.content or "",
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            truncated=getattr(choice, "finish_reason", None) == "length",
        )


class GeminiClient:
    def __init__(self, cfg: RoleConfig, api_key: str):
        from google import genai
        from google.genai import types
        self._types = types
        try:
            from google.genai import errors
            self._api_error = errors.APIError
        except ImportError:
            self._api_error = None
        kwargs = {"api_key": api_key}
        if cfg.timeout is not None:
            # google-genai takes the timeout in milliseconds
            kwargs["http_options"] = types.HttpOptions(timeout=int(cfg.timeout * 1000))
        self._client = genai.Client(**kwargs)
        self.model = cfg.model

    def generate(self, req: GenerationRequest) -> GenerationResult:
        config = {"max_output_tokens": req.max_tokens}
        if req.system:
            config["system_instruction"] = req.system
        if req.effort:
            # Thinking settings are nested in ThinkingConfig, not top-level.
            config["thinking_config"] = self._types.ThinkingConfig(thinking_level=req.effort)
        try:
            resp = self._client.models.generate_content(
                model=self.model,
                contents=req.user,
                config=self._types.GenerateContentConfig(**config),
            )
        except Exception as e:
            if self._api_error and isinstance(e, self._api_error) and str(getattr(e, "code", "")) == "429":
                raise RateLimited(str(e)) from e
            raise
        usage = getattr(resp, "usage_metadata", None)
        finish = None
        candidates = getattr(resp, "candidates", None) or []
        if candidates:
            finish = str(getattr(candidates[0], "finish_reason", "") or "")
        return GenerationResult(
            text=resp.text or "",
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
            truncated="MAX_TOKENS" in (finish or ""),
        )


_ADAPTERS = {
    "anthropic": AnthropicClient,
    "openai": OpenAIClient,
    "openai_compatible": OpenAIClient,
    "gemini": GeminiClient,
}


def build_client(cfg: RoleConfig):
    """Build the adapter for a resolved role. Fails if the API key is missing."""
    api_key = _env(cfg.api_key_env)
    if not api_key:
        raise ConfigError(
            f"Missing API key for the {cfg.role} role: {cfg.api_key_env} is not set.\n"
            f"Add it to ~/ai-workspace/.shared/.env or the project .env"
        )
    return _ADAPTERS[cfg.provider](cfg, api_key)


def describe(cfg: RoleConfig) -> str:
    """One-line, secret-free summary of a role, for logs and the smoke test."""
    parts = [f"{cfg.provider}/{cfg.model}", f"max_tokens={cfg.max_output_tokens}"]
    if cfg.reasoning_effort:
        parts.append(f"effort={cfg.reasoning_effort}")
    if cfg.base_url:
        parts.append(f"base_url={cfg.base_url}")
    if cfg.legacy:
        parts.append("(legacy config)")
    return " ".join(parts)

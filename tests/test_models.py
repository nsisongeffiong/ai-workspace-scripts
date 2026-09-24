"""
Offline tests for the provider adapters and role configuration.

No API keys, no network: provider SDKs are replaced with fakes that record the
arguments they receive. The parity tests pin the exact request each stage sent
before the model-agnostic refactor, so a default install behaves identically.

Run from the repo root: python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

CALLS = []  # (sdk, kwargs) recorded by the fakes


# -- Fake SDKs ----------------------------------------------------------------

def _block(kind, text=None):
    return types.SimpleNamespace(type=kind, text=text) if text is not None else types.SimpleNamespace(type=kind)


class FakeAnthropicRateLimit(Exception):
    pass


class _Stream:
    def __init__(self, msg):
        self._msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._msg


class FakeAnthropic:
    raise_next = []

    def __init__(self, **kwargs):
        CALLS.append(("anthropic.client", kwargs))
        self.messages = self

    def stream(self, **kwargs):
        CALLS.append(("anthropic", kwargs))
        if FakeAnthropic.raise_next:
            raise FakeAnthropic.raise_next.pop(0)
        return _Stream(types.SimpleNamespace(
            content=[_block("thinking"), _block("text", "hello "), _block("text", "world")],
            usage=types.SimpleNamespace(input_tokens=10, output_tokens=20),
            stop_reason="end_turn",
        ))


class FakeOpenAIRateLimit(Exception):
    pass


class FakeOpenAI:
    def __init__(self, **kwargs):
        CALLS.append(("openai.client", kwargs))
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kwargs):
        CALLS.append(("openai", kwargs))
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content="review"), finish_reason="length")],
            usage=types.SimpleNamespace(prompt_tokens=5, completion_tokens=6),
        )


class FakeGenaiAPIError(Exception):
    def __init__(self, code):
        super().__init__(f"code {code}")
        self.code = code


class _Recorded:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __eq__(self, other):
        return type(self) is type(other) and self.kwargs == other.kwargs

    def __repr__(self):
        return f"{type(self).__name__}({self.kwargs})"


class GenerateContentConfig(_Recorded):
    pass


class ThinkingConfig(_Recorded):
    pass


class HttpOptions(_Recorded):
    pass


class FakeGenaiClient:
    def __init__(self, **kwargs):
        CALLS.append(("gemini.client", kwargs))
        self.models = self

    def generate_content(self, **kwargs):
        CALLS.append(("gemini", kwargs))
        return types.SimpleNamespace(
            text=None,  # the SDK can return None; adapters must cope
            usage_metadata=None,
            candidates=[types.SimpleNamespace(finish_reason="STOP")],
        )


def install_fake_sdks():
    anthropic = types.ModuleType("anthropic")
    anthropic.Anthropic = FakeAnthropic
    anthropic.RateLimitError = FakeAnthropicRateLimit
    openai = types.ModuleType("openai")
    openai.OpenAI = FakeOpenAI
    openai.RateLimitError = FakeOpenAIRateLimit
    google = types.ModuleType("google")
    genai = types.ModuleType("google.genai")
    genai_types = types.ModuleType("google.genai.types")
    genai_errors = types.ModuleType("google.genai.errors")
    genai.Client = FakeGenaiClient
    genai_types.GenerateContentConfig = GenerateContentConfig
    genai_types.ThinkingConfig = ThinkingConfig
    genai_types.HttpOptions = HttpOptions
    genai_errors.APIError = FakeGenaiAPIError
    genai.types = genai_types
    genai.errors = genai_errors
    google.genai = genai
    sys.modules.update({
        "anthropic": anthropic, "openai": openai, "google": google,
        "google.genai": genai, "google.genai.types": genai_types,
        "google.genai.errors": genai_errors,
    })


install_fake_sdks()
import models  # noqa: E402

KEYS = {"ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o", "GOOGLE_API_KEY": "g"}
ROLE_VARS = [
    f"{r}_{f}" for r in ("IMPLEMENTATION", "QUALITY", "SECURITY")
    for f in ("PROVIDER", "MODEL", "MAX_OUTPUT_TOKENS", "REASONING_EFFORT", "PREFLIGHT_EFFORT",
              "BASE_URL", "API_KEY_ENV", "TIMEOUT_SECONDS", "TOKEN_PARAM")
] + ["CLAUDE_MODEL", "GPT_MODEL", "GEMINI_MODEL", "MAX_OUTPUT_TOKENS"]


class EnvTestCase(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        FakeAnthropic.raise_next = []
        self._env = mock.patch.dict(os.environ, KEYS, clear=False)
        self._env.start()
        for var in ROLE_VARS:
            os.environ.pop(var, None)

    def tearDown(self):
        self._env.stop()

    def last(self, sdk):
        return [kw for name, kw in CALLS if name == sdk][-1]


# -- Config resolution ----------------------------------------------------------

class TestConfig(EnvTestCase):
    def test_legacy_defaults(self):
        impl = models.resolve_role("implementation")
        self.assertEqual((impl.provider, impl.model, impl.max_output_tokens), ("anthropic", "claude-opus-5", 64000))
        self.assertEqual((impl.reasoning_effort, impl.preflight_effort), ("xhigh", "low"))
        q = models.resolve_role("quality")
        self.assertEqual((q.provider, q.model, q.max_output_tokens, q.timeout), ("openai", "gpt-5.6-sol", 16000, 120))
        s = models.resolve_role("security")
        self.assertEqual((s.provider, s.model, s.max_output_tokens, s.reasoning_effort), ("gemini", "gemini-3.6-flash", 16000, None))
        self.assertTrue(impl.legacy and q.legacy and s.legacy)

    def test_legacy_env_values_respected(self):
        os.environ.update(CLAUDE_MODEL="claude-x", GPT_MODEL="gpt-x", GEMINI_MODEL="gem-x", MAX_OUTPUT_TOKENS="32000")
        self.assertEqual(models.resolve_role("implementation").model, "claude-x")
        self.assertEqual(models.resolve_role("implementation").max_output_tokens, 32000)
        self.assertEqual(models.resolve_role("quality").model, "gpt-x")
        self.assertEqual(models.resolve_role("quality").max_output_tokens, 16000)
        self.assertEqual(models.resolve_role("security").model, "gem-x")

    def test_half_config_rejected(self):
        os.environ["QUALITY_PROVIDER"] = "openai"
        with self.assertRaises(models.ConfigError):
            models.resolve_role("quality")
        os.environ.pop("QUALITY_PROVIDER")
        os.environ["QUALITY_MODEL"] = "gpt-x"
        with self.assertRaises(models.ConfigError):
            models.resolve_role("quality")

    def test_explicit_role_has_no_legacy_extras(self):
        os.environ.update(IMPLEMENTATION_PROVIDER="Anthropic ", IMPLEMENTATION_MODEL="claude-y")
        cfg = models.resolve_role("implementation")
        self.assertEqual(cfg.provider, "anthropic")
        self.assertIsNone(cfg.reasoning_effort)
        self.assertIsNone(cfg.preflight_effort)
        self.assertFalse(cfg.legacy)

    def test_invalid_configs(self):
        os.environ.update(SECURITY_PROVIDER="nope", SECURITY_MODEL="m")
        with self.assertRaises(models.ConfigError):
            models.resolve_role("security")
        os.environ.update(SECURITY_PROVIDER="openai_compatible")
        with self.assertRaises(models.ConfigError):  # no BASE_URL
            models.resolve_role("security")
        os.environ.update(SECURITY_BASE_URL="https://x.example/v1")
        with self.assertRaises(models.ConfigError):  # no API_KEY_ENV
            models.resolve_role("security")
        os.environ.update(SECURITY_API_KEY_ENV="X_KEY", SECURITY_TOKEN_PARAM="bogus")
        with self.assertRaises(models.ConfigError):
            models.resolve_role("security")

    def test_duplicate_models_rejected(self):
        os.environ.update(
            IMPLEMENTATION_PROVIDER="anthropic", IMPLEMENTATION_MODEL="claude-opus-5",
            QUALITY_PROVIDER="openai_compatible", QUALITY_MODEL="Claude-Opus-5",
            QUALITY_BASE_URL="https://gw.example/v1", QUALITY_API_KEY_ENV="X_KEY",
        )
        configs = {r: models.resolve_role(r) for r in models.ROLES}
        with self.assertRaises(models.ConfigError):
            models.validate_unique_models(configs)

    def test_legacy_defaults_are_unique(self):
        models.validate_unique_models({r: models.resolve_role(r) for r in models.ROLES})

    def test_fractional_timeout(self):
        os.environ["QUALITY_TIMEOUT_SECONDS"] = "120.5"
        self.assertEqual(models.resolve_role("quality").timeout, 120.5)
        os.environ["QUALITY_TIMEOUT_SECONDS"] = "soon"
        with self.assertRaises(models.ConfigError):
            models.resolve_role("quality")

    def test_missing_key_fails_at_build(self):
        os.environ.pop("OPENAI_API_KEY")
        with self.assertRaises(models.ConfigError):
            models.build_client(models.resolve_role("quality"))


# -- Adapters -----------------------------------------------------------------

class TestAdapters(EnvTestCase):
    def test_anthropic_text_extraction_skips_thinking(self):
        client = models.build_client(models.resolve_role("implementation"))
        res = client.generate(models.GenerationRequest(system="s", user="u", max_tokens=10))
        self.assertEqual(res.text, "hello world")
        self.assertEqual((res.input_tokens, res.output_tokens, res.truncated), (10, 20, False))

    def test_openai_compatible_request(self):
        os.environ.update(
            QUALITY_PROVIDER="openai_compatible", QUALITY_MODEL="kimi-x",
            QUALITY_BASE_URL="https://api.example/v1", QUALITY_API_KEY_ENV="X_KEY",
            X_KEY="k", QUALITY_REASONING_EFFORT="high",
        )
        cfg = models.resolve_role("quality")
        res = models.build_client(cfg).generate(models.GenerationRequest(
            system="s", user="u", max_tokens=100, effort=cfg.reasoning_effort))
        self.assertEqual(self.last("openai.client"), {"api_key": "k", "base_url": "https://api.example/v1"})
        call = self.last("openai")
        self.assertEqual(call["max_tokens"], 100)
        self.assertNotIn("max_completion_tokens", call)
        self.assertEqual(call["reasoning_effort"], "high")
        self.assertTrue(res.truncated)

    def test_gemini_effort_nested_in_thinking_config(self):
        os.environ.update(SECURITY_PROVIDER="gemini", SECURITY_MODEL="gem", SECURITY_REASONING_EFFORT="low")
        cfg = models.resolve_role("security")
        res = models.build_client(cfg).generate(models.GenerationRequest(
            system="s", user="u", max_tokens=50, effort=cfg.reasoning_effort))
        config = self.last("gemini")["config"]
        self.assertEqual(config.kwargs["thinking_config"], ThinkingConfig(thinking_level="low"))
        self.assertNotIn("thinking_level", config.kwargs)
        self.assertEqual(res.text, "")
        self.assertIsNone(res.input_tokens)

    def test_gemini_rate_limit_code_as_string(self):
        os.environ.update(SECURITY_PROVIDER="gemini", SECURITY_MODEL="gem")
        client = models.build_client(models.resolve_role("security"))
        with mock.patch.object(FakeGenaiClient, "generate_content",
                               side_effect=FakeGenaiAPIError("429")):
            with self.assertRaises(models.RateLimited):
                client.generate(models.GenerationRequest(system=None, user="u", max_tokens=1))

    def test_rate_limits_translated(self):
        client = models.build_client(models.resolve_role("implementation"))
        FakeAnthropic.raise_next = [FakeAnthropicRateLimit("slow down")]
        with self.assertRaises(models.RateLimited):
            client.generate(models.GenerationRequest(system=None, user="u", max_tokens=1))


# -- Parity with the pre-refactor pipeline --------------------------------------

class TestParity(EnvTestCase):
    """Default (legacy) config must send exactly what the old stage code sent."""

    def setUp(self):
        super().setUp()
        import orchestrate
        from tenacity import wait_none
        self.o = orchestrate
        self.o.ROLE_CONFIG.clear()
        self.o.CLIENTS.clear()
        self.o.init_roles(1)
        self.patches = [
            mock.patch.object(orchestrate, "load_prompt", side_effect=lambda n: f"<{n}>"),
            mock.patch.object(orchestrate, "read_src", return_value="SRC"),
            mock.patch.object(orchestrate, "extract_code_blocks", return_value=[]),
            mock.patch.object(orchestrate, "install_dependencies"),
            mock.patch.object(orchestrate, "write_file"),
            mock.patch.object(orchestrate, "generate",
                              orchestrate.generate.retry_with(wait=wait_none())),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def test_stage_1(self):
        self.o.stage_1_implement("TASK")
        self.assertEqual(self.last("anthropic"), {
            "model": "claude-opus-5", "max_tokens": 64000,
            "thinking": {"type": "adaptive"}, "output_config": {"effort": "xhigh"},
            "system": "<implementation>", "messages": [{"role": "user", "content": "TASK"}],
        })

    def test_stage_2(self):
        self.o.stage_2_quality_review()
        self.assertEqual(self.last("openai"), {
            "model": "gpt-5.6-sol", "max_completion_tokens": 16000, "timeout": 120,
            "messages": [{"role": "system", "content": "<quality>"},
                         {"role": "user", "content": "SRC"}],
        })

    def test_stage_3(self):
        self.o.stage_3_security_audit()
        call = self.last("gemini")
        self.assertEqual((call["model"], call["contents"]), ("gemini-3.6-flash", "SRC"))
        self.assertEqual(call["config"], GenerateContentConfig(
            max_output_tokens=16000, system_instruction="<security>"))

    def test_stage_4(self):
        with mock.patch.object(Path, "read_text", return_value="REVIEW"):
            self.o.stage_4_synthesize("STAGE1")
        call = self.last("anthropic")
        self.assertEqual(call["system"], "<synthesis>")
        self.assertEqual((call["max_tokens"], call["thinking"], call["output_config"]),
                         (64000, {"type": "adaptive"}, {"effort": "xhigh"}))
        content = call["messages"][0]["content"]
        self.assertTrue(content.startswith("<review_content>\n"))
        self.assertIn("## Source Code\nSTAGE1", content)

    def test_preflight_request(self):
        req = models.GenerationRequest(system=None, user="P", max_tokens=8000,
                                       effort=self.o.ROLE_CONFIG["implementation"].preflight_effort)
        self.o.CLIENTS["implementation"].generate(req)
        self.assertEqual(self.last("anthropic"), {
            "model": "claude-opus-5", "max_tokens": 8000,
            "output_config": {"effort": "low"},
            "messages": [{"role": "user", "content": "P"}],
        })

    def test_rate_limit_retried_then_raised(self):
        FakeAnthropic.raise_next = [FakeAnthropicRateLimit("1")]
        self.o.stage_1_implement("TASK")  # one retry, then success
        self.assertEqual(sum(1 for n, _ in CALLS if n == "anthropic"), 2)
        FakeAnthropic.raise_next = [FakeAnthropicRateLimit(str(i)) for i in range(self.o.MAX_RETRIES)]
        with self.assertRaises(models.RateLimited):
            self.o.stage_1_implement("TASK")

    def test_other_errors_not_retried(self):
        FakeAnthropic.raise_next = [ValueError("bad request")]
        before = sum(1 for n, _ in CALLS if n == "anthropic")
        with self.assertRaises(ValueError):
            self.o.stage_1_implement("TASK")
        self.assertEqual(sum(1 for n, _ in CALLS if n == "anthropic") - before, 1)


class TestPromptLookup(unittest.TestCase):
    """Project beats shared; new name beats legacy name within each."""

    def setUp(self):
        import tempfile
        import orchestrate
        self.o = orchestrate
        self.tmp = Path(tempfile.mkdtemp())
        self.project = self.tmp / "project" / "prompts"
        self.shared = self.tmp / "shared" / "prompts"
        self.project.mkdir(parents=True)
        self.shared.mkdir(parents=True)
        self.patches = [
            mock.patch.object(orchestrate, "PROJECT_ROOT", self.tmp / "project"),
            mock.patch.object(orchestrate, "SHARED_DIR", self.tmp / "shared"),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def write(self, where, name):
        (where / f"{name}.md").write_text(f"{where.parent.name}:{name}")

    def test_order(self):
        self.write(self.shared, "claude_coder")
        self.assertEqual(self.o.load_prompt("implementation"), "shared:claude_coder")
        self.write(self.shared, "implementation")
        self.assertEqual(self.o.load_prompt("implementation"), "shared:implementation")
        self.write(self.project, "claude_coder")
        self.assertEqual(self.o.load_prompt("implementation"), "project:claude_coder")
        self.write(self.project, "implementation")
        self.assertEqual(self.o.load_prompt("implementation"), "project:implementation")

    def test_missing(self):
        with self.assertRaises(FileNotFoundError):
            self.o.load_prompt("quality")


if __name__ == "__main__":
    unittest.main()

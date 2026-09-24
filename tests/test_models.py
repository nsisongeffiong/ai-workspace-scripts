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
            mock.patch.object(orchestrate, "retire_legacy_review"),
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
        with mock.patch.object(self.o, "read_review", return_value="REVIEW"):
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


class ProjectTestCase(unittest.TestCase):
    """A temporary project root, with scope reset around each test."""

    def setUp(self):
        import tempfile
        import orchestrate
        self.o = orchestrate
        self.root = Path(tempfile.mkdtemp()).resolve()
        patch = mock.patch.object(orchestrate, "PROJECT_ROOT", self.root)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(lambda: setattr(orchestrate, "_ALLOWED_PATHS", None))
        orchestrate._ALLOWED_PATHS = None

    def put(self, rel, text="x"):
        f = self.root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        return f


class TestScope(ProjectTestCase):
    def test_no_scope_line_is_unrestricted(self):
        self.o.set_task_scope("Build the thing")
        self.assertIsNone(self.o._ALLOWED_PATHS)
        self.assertIsNotNone(self.o._safe_path("anything/at/all.go"))

    def test_scope_normalised_and_enforced(self):
        self.o.set_task_scope("Fix it\nSCOPE: ./src/api/client.ts, src/api/types.ts, ../escape.ts, /abs.ts\n")
        self.assertEqual(self.o._ALLOWED_PATHS, {"src/api/client.ts", "src/api/types.ts"})
        self.assertIsNotNone(self.o._safe_path("src/api/client.ts"))
        self.assertIsNotNone(self.o._safe_path("./src//api/types.ts"))
        self.assertIsNone(self.o._safe_path("src/api/other.ts"))

    def test_refused_blocks_do_not_fall_back_to_generated(self):
        self.o.set_task_scope("SCOPE: src/api/client.ts")
        text = "```ts src/api/client.ts\nexport const a = 1\n```\n```ts other/x.ts\nexport const x = 1\n```"
        written = self.o.extract_code_blocks(text)
        self.assertEqual([w.relative_to(self.root).as_posix() for w in written], ["src/api/client.ts"])
        self.assertFalse((self.root / "other" / "x.ts").exists())
        self.assertFalse((self.root / "src" / "generated.py").exists())

    def test_all_refused_writes_nothing(self):
        self.o.set_task_scope("SCOPE: src/api/client.ts")
        self.assertEqual(self.o.extract_code_blocks("```ts other/x.ts\nexport const x = 1\n```"), [])
        self.assertFalse((self.root / "src" / "generated.py").exists())

    def test_fallback_respects_scope(self):
        self.o.set_task_scope("SCOPE: src/api/client.ts")
        self.assertEqual(self.o.extract_code_blocks("no code blocks here"), [])
        self.assertFalse((self.root / "src" / "generated.py").exists())

    def test_fallback_unchanged_without_scope(self):
        written = self.o.extract_code_blocks("no code blocks here")
        self.assertEqual([w.relative_to(self.root).as_posix() for w in written], ["src/generated.py"])


class TestEnvMigration(EnvTestCase):
    SHARED = (
        "ANTHROPIC_API_KEY=a\nOPENAI_API_KEY=o\nGOOGLE_API_KEY=g\n\n"
        "CLAUDE_MODEL=claude-opus-5\nGPT_MODEL=gpt-5.6-sol\nGEMINI_MODEL=gemini-3.6-flash\n\n"
        "# a comment\nMAX_OUTPUT_TOKENS=96000\nMAX_RETRIES=3\n"
    )

    def setUp(self):
        super().setUp()
        import tempfile
        self.dir = Path(tempfile.mkdtemp())

    def write(self, text, name=".env"):
        f = self.dir / name
        f.write_text(text)
        f.chmod(0o600)
        return f

    def resolved(self, env_file):
        from dotenv import dotenv_values
        for var in ROLE_VARS:
            os.environ.pop(var, None)
        os.environ.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
        out = {}
        for r in models.ROLES:
            cfg = models.resolve_role(r)
            out[r] = {k: v for k, v in vars(cfg).items() if k != "legacy"}
        return out

    def test_shared_migration_is_behaviour_preserving(self):
        f = self.write(self.SHARED)
        before = self.resolved(f)
        changes = models.migrate_env_file(f, fill_defaults=True)
        self.assertEqual(len(changes), 3)
        text = f.read_text()
        for legacy in ("CLAUDE_MODEL", "GPT_MODEL", "GEMINI_MODEL"):
            self.assertNotIn(legacy, text)
        self.assertIn("MAX_OUTPUT_TOKENS=96000", text)
        self.assertIn("ANTHROPIC_API_KEY=a", text)
        self.assertIn("# a comment", text)
        self.assertEqual(oct(f.stat().st_mode & 0o777), oct(0o600))
        after = self.resolved(f)
        self.assertEqual(before, after)
        self.assertTrue(all(not models.resolve_role(r).legacy for r in models.ROLES))

    def test_every_non_model_line_kept_verbatim(self):
        original = (
            'ANTHROPIC_API_KEY=sk-ant-abc123\n'
            'OPENAI_API_KEY="sk-quoted"\n'
            "export GOOGLE_API_KEY='AIza-single'\n"
            'DEEPSEEK_API_KEY=sk-extra\n'
            '# CLAUDE_MODEL=commented-out\n'
            'CLAUDE_MODEL=claude-opus-5\n'
            'MAX_OUTPUT_TOKENS=96000\n'
            '\n'
            'LOG_LEVEL=INFO\n'
        )
        f = self.write(original)
        models.migrate_env_file(f, fill_defaults=True)
        after = f.read_text().splitlines()
        kept = [ln for ln in original.splitlines() if not ln.startswith("CLAUDE_MODEL=")]
        for ln in kept:
            self.assertIn(ln, after)

    def test_failed_write_leaves_original_and_no_temp_file(self):
        f = self.write(self.SHARED)
        with mock.patch.object(Path, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                models.migrate_env_file(f, fill_defaults=True)
        self.assertEqual(f.read_text(), self.SHARED)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), [".env"])

    def test_idempotent(self):
        f = self.write(self.SHARED)
        models.migrate_env_file(f, fill_defaults=True)
        first = f.read_text()
        self.assertEqual(models.migrate_env_file(f, fill_defaults=True), [])
        self.assertEqual(f.read_text(), first)

    def test_shared_fills_missing_roles(self):
        f = self.write("ANTHROPIC_API_KEY=a\nCLAUDE_MODEL=claude-x\n")
        models.migrate_env_file(f, fill_defaults=True)
        text = f.read_text()
        self.assertIn("IMPLEMENTATION_MODEL=claude-x", text)
        self.assertIn("QUALITY_MODEL=gpt-5.6-sol", text)
        self.assertIn("SECURITY_MODEL=gemini-3.6-flash", text)

    def test_project_converts_only_what_it_has(self):
        f = self.write("# Project-local .env\nCLAUDE_MODEL=claude-y\n# GPT_MODEL=commented\nLOG_LEVEL=DEBUG\n")
        changes = models.migrate_env_file(f, fill_defaults=False)
        self.assertEqual(len(changes), 1)
        text = f.read_text()
        self.assertIn("IMPLEMENTATION_PROVIDER=anthropic\nIMPLEMENTATION_MODEL=claude-y", text)
        self.assertNotIn("QUALITY_", text)
        self.assertIn("# GPT_MODEL=commented", text)
        self.assertIn("LOG_LEVEL=DEBUG", text)

    def test_dead_legacy_line_removed_when_role_configured(self):
        f = self.write("QUALITY_PROVIDER=openai\nQUALITY_MODEL=gpt-z\nGPT_MODEL=gpt-old\n")
        changes = models.migrate_env_file(f, fill_defaults=False)
        self.assertIn("removed unused", changes[0])
        self.assertNotIn("GPT_MODEL", f.read_text())
        self.assertIn("QUALITY_MODEL=gpt-z", f.read_text())

    def test_project_env_migrated_and_reloaded_before_resolution(self):
        import orchestrate
        project = self.dir / "project"
        project.mkdir()
        (project / ".env").write_text("CLAUDE_MODEL=claude-project\n")
        os.environ.update(IMPLEMENTATION_PROVIDER="anthropic", IMPLEMENTATION_MODEL="claude-shared")
        with mock.patch.object(orchestrate, "PROJECT_ROOT", project):
            orchestrate.migrate_project_env()
        self.assertEqual(models.resolve_role("implementation").model, "claude-project")


class TestProjectPromptMigration(ProjectTestCase):
    def repo(self):
        from git import Repo
        repo = Repo.init(self.root)
        with repo.config_writer() as cw:
            cw.set_value("user", "name", "t")
            cw.set_value("user", "email", "t@example.com")
        return repo

    def test_tracked_prompt_renamed_and_committed(self):
        repo = self.repo()
        self.put("prompts/claude_coder.md", "{brand_context}\ncustom")
        repo.index.add(["prompts/claude_coder.md"])
        repo.index.commit("scaffold")
        self.put("prompts/claude_coder.md", "{brand_context}\ncustom, edited")  # uncommitted edit
        self.o.migrate_project_prompts(repo)
        self.assertEqual(repo.git.ls_files().splitlines(), ["prompts/implementation.md"])
        self.assertEqual(repo.head.commit.message, "chore: rename prompt files to role names")
        self.assertEqual((self.root / "prompts/implementation.md").read_text(),
                         "{brand_context}\ncustom, edited")
        self.assertFalse((self.root / "prompts/claude_coder.md").exists())

    def test_untracked_prompt_renamed_without_commit(self):
        repo = self.repo()
        self.put("README", "x")
        repo.index.add(["README"])
        repo.index.commit("init")
        self.put("prompts/gpt_reviewer.md", "q")
        self.o.migrate_project_prompts(repo)
        self.assertTrue((self.root / "prompts/quality.md").exists())
        self.assertEqual(repo.head.commit.message, "init")

    def test_both_names_left_alone(self):
        repo = self.repo()
        self.put("prompts/claude_final.md", "old")
        self.put("prompts/synthesis.md", "new")
        self.o.migrate_project_prompts(repo)
        self.assertEqual((self.root / "prompts/claude_final.md").read_text(), "old")
        self.assertEqual(self.o.load_prompt("synthesis"), "new")

    def test_nothing_to_do(self):
        repo = self.repo()
        self.put("README", "x")
        repo.index.add(["README"])
        repo.index.commit("init")
        self.o.migrate_project_prompts(repo)
        self.assertEqual(repo.head.commit.message, "init")


class TestReviewFiles(ProjectTestCase):
    def test_role_names(self):
        self.assertEqual(self.o.review_path("quality").name, "review-quality.md")
        self.assertEqual(self.o.review_path("security").name, "review-security.md")

    def test_new_name_wins_over_legacy(self):
        self.put("reviews/review-gpt.md", "old")
        self.put("reviews/review-quality.md", "new")
        self.assertEqual(self.o.read_review("quality"), "new")

    def test_legacy_fallback_for_old_branches(self):
        self.put("reviews/review-gemini.md", "old security")
        self.assertEqual(self.o.read_review("security"), "old security")

    def test_new_review_retires_legacy_file_in_the_commit(self):
        from git import Repo
        repo = Repo.init(self.root)
        with repo.config_writer() as cw:
            cw.set_value("user", "name", "t")
            cw.set_value("user", "email", "t@example.com")
        self.put("reviews/review-gpt.md", "old")
        repo.index.add(["reviews/review-gpt.md"])
        repo.index.commit("old run")
        self.o.RETIRED_REVIEWS.clear()
        self.o.write_file(self.o.review_path("quality"), "new")
        self.o.retire_legacy_review("quality")
        self.o.retire_legacy_review("security")  # nothing to retire: no-op
        self.o.git_commit(repo, "review", [self.root / "reviews"], removed=self.o.RETIRED_REVIEWS)
        tracked = repo.git.ls_files().splitlines()
        self.assertEqual(tracked, ["reviews/review-quality.md"])
        self.assertFalse(repo.is_dirty(untracked_files=True))

    def test_untracked_legacy_file_is_just_deleted(self):
        from git import Repo
        repo = Repo.init(self.root)
        with repo.config_writer() as cw:
            cw.set_value("user", "name", "t")
            cw.set_value("user", "email", "t@example.com")
        self.put("reviews/review-gemini.md", "old")
        self.o.RETIRED_REVIEWS.clear()
        self.o.write_file(self.o.review_path("security"), "new")
        self.o.retire_legacy_review("security")
        self.o.git_commit(repo, "review", [self.root / "reviews"], removed=self.o.RETIRED_REVIEWS)
        self.assertEqual(repo.git.ls_files().splitlines(), ["reviews/review-security.md"])

    def test_missing_review_says_where_to_resume(self):
        with self.assertRaisesRegex(FileNotFoundError, "--from-stage 2"):
            self.o.read_review("quality")


if __name__ == "__main__":
    unittest.main()

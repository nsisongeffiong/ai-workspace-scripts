#!/usr/bin/env python3
"""
Multi-model pipeline orchestrator -- cloud edition.

Stage 1: implementation role -- initial implementation
Stage 2: quality role        -- code quality & documentation review
Stage 3: security role       -- security & correctness audit
Stage 4: implementation role -- final synthesis and corrections

Each role's provider and model are configured in .env (see .env.example and
models.py). Provider SDK calls live in models.py; this file never imports them.

Usage:
  PROJECT_ROOT=/path/to/project python orchestrate.py "task description"
  or via the project wrapper:
  python scripts/run.py "task description"

Resume from a specific stage (skips earlier stages):
  python scripts/run.py --from-stage 3 "task description"
  python scripts/run.py --from-stage 4 "task description"
"""
import os, sys, logging, time, re, posixpath
from pathlib import Path

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", Path.cwd())).resolve()

from dotenv import load_dotenv
SHARED_DIR = Path(__file__).parent
load_dotenv(SHARED_DIR / ".env")
load_dotenv(PROJECT_ROOT / ".env", override=True)

from git import Repo, InvalidGitRepositoryError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from models import (
    ConfigError, GenerationRequest, RateLimited, build_client, describe, resolve_role,
    validate_unique_models,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pipeline")

MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))

# Populated by init_roles() at the start of run(), after CLI parsing.
ROLE_CONFIG: dict = {}
CLIENTS: dict = {}


def init_roles(from_stage: int) -> None:
    """Resolve all role configs, then build clients for the stages that will run.

    Config errors and missing keys fail here, before any branch is created or
    any model is called.
    """
    ROLE_CONFIG.clear()
    CLIENTS.clear()
    for role in ("implementation", "quality", "security"):
        ROLE_CONFIG[role] = resolve_role(role)
    validate_unique_models(ROLE_CONFIG)
    needed = ["implementation"]
    if from_stage <= 2:
        needed.append("quality")
    if from_stage <= 3:
        needed.append("security")
    for role in needed:
        CLIENTS[role] = build_client(ROLE_CONFIG[role])
        log.info("Role %-14s %s", role + ":", describe(ROLE_CONFIG[role]))


def _fmt(n) -> str:
    return "?" if n is None else str(n)


@retry(stop=stop_after_attempt(MAX_RETRIES),
       wait=wait_exponential(multiplier=2, min=4, max=60),
       retry=retry_if_exception_type(RateLimited),
       reraise=True)
def generate(role: str, request: GenerationRequest):
    """Call a role's model. Only rate limits are retried; nothing else happens here."""
    result = CLIENTS[role].generate(request)
    log.info("  Tokens in/out: %s / %s", _fmt(result.input_tokens), _fmt(result.output_tokens))
    if result.truncated:
        log.warning("  Output hit the %s token ceiling and may be incomplete", request.max_tokens)
    return result


# Prompt files are named after roles. The provider-era names are still read so
# existing workspaces and project-level overrides keep working.
LEGACY_PROMPT_NAMES = {
    "implementation": "claude_coder",
    "quality":        "gpt_reviewer",
    "security":       "gemini_validator",
    "synthesis":      "claude_final",
}


def find_prompt(name: str):
    """Return the prompt file to use for a stage, or None.

    Lookup order: project new name, project legacy name, shared new name,
    shared legacy name -- so a project override always beats the shared copy.
    """
    names = [name] + ([LEGACY_PROMPT_NAMES[name]] if name in LEGACY_PROMPT_NAMES else [])
    for search in [PROJECT_ROOT / "prompts", SHARED_DIR / "prompts"]:
        for n in names:
            p = search / f"{n}.md"
            if p.exists():
                if n != name:
                    log.info("  Prompt: using legacy name %s (rename to %s.md)", p, name)
                return p
    return None


def load_prompt(name: str) -> str:
    """Load prompt, injecting brand context only if the placeholder is present.

    Prefers BRAND_TOKENS.md (distilled) over BRAND.md (full) when available.
    Prompts opt in to brand context by including {brand_context} in their text.
    This keeps token usage proportional to what each stage actually needs.
    """
    p = find_prompt(name)
    if p is None:
        raise FileNotFoundError(f"Prompt '{name}.md' not found")
    text = p.read_text(encoding="utf-8")
    if "{brand_context}" in text:
        # Prefer distilled tokens file -- falls back to full brand guide
        tokens_path = PROJECT_ROOT / "prompts" / "BRAND_TOKENS.md"
        brand_path  = PROJECT_ROOT / "prompts" / "BRAND.md"
        if tokens_path.exists():
            brand = tokens_path.read_text(encoding="utf-8")
            log.debug("  Brand context: using BRAND_TOKENS.md (%d chars)", len(brand))
        elif brand_path.exists():
            brand = brand_path.read_text(encoding="utf-8")
            log.debug("  Brand context: using BRAND.md (%d chars)", len(brand))
        else:
            brand = ""
        text = text.replace("{brand_context}", brand)
    return text


def check_brand_budget() -> None:
    """Warn if brand context is large enough to crowd the token budget."""
    brand_path = PROJECT_ROOT / "prompts" / "BRAND.md"
    if not brand_path.exists():
        return
    size = len(brand_path.read_text(encoding="utf-8"))
    tokens_approx = size // 4
    if tokens_approx > 2000:
        log.warning(
            "Brand context is ~%d tokens -- this leaves only ~%d tokens for code output. "
            "Consider splitting your task into smaller focused pipeline runs, or trimming "
            "the brand guide to essential design tokens only (colours, fonts, spacing).",
            tokens_approx, ROLE_CONFIG["implementation"].max_output_tokens - tokens_approx
        )

def read_src() -> str:
    """Concatenate all project source and config files for review stages.

    Only reads files that were written or modified by the pipeline —
    determined by diffing against the initial scaffold commit.
    Falls back to reading all src/ files if git history is unavailable.
    """
    ROOT_INCLUDE = {
        "package.json", "tsconfig.json", "tsconfig.node.json",
        "next.config.ts", "next.config.js", "next.config.mjs",
        "postcss.config.mjs", "postcss.config.js",
        "tailwind.config.ts", "tailwind.config.js",
        "vite.config.ts", "vite.config.js",
        "go.mod", "pyproject.toml",
    }

    try:
        repo = Repo(PROJECT_ROOT)
        # Get files changed since the initial scaffold commit
        initial = repo.git.rev_list("--max-parents=0", "HEAD").strip().splitlines()[-1]
        changed = set(repo.git.diff("--name-only", initial, "HEAD").strip().splitlines())
        # Include only changed files that exist on disk
        pipeline_files = sorted(
            PROJECT_ROOT / f for f in changed
            if (PROJECT_ROOT / f).is_file()
            and ".git" not in (PROJECT_ROOT / f).parts
        )
        if pipeline_files:
            return "\n\n".join(
                f"### {f.relative_to(PROJECT_ROOT)}\n"
                f"```\n{f.read_text(encoding='utf-8', errors='replace')}\n```"
                for f in pipeline_files
            )
    except Exception:
        pass

    # Fallback: read root config files + all of src/
    root_files = sorted(
        f for f in PROJECT_ROOT.iterdir()
        if f.is_file() and f.name in ROOT_INCLUDE
    )
    src_root = PROJECT_ROOT / "src"
    src_files = sorted(f for f in src_root.rglob("*") if f.is_file()) \
                if src_root.exists() else []
    all_files = root_files + src_files
    if not all_files:
        return "(no source files found)"
    return "\n\n".join(
        f"### {f.relative_to(PROJECT_ROOT)}\n"
        f"```\n{f.read_text(encoding='utf-8', errors='replace')}\n```"
        for f in all_files
    )


_BLOCKED_TARGETS = {".git", ".env", ".env.local", ".env.production"}


# -- Task scope allowlist -----------------------------------------------------
#
# A stage may write files the task never asked for.
# A task can declare which files may be written with a line such as:
#
#   SCOPE: src/api/client.ts, src/api/types.ts
#
# The allowlist is enforced where model-supplied paths are resolved, so it
# covers Stages 1 and 4. No SCOPE line means no restriction.

_ALLOWED_PATHS = None


def _normalise_rel(raw: str):
    """Project-relative POSIX form of a path, or None if it leaves the project."""
    rel = posixpath.normpath(raw.strip().replace("\\", "/"))
    if rel.startswith("/") or rel == ".." or rel.startswith("../") or rel == ".":
        return None
    return rel


def set_task_scope(task: str) -> None:
    global _ALLOWED_PATHS
    m = re.search(r"^\s*SCOPE:\s*(.+)$", task, re.MULTILINE)
    if not m:
        _ALLOWED_PATHS = None
        log.info("  Scope: unrestricted")
        return
    allowed = set()
    for entry in (x.strip() for x in m.group(1).split(",")):
        if not entry:
            continue
        rel = _normalise_rel(entry)
        if rel is None:
            log.warning("  Scope: ignoring entry outside the project: %s", entry)
            continue
        allowed.add(rel)
    _ALLOWED_PATHS = allowed
    log.info("  Scope: %d writable -- %s", len(allowed), ", ".join(sorted(allowed)) or "(none)")


def _safe_path(raw: str):
    """Resolve a model-supplied path; return None if unsafe or out of scope."""
    if Path(raw).is_absolute():
        log.warning("  Blocked absolute path from model output: %s", raw)
        return None
    resolved = (PROJECT_ROOT / raw).resolve()
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        log.warning("  Blocked path traversal from model output: %s", raw)
        return None
    for part in resolved.parts:
        if part in _BLOCKED_TARGETS:
            log.warning("  Blocked sensitive target from model output: %s", raw)
            return None
    if _ALLOWED_PATHS is not None:
        key = resolved.relative_to(PROJECT_ROOT.resolve()).as_posix()
        if key not in _ALLOWED_PATHS:
            log.warning("  REFUSED out-of-scope write: %s", key)
            return None
    return resolved


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    log.info("  Wrote %s (%d chars)", path.relative_to(PROJECT_ROOT), len(content))


def extract_code_blocks(text: str) -> list:
    """Parse labelled code blocks, validate paths, and write files to disk.

    Falls back to src/generated.py only if no labelled file block is found at
    all. Blocks that were found but refused (unsafe or out of scope) never
    trigger the fallback, and the fallback path passes the same checks.
    Returns the list of Path objects actually written.
    """
    LANGS = r"tsx?|jsx?|typescript|javascript|css|scss|html|python|go|json|yaml|sh|bash|mjs|cjs"
    EXT   = r"tsx?|jsx?|css|scss|html|py|go|json|yaml|sh|md|mjs|cjs|toml|txt|env"

    written = []
    matched = False

    # Pattern 1: path on the fence line  e.g. ```tsx src/app/page.tsx
    pattern1 = re.compile(
        rf"```(?:{LANGS})\s+([\w./\-]+\.(?:{EXT}))\n(.*?)```",
        re.DOTALL
    )
    for m in pattern1.finditer(text):
        matched = True
        path = _safe_path(m.group(1))
        if path:
            write_file(path, m.group(2))
            written.append(path)

    # Pattern 2: path as first-line comment  e.g. ```tsx\n// src/app/page.tsx
    if not matched:
        pattern2 = re.compile(
            rf"```(?:{LANGS})\n(?://\s*|#\s*)?([\w./\-]+\.(?:{EXT}))\n(.*?)```",
            re.DOTALL
        )
        for m in pattern2.finditer(text):
            matched = True
            path = _safe_path(m.group(1))
            if path:
                write_file(path, m.group(2))
                written.append(path)

    if not matched:
        fallback = _safe_path("src/generated.py")
        if fallback:
            write_file(fallback, text)
            written.append(fallback)
    elif not written:
        log.warning("  No files written: every file block was refused")

    return written


def install_dependencies() -> None:
    """Run package manager install if a manifest was written this stage."""
    import subprocess

    pkg_json = PROJECT_ROOT / "package.json"
    req_txt  = PROJECT_ROOT / "requirements.txt"
    pyproject = PROJECT_ROOT / "pyproject.toml"

    if pkg_json.exists():
        log.info("  Installing Node dependencies...")
        r = subprocess.run(["npm", "install", "--prefer-offline"], cwd=PROJECT_ROOT, capture_output=True, text=True)
        if r.returncode == 0:
            log.info("  npm install: OK")
        else:
            log.warning("  npm install failed:\n%s", r.stderr.strip())

    if req_txt.exists() or pyproject.exists():
        venv_pip = SHARED_DIR / ".venv" / "bin" / "pip"
        manifest = req_txt if req_txt.exists() else pyproject
        args = [str(venv_pip), "install", "-q", "-r", str(manifest)] if req_txt.exists() \
               else [str(venv_pip), "install", "-q", "-e", str(PROJECT_ROOT)]
        log.info("  Installing Python dependencies...")
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode == 0:
            log.info("  pip install: OK")
        else:
            log.warning("  pip install failed:\n%s", r.stderr.strip())


def git_commit(repo: Repo, message: str, paths: list) -> None:
    rel = [str(p.relative_to(PROJECT_ROOT)) for p in paths if p.exists()]
    if not rel:
        return
    repo.index.add(rel)
    try:
        repo.index.commit(message)
        log.info("  Git: %s", message)
    except Exception as e:
        log.error("  Git commit failed: %s", e)
        raise


# -- Stage 1: implementation ------------------------------------------------

def stage_1_implement(task: str):
    cfg = ROLE_CONFIG["implementation"]
    log.info("Stage 1 -- %s: initial implementation", cfg.model)
    result = generate("implementation", GenerationRequest(
        system=load_prompt("implementation"),
        user=task,
        max_tokens=cfg.max_output_tokens,
        effort=cfg.reasoning_effort,
        thinking=True,  # honoured by the anthropic adapter; ignored by others
    ))
    written = extract_code_blocks(result.text)
    install_dependencies()
    return result.text, written


# -- Stage 2: quality review --------------------------------------------------

def stage_2_quality_review() -> str:
    cfg = ROLE_CONFIG["quality"]
    log.info("Stage 2 -- %s: code quality & documentation review", cfg.model)
    result = generate("quality", GenerationRequest(
        system=load_prompt("quality"),
        user=read_src(),
        max_tokens=cfg.max_output_tokens,
        effort=cfg.reasoning_effort,
    ))
    write_file(PROJECT_ROOT / "reviews" / "review-gpt.md", result.text)
    return result.text


# -- Stage 3: security audit --------------------------------------------------

def stage_3_security_audit() -> str:
    cfg = ROLE_CONFIG["security"]
    log.info("Stage 3 -- %s: security & correctness audit", cfg.model)
    result = generate("security", GenerationRequest(
        system=load_prompt("security"),
        user=read_src(),
        max_tokens=cfg.max_output_tokens,
        effort=cfg.reasoning_effort,
    ))
    write_file(PROJECT_ROOT / "reviews" / "review-gemini.md", result.text)
    return result.text


# -- Stage 4: synthesis -------------------------------------------------------

def stage_4_synthesize(stage1_output: str = "") -> list:
    cfg = ROLE_CONFIG["implementation"]
    log.info("Stage 4 -- %s: final synthesis", cfg.model)
    # Review filenames are kept from the original three-provider layout so
    # existing branches resume cleanly; they hold the quality and security reviews.
    quality_fb  = (PROJECT_ROOT / "reviews" / "review-gpt.md").read_text(encoding="utf-8")
    security_fb = (PROJECT_ROOT / "reviews" / "review-gemini.md").read_text(encoding="utf-8")
    context = (
        "<review_content>\n"
        f"## Quality review ({ROLE_CONFIG['quality'].model})\n{quality_fb}\n\n"
        f"## Security review ({ROLE_CONFIG['security'].model})\n{security_fb}\n"
        "</review_content>\n\n"
        f"## Source Code\n{stage1_output if stage1_output else read_src()}"
    )
    result = generate("implementation", GenerationRequest(
        system=load_prompt("synthesis"),
        user=context,
        max_tokens=cfg.max_output_tokens,
        effort=cfg.reasoning_effort,
        thinking=True,  # honoured by the anthropic adapter; ignored by others
    ))
    write_file(PROJECT_ROOT / "reviews" / "final-review.md", result.text)
    written = extract_code_blocks(result.text)
    install_dependencies()
    return written



def distil_brand_tokens() -> None:
    """Use the implementation role to distil BRAND.md into a lean BRAND_TOKENS.md.

    Only runs when BRAND.md exists and BRAND_TOKENS.md does not yet exist.
    The distilled file contains only the tokens needed for consistent UI output:
    colours, typography, spacing, logo paths, and a brief brand voice summary.
    Target: under 100 lines / ~1000 tokens, leaving maximum budget for code.
    """
    brand_path  = PROJECT_ROOT / "prompts" / "BRAND.md"
    tokens_path = PROJECT_ROOT / "prompts" / "BRAND_TOKENS.md"

    if not brand_path.exists():
        return

    if tokens_path.exists():
        tokens_size = len(tokens_path.read_text(encoding="utf-8"))
        log.info("Pre-flight: BRAND_TOKENS.md already exists (%d chars) -- skipping distillation", tokens_size)
        return

    brand_content = brand_path.read_text(encoding="utf-8")
    brand_tokens  = len(brand_content) // 4
    log.info(
        "Pre-flight: Distilling brand guide (%d chars / ~%d tokens) into BRAND_TOKENS.md...",
        len(brand_content), brand_tokens
    )

    prompt = f"""You are a design systems engineer. Extract only the essential design tokens
from this brand guide into a compact BRAND_TOKENS.md file for use as AI pipeline context.

The output must be under 100 lines and contain ONLY:
1. Brand name and one-line description
2. Colour palette -- exact hex values with semantic names (primary, secondary, background, text, accent, etc.)
3. Typography -- font family names, weights, and size scale
4. Spacing scale (if defined)
5. Logo file paths or URLs (exact paths as specified in the brand guide)
6. Component library or CSS framework name (if specified)
7. Brand voice -- maximum 3 sentences describing tone and personality
8. Any critical DO / DO NOT rules (maximum 5 bullet points)

Format as clean markdown with concise sections. No narrative. No explanations.
Every colour must include its exact hex value. Every font must include its exact name.

BRAND GUIDE:
{brand_content}

Return ONLY the BRAND_TOKENS.md content -- no preamble, no explanation."""

    try:
        # Pre-flight calls are not retried; failures degrade to the full BRAND.md.
        result = CLIENTS["implementation"].generate(GenerationRequest(
            system=None, user=prompt, max_tokens=8000,
            effort=ROLE_CONFIG["implementation"].preflight_effort,
        ))
        tokens_content = result.text.strip()
        if not tokens_content:
            raise ValueError("empty response")
        tokens_path.write_text(tokens_content, encoding="utf-8")
        distilled_tokens = len(tokens_content) // 4
        saved = brand_tokens - distilled_tokens
        log.info(
            "Pre-flight: BRAND_TOKENS.md written (%d chars / ~%d tokens) -- saved ~%d tokens vs full brand guide",
            len(tokens_content), distilled_tokens, saved
        )
    except Exception as e:
        log.warning("Pre-flight: Brand distillation failed (%s) -- will use full BRAND.md", e)


# -- Pre-pipeline: brand asset placement -------------------------------------

def setup_brand_assets() -> None:
    """Ask the implementation role where brand assets should go, then copy them.

    Only runs when a non-empty brand/ submodule exists.
    The model inspects the project structure and returns a JSON placement map,
    so this works for any language or framework without hardcoded rules.
    """
    import json, shutil

    distil_brand_tokens()
    check_brand_budget()

    brand_dir = PROJECT_ROOT / "brand"
    if not brand_dir.exists() or not any(brand_dir.iterdir()):
        return

    # Build a compact project snapshot for Claude to reason about
    structure = "\n".join(
        str(p.relative_to(PROJECT_ROOT))
        for p in sorted(PROJECT_ROOT.rglob("*"))
        if p.is_file()
        and ".git" not in p.parts
        and "node_modules" not in p.parts
        and "brand" not in p.parts
    ) or "(empty project)"

    brand_files = "\n".join(
        str(p.relative_to(brand_dir))
        for p in sorted(brand_dir.rglob("*"))
        if p.is_file() and ".git" not in p.parts
    )

    prompt = (
        "You are a build engineer. Given the project structure below, determine "
        "where brand assets should be copied so the web framework can serve them.\n\n"
        f"Project files:\n{structure}\n\n"
        f"Brand asset files (relative to brand/):\n{brand_files}\n\n"
        "Return ONLY a JSON object mapping each brand asset subdirectory to its "
        "destination path relative to the project root. Example:\n"
        '{"logo": "public/brand/logo", "fonts": "public/brand/fonts", "tokens": "public/brand/tokens"}\n\n'
        "If no static serving directory exists or cannot be determined, return {}.\n"
        "Return ONLY the JSON object — no explanation, no markdown."
    )

    log.info("Pre-flight: determining brand asset placement...")
    try:
        result = CLIENTS["implementation"].generate(GenerationRequest(
            system=None, user=prompt, max_tokens=4000,
            effort=ROLE_CONFIG["implementation"].preflight_effort,
        ))
        raw = result.text.strip()
        placement = json.loads(raw)
    except Exception as e:
        log.warning("  Brand placement check failed (%s) -- skipping asset copy", e)
        return

    if not placement:
        log.info("  No static serving directory detected -- skipping asset copy")
        return

    for src_subdir, dest_path in placement.items():
        src = brand_dir / src_subdir
        dest = PROJECT_ROOT / dest_path
        if not src.exists():
            continue
        dest.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if f.is_file():
                shutil.copy2(f, dest / f.name)
        log.info("  Copied brand/%s -> %s (%d files)", src_subdir, dest_path,
                 sum(1 for _ in src.iterdir() if _.is_file()))


# -- Main pipeline ------------------------------------------------------------

def run(task: str, from_stage: int = 1) -> None:
    start = time.monotonic()
    log.info("Project: %s", PROJECT_ROOT.name)
    log.info("Task:    %s", task[:100])

    try:
        init_roles(from_stage)
    except ConfigError as e:
        log.error("Configuration error: %s", e)
        sys.exit(1)

    try:
        repo = Repo(PROJECT_ROOT)
    except InvalidGitRepositoryError:
        log.error("Not a git repository: %s", PROJECT_ROOT)
        sys.exit(1)

    if from_stage > 1:
        # Resume on the current branch -- do not create a new one
        branch = repo.active_branch.name
        log.info("Resuming from stage %d on branch: %s", from_stage, branch)
    else:
        branch = f"feature/{int(time.time())}"
        repo.git.checkout("-b", branch)
        log.info("Branch:  %s", branch)

    set_task_scope(task)

    stage1_output = ""
    if from_stage <= 1:
        setup_brand_assets()
        stage1_output, stage1_written = stage_1_implement(task)
        git_commit(repo, "feat(implementation): initial implementation", stage1_written)

    if from_stage <= 2:
        stage_2_quality_review()

    if from_stage <= 3:
        stage_3_security_audit()

    if from_stage <= 2 or from_stage == 3:
        git_commit(repo, f"review: {ROLE_CONFIG['quality'].model} and "
                         f"{ROLE_CONFIG['security'].model} feedback", [PROJECT_ROOT / "reviews"])

    if from_stage <= 4:
        stage4_written = stage_4_synthesize(stage1_output)
        git_commit(repo, "review(synthesis): final synthesis", [
            PROJECT_ROOT / "reviews" / "final-review.md",
            *stage4_written,
        ])

    log.info("Done in %.1fs | branch: %s", time.monotonic() - start, branch)
    log.info("Next: open a PR from '%s' -> main when satisfied.", branch)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f'Usage: PROJECT_ROOT=/path python {Path(__file__).name} "task"')
        print(f'       PROJECT_ROOT=/path python {Path(__file__).name} --from-stage 3 "task"')
        sys.exit(1)

    # Parse --from-stage N before the task string
    args = sys.argv[1:]
    from_stage = 1
    if "--from-stage" in args:
        idx = args.index("--from-stage")
        try:
            from_stage = int(args[idx + 1])
            if from_stage not in (1, 2, 3, 4):
                raise ValueError
        except (IndexError, ValueError):
            print("ERROR: --from-stage requires a value between 1 and 4")
            sys.exit(1)
        args = args[:idx] + args[idx + 2:]  # remove --from-stage and its value

    if not args:
        print("ERROR: task description is required")
        sys.exit(1)

    run(" ".join(args), from_stage=from_stage)

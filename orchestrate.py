#!/usr/bin/env python3
"""
Multi-model pipeline orchestrator -- cloud edition.

Stage 1: Claude (CLAUDE_MODEL) -- initial implementation
Stage 2: GPT    (GPT_MODEL)    -- code quality & documentation review
Stage 3: Gemini (GEMINI_MODEL) -- security & correctness audit
Stage 4: Claude (CLAUDE_MODEL) -- final synthesis and corrections

Models are configured via environment variables in .env (see .env.example).

Usage:
  PROJECT_ROOT=/path/to/project python orchestrate.py "task description"
  or via the project wrapper:
  python scripts/run.py "task description"

Resume from a specific stage (skips earlier stages):
  python scripts/run.py --from-stage 3 "task description"
  python scripts/run.py --from-stage 4 "task description"
"""
import os, sys, logging, time, re
from pathlib import Path

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", Path.cwd())).resolve()

from dotenv import load_dotenv
SHARED_DIR = Path(__file__).parent
load_dotenv(SHARED_DIR / ".env")
load_dotenv(PROJECT_ROOT / ".env", override=True)

import anthropic
import openai
from google import genai as google_genai
from google.genai import types as genai_types
from git import Repo, InvalidGitRepositoryError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pipeline")

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-7")
GPT_MODEL    = os.getenv("GPT_MODEL",    "gpt-5.5")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
MAX_RETRIES       = int(os.getenv("MAX_RETRIES", "3"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "20000"))


def _require_env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(
            f"Missing environment variable: {key}\n"
            f"Check {SHARED_DIR}/.env or {PROJECT_ROOT}/.env"
        )
    return val


# Initialise clients
claude_client = anthropic.Anthropic(api_key=_require_env("ANTHROPIC_API_KEY"))
openai_client = openai.OpenAI(api_key=_require_env("OPENAI_API_KEY"))
gemini_client = google_genai.Client(api_key=_require_env("GOOGLE_API_KEY"))


def load_prompt(name: str) -> str:
    """Load prompt, injecting brand context only if the placeholder is present.

    Prefers BRAND_TOKENS.md (distilled) over BRAND.md (full) when available.
    Prompts opt in to brand context by including {brand_context} in their text.
    This keeps token usage proportional to what each stage actually needs.
    """
    for search in [PROJECT_ROOT / "prompts", SHARED_DIR / "prompts"]:
        p = search / f"{name}.md"
        if p.exists():
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
    raise FileNotFoundError(f"Prompt '{name}.md' not found")


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
            tokens_approx, MAX_OUTPUT_TOKENS - tokens_approx
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


def _safe_path(raw: str):
    """Resolve a model-supplied path; return None if unsafe to write."""
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
    return resolved


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    log.info("  Wrote %s (%d chars)", path.relative_to(PROJECT_ROOT), len(content))


def extract_code_blocks(text: str) -> list:
    """Parse labelled code blocks, validate paths, and write files to disk.

    Falls back to src/generated.py if no labelled filepath is found.
    Returns the list of Path objects actually written.
    """
    LANGS = r"tsx?|jsx?|typescript|javascript|css|scss|html|python|go|json|yaml|sh|bash|mjs|cjs"
    EXT   = r"tsx?|jsx?|css|scss|html|py|go|json|yaml|sh|md|mjs|cjs|toml|txt|env"

    written = []

    # Pattern 1: path on the fence line  e.g. ```tsx src/app/page.tsx
    pattern1 = re.compile(
        rf"```(?:{LANGS})\s+([\w./\-]+\.(?:{EXT}))\n(.*?)```",
        re.DOTALL
    )
    for m in pattern1.finditer(text):
        path = _safe_path(m.group(1))
        if path:
            write_file(path, m.group(2))
            written.append(path)

    # Pattern 2: path as first-line comment  e.g. ```tsx\n// src/app/page.tsx
    if not written:
        pattern2 = re.compile(
            rf"```(?:{LANGS})\n(?://\s*|#\s*)?([\w./\-]+\.(?:{EXT}))\n(.*?)```",
            re.DOTALL
        )
        for m in pattern2.finditer(text):
            path = _safe_path(m.group(1))
            if path:
                write_file(path, m.group(2))
                written.append(path)

    if not written:
        fallback = PROJECT_ROOT / "src" / "generated.py"
        write_file(fallback, text)
        written.append(fallback)

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


# -- Stage 1: Claude codes ----------------------------------------------------

@retry(stop=stop_after_attempt(MAX_RETRIES),
       wait=wait_exponential(multiplier=2, min=4, max=60),
       retry=retry_if_exception_type(anthropic.RateLimitError))
def stage_1_claude_code(task: str) -> str:
    log.info("Stage 1 -- %s: initial implementation", CLAUDE_MODEL)
    msg = claude_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": "xhigh"},
        system=load_prompt("claude_coder"),
        messages=[{"role": "user", "content": task}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    log.info("  Tokens in/out: %d / %d", msg.usage.input_tokens, msg.usage.output_tokens)
    written = extract_code_blocks(text)
    install_dependencies()
    return text, written


# -- Stage 2: GPT review -----------------------------------------------------

@retry(stop=stop_after_attempt(MAX_RETRIES),
       wait=wait_exponential(multiplier=2, min=4, max=60),
       retry=retry_if_exception_type(openai.RateLimitError))
def stage_2_gpt_review() -> str:
    log.info("Stage 2 -- %s: code quality & documentation review", GPT_MODEL)
    resp = openai_client.chat.completions.create(
        model=GPT_MODEL,
        max_completion_tokens=16000,
        timeout=120,
        messages=[
            {"role": "system", "content": load_prompt("gpt_reviewer")},
            {"role": "user",   "content": read_src()},
        ],
    )
    review = resp.choices[0].message.content
    write_file(PROJECT_ROOT / "reviews" / "review-gpt.md", review)
    return review


# -- Stage 3: Gemini validates ------------------------------------------------

def stage_3_gemini_validate() -> str:
    log.info("Stage 3 -- %s: security & correctness audit", GEMINI_MODEL)
    resp = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=read_src(),
        config=genai_types.GenerateContentConfig(
            system_instruction=load_prompt("gemini_validator"),
            max_output_tokens=16000,
        ),
    )
    review = resp.text
    write_file(PROJECT_ROOT / "reviews" / "review-gemini.md", review)
    return review


# -- Stage 4: Claude synthesises ----------------------------------------------

@retry(stop=stop_after_attempt(MAX_RETRIES),
       wait=wait_exponential(multiplier=2, min=4, max=60),
       retry=retry_if_exception_type(anthropic.RateLimitError))
def stage_4_claude_final(stage1_output: str = "") -> list:
    log.info("Stage 4 -- %s: final synthesis", CLAUDE_MODEL)
    gpt_fb    = (PROJECT_ROOT / "reviews" / "review-gpt.md").read_text(encoding="utf-8")
    gemini_fb = (PROJECT_ROOT / "reviews" / "review-gemini.md").read_text(encoding="utf-8")
    context = (
        "<review_content>\n"
        f"## {GPT_MODEL} Review\n{gpt_fb}\n\n"
        f"## {GEMINI_MODEL} Review\n{gemini_fb}\n"
        "</review_content>\n\n"
        f"## Source Code\n{stage1_output if stage1_output else read_src()}"
    )
    msg = claude_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": "xhigh"},
        system=load_prompt("claude_final"),
        messages=[{"role": "user", "content": context}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    log.info("  Tokens in/out: %d / %d", msg.usage.input_tokens, msg.usage.output_tokens)
    write_file(PROJECT_ROOT / "reviews" / "final-review.md", text)
    written = extract_code_blocks(text)
    install_dependencies()
    return written



def distil_brand_tokens() -> None:
    """Use Claude to distil BRAND.md into a lean BRAND_TOKENS.md.

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
        msg = claude_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        tokens_content = "".join(b.text for b in msg.content if b.type == "text").strip()
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
    """Ask Claude where brand assets should go, then copy them there.

    Only runs when a non-empty brand/ submodule exists.
    Claude inspects the project structure and returns a JSON placement map,
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
        msg = claude_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(b.text for b in msg.content if b.type == "text").strip()
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

    stage1_output = ""
    if from_stage <= 1:
        setup_brand_assets()
        stage1_output, stage1_written = stage_1_claude_code(task)
        git_commit(repo, "feat(claude): initial implementation", stage1_written)

    if from_stage <= 2:
        stage_2_gpt_review()

    if from_stage <= 3:
        stage_3_gemini_validate()

    if from_stage <= 2 or from_stage == 3:
        git_commit(repo, f"review: {GPT_MODEL} and {GEMINI_MODEL} feedback", [PROJECT_ROOT / "reviews"])

    if from_stage <= 4:
        stage4_written = stage_4_claude_final(stage1_output)
        git_commit(repo, "review(claude): final synthesis", [
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

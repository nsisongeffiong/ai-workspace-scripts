#!/usr/bin/env python3
"""
Multi-model pipeline orchestrator -- cloud edition.

Stage 1: Claude Opus 4.6  -- initial implementation
Stage 2: GPT-4o           -- code quality & documentation review
Stage 3: Gemini 2.5 Flash -- security & correctness audit
Stage 4: Claude Opus 4.6  -- final synthesis and corrections

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

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-6")
GPT_MODEL    = os.getenv("GPT_MODEL",    "gpt-5.4")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
MAX_RETRIES  = int(os.getenv("MAX_RETRIES", "3"))


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

    Prompts opt in to brand context by including {brand_context} in their text.
    This keeps token usage proportional to what each stage actually needs.
    """
    for search in [PROJECT_ROOT / "prompts", SHARED_DIR / "prompts"]:
        p = search / f"{name}.md"
        if p.exists():
            text = p.read_text(encoding="utf-8")
            if "{brand_context}" in text:
                brand_path = PROJECT_ROOT / "prompts" / "BRAND.md"
                brand = brand_path.read_text(encoding="utf-8") if brand_path.exists() else ""
                text = text.replace("{brand_context}", brand)
            return text
    raise FileNotFoundError(f"Prompt '{name}.md' not found")


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


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    log.info("  Wrote %s (%d chars)", path.relative_to(PROJECT_ROOT), len(content))


def extract_code_blocks(text: str) -> None:
    """Parse labelled code blocks and write files to disk.

    Trusts the filepath the AI provides on the fence line.
    Falls back to src/generated.py if no filepath is found.
    """
    LANGS = r"tsx?|jsx?|typescript|javascript|css|scss|html|python|go|json|yaml|sh|bash|mjs|cjs"
    EXT   = r"tsx?|jsx?|css|scss|html|py|go|json|yaml|sh|md|mjs|cjs|toml|txt|env"

    # Pattern 1: path on the fence line  e.g. ```tsx src/app/page.tsx
    pattern1 = re.compile(
        rf"```(?:{LANGS})\s+([\w./\-]+\.(?:{EXT}))\n(.*?)```",
        re.DOTALL
    )
    written = 0
    for m in pattern1.finditer(text):
        write_file(PROJECT_ROOT / m.group(1), m.group(2))
        written += 1

    # Pattern 2: path as first-line comment  e.g. ```tsx\n// src/app/page.tsx
    if written == 0:
        pattern2 = re.compile(
            rf"```(?:{LANGS})\n(?://\s*|#\s*)?([\w./\-]+\.(?:{EXT}))\n(.*?)```",
            re.DOTALL
        )
        for m in pattern2.finditer(text):
            write_file(PROJECT_ROOT / m.group(1), m.group(2))
            written += 1

    if written == 0:
        write_file(PROJECT_ROOT / "src" / "generated.py", text)


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
    except Exception:
        pass


# -- Stage 1: Claude codes ----------------------------------------------------

@retry(stop=stop_after_attempt(MAX_RETRIES),
       wait=wait_exponential(multiplier=2, min=4, max=60),
       retry=retry_if_exception_type(anthropic.RateLimitError))
def stage_1_claude_code(task: str) -> str:
    log.info("Stage 1 -- Claude Opus 4.6: initial implementation")
    msg = claude_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=8192,
        system=load_prompt("claude_coder"),
        messages=[{"role": "user", "content": task}],
    )
    text = msg.content[0].text
    log.info("  Tokens in/out: %d / %d", msg.usage.input_tokens, msg.usage.output_tokens)
    extract_code_blocks(text)
    install_dependencies()
    return text


# -- Stage 2: GPT-4o reviews --------------------------------------------------

@retry(stop=stop_after_attempt(MAX_RETRIES),
       wait=wait_exponential(multiplier=2, min=4, max=60),
       retry=retry_if_exception_type(openai.RateLimitError))
def stage_2_gpt_review() -> str:
    log.info("Stage 2 -- GPT-4o: code quality & documentation review")
    resp = openai_client.chat.completions.create(
        model=GPT_MODEL,
        max_completion_tokens=4096,
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
    log.info("Stage 3 -- Gemini 2.5 Flash: security & correctness audit")
    resp = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=read_src(),
        config=genai_types.GenerateContentConfig(
            system_instruction=load_prompt("gemini_validator"),
            max_output_tokens=4096,
        ),
    )
    review = resp.text
    write_file(PROJECT_ROOT / "reviews" / "review-gemini.md", review)
    return review


# -- Stage 4: Claude synthesises ----------------------------------------------

@retry(stop=stop_after_attempt(MAX_RETRIES),
       wait=wait_exponential(multiplier=2, min=4, max=60),
       retry=retry_if_exception_type(anthropic.RateLimitError))
def stage_4_claude_final() -> str:
    log.info("Stage 4 -- Claude Opus 4.6: final synthesis")
    gpt_fb    = (PROJECT_ROOT / "reviews" / "review-gpt.md").read_text(encoding="utf-8")
    gemini_fb = (PROJECT_ROOT / "reviews" / "review-gemini.md").read_text(encoding="utf-8")
    context = (
        "<review_content>\n"
        f"## GPT-4o Review\n{gpt_fb}\n\n"
        f"## Gemini 2.5 Flash Review\n{gemini_fb}\n"
        "</review_content>\n\n"
        f"## Source Code\n{read_src()}"
    )
    msg = claude_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=8192,
        system=load_prompt("claude_final"),
        messages=[{"role": "user", "content": context}],
    )
    text = msg.content[0].text
    log.info("  Tokens in/out: %d / %d", msg.usage.input_tokens, msg.usage.output_tokens)
    write_file(PROJECT_ROOT / "reviews" / "final-review.md", text)
    extract_code_blocks(text)
    install_dependencies()
    return text


# -- Pre-pipeline: brand asset placement -------------------------------------

def setup_brand_assets() -> None:
    """Ask Claude where brand assets should go, then copy them there.

    Only runs when a non-empty brand/ submodule exists.
    Claude inspects the project structure and returns a JSON placement map,
    so this works for any language or framework without hardcoded rules.
    """
    import json, shutil

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
        raw = msg.content[0].text.strip()
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

    if from_stage <= 1:
        setup_brand_assets()
        stage_1_claude_code(task)
        git_commit(repo, "feat(claude): initial implementation", [PROJECT_ROOT / "src"])

    if from_stage <= 2:
        stage_2_gpt_review()

    if from_stage <= 3:
        stage_3_gemini_validate()

    if from_stage <= 2 or from_stage == 3:
        git_commit(repo, "review: gpt-5.4 and gemini feedback", [PROJECT_ROOT / "reviews"])

    if from_stage <= 4:
        stage_4_claude_final()
        git_commit(repo, "review(claude): final synthesis", [
            PROJECT_ROOT / "reviews" / "final-review.md",
            PROJECT_ROOT / "src",
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

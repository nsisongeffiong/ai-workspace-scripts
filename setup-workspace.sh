#!/usr/bin/env bash
# =============================================================================
#  Multi-Model Agentic Dev Environment -- ONE-TIME WORKSPACE SETUP
#  Cloud edition: Claude (Anthropic) | GPT (OpenAI) | Gemini (Google)
#
#  Run this ONCE inside WSL2:  chmod +x setup-workspace.sh && ./setup-workspace.sh
#  Resume after a failure:     ./setup-workspace.sh
#  Start completely fresh:     ./setup-workspace.sh --reset
# =============================================================================

set -euo pipefail
IFS=$'\n\t'

RED='\033[0;31m';  GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m';  BOLD='\033[1m';  RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
step()    { echo -e "\n${BOLD}${BLUE}---  $*  ---${RESET}\n"; }

confirm() {
  while true; do
    read -rp "$(echo -e "${YELLOW}[?]${RESET} ${1:-Continue?} [y/n]: ")" yn
    case "$yn" in [Yy]*) return 0;; [Nn]*) return 1;; *) echo "y or n";; esac
  done
}

read_secret() {
  local prompt="$1" varname="$2" value=""
  echo -ne "${YELLOW}[SECRET]${RESET} ${prompt}: "
  read -rs value; echo ""
  printf -v "$varname" '%s' "$value"
}

WORKSPACE="$HOME/ai-workspace"
SHARED_DIR="$WORKSPACE/.shared"
PROJECTS_DIR="$WORKSPACE/projects"

# -- Checkpoint system: completed phases are skipped on re-run ----------------
CHECKPOINT_FILE="$HOME/.aiworkspace_setup_progress"

checkpoint_done()  { echo "$1" >> "$CHECKPOINT_FILE"; sort -u "$CHECKPOINT_FILE" -o "$CHECKPOINT_FILE"; }
checkpoint_skip()  { [ -f "$CHECKPOINT_FILE" ] && grep -qx "$1" "$CHECKPOINT_FILE"; }
reset_checkpoints(){ rm -f "$CHECKPOINT_FILE"; echo "Checkpoints cleared -- all phases will re-run."; }

run_phase() {
  local label="$1" fn="$2"
  if checkpoint_skip "$label"; then
    echo "  [SKIP] Already completed: ${label}"
  else
    "$fn"
    checkpoint_done "$label"
  fi
}

# =============================================================================
print_banner() {
  echo -e "${BOLD}${BLUE}"
  echo "  +==============================================================+"
  echo "  |        MULTI-MODEL WORKSPACE -- ONE-TIME SETUP               |"
  echo "  |  Claude Opus 4.6  |  GPT-5.4  |  Gemini 2.5 Flash       |"
  echo "  +==============================================================+"
  echo -e "${RESET}"
  echo -e "  Run this script ${BOLD}once${RESET} per machine. For each new project:"
  echo -e "    ${CYAN}~/new-project.sh <project-name>${RESET}"
  echo ""
  echo -e "  Workspace layout:"
  echo -e "  ${CYAN}~/ai-workspace/${RESET}"
  echo -e "  ${CYAN}  .shared/     ${RESET}<- shared venv, API keys, prompts, orchestrator"
  echo -e "  ${CYAN}  projects/    ${RESET}<- one subfolder per project"
  echo ""
}

# =============================================================================
# PHASE 0 -- Pre-flight checks
# =============================================================================
phase_preflight() {
  step "PHASE 0 -- Pre-flight Checks"
  grep -qi microsoft /proc/version 2>/dev/null || { error "Must run inside WSL2."; exit 1; }
  success "Running inside WSL2"
  [ "$EUID" -eq 0 ] && { error "Do not run as root."; exit 1; }
  success "Running as user: $USER"
  local free_kb; free_kb=$(df -k "$HOME" | awk 'NR==2{print $4}')
  (( free_kb < 5242880 )) \
    && warn "Less than 5 GB free -- consider freeing space" \
    || success "Disk space OK ($(( free_kb/1024/1024 )) GB free)"
}

# =============================================================================
# PHASE 1 -- Collect inputs (always runs -- needed for variables)
# =============================================================================
phase_collect_inputs() {
  step "PHASE 1 -- Your Details & API Keys"

  echo -e "Your API keys will be stored in the system keyring and a gitignored"
  echo -e ".env file. They are ${BOLD}never${RESET} committed to Git.\n"

  read -rp "$(echo -e "${YELLOW}[INPUT]${RESET} Full name (for Git commits): ")" GIT_NAME
  while [ -z "$GIT_NAME" ]; do
    read -rp "$(echo -e "${YELLOW}[INPUT]${RESET} Name cannot be empty: ")" GIT_NAME
  done

  read -rp "$(echo -e "${YELLOW}[INPUT]${RESET} Email (for Git commits): ")" GIT_EMAIL
  while [[ ! "$GIT_EMAIL" =~ ^[^@]+@[^@]+\.[^@]+$ ]]; do
    read -rp "$(echo -e "${YELLOW}[INPUT]${RESET} Invalid email, try again: ")" GIT_EMAIL
  done

  echo ""
  echo -e "  ${CYAN}Anthropic:${RESET} https://console.anthropic.com  -> API Keys"
  echo -e "  ${CYAN}OpenAI:   ${RESET} https://platform.openai.com    -> API Keys"
  echo -e "  ${CYAN}Google:   ${RESET} https://aistudio.google.com    -> Get API key"
  echo ""

  read_secret "Anthropic API key (sk-ant-...)" ANTHROPIC_KEY
  while [ -z "$ANTHROPIC_KEY" ]; do
    warn "Cannot be empty."; read_secret "Anthropic API key" ANTHROPIC_KEY
  done

  read_secret "OpenAI API key (sk-...)" OPENAI_KEY
  while [ -z "$OPENAI_KEY" ]; do
    warn "Cannot be empty."; read_secret "OpenAI API key" OPENAI_KEY
  done

  read_secret "Google API key (AIza...)" GOOGLE_KEY
  while [ -z "$GOOGLE_KEY" ]; do
    warn "Cannot be empty."; read_secret "Google API key" GOOGLE_KEY
  done

  echo ""
  echo -e "${BOLD}Summary${RESET}"
  echo -e "  Git name:      $GIT_NAME"
  echo -e "  Git email:     $GIT_EMAIL"
  echo -e "  Workspace:     $WORKSPACE"
  echo -e "  Anthropic key: ${ANTHROPIC_KEY:0:10}..."
  echo -e "  OpenAI key:    ${OPENAI_KEY:0:10}..."
  echo -e "  Google key:    ${GOOGLE_KEY:0:10}..."
  echo ""
  confirm "Proceed?" || { echo "Cancelled."; exit 0; }
}

# =============================================================================
# PHASE 2 -- System dependencies
# =============================================================================
phase_system_deps() {
  step "PHASE 2 -- System Dependencies"
  info "Updating apt..."
  sudo apt-get update -qq

  info "Installing packages..."
  sudo apt-get install -y -qq \
    git curl wget gnupg ca-certificates \
    python3 python3-venv python3-pip python3-keyring \
    build-essential libssl-dev libffi-dev jq unzip || true
  success "System packages installed"

  export NVM_DIR="$HOME/.nvm"
  if ! command -v node &>/dev/null || [[ "$(node --version | cut -d. -f1 | tr -dv)" -lt 20 ]]; then
    info "Installing Node.js 20 LTS via nvm..."
    [ ! -d "$NVM_DIR" ] && \
      curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
    # shellcheck disable=SC1090
    source "$NVM_DIR/nvm.sh"
    nvm install 20 --lts --silent && nvm use 20 && nvm alias default 20
    grep -q 'NVM_DIR' ~/.bashrc || {
      echo 'export NVM_DIR="$HOME/.nvm"'                               >> ~/.bashrc
      echo '[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"'         >> ~/.bashrc
      echo '[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"' >> ~/.bashrc
    }
    success "Node $(node --version) installed"
  else
    source "$NVM_DIR/nvm.sh" 2>/dev/null || true
    success "Node $(node --version) already present"
  fi

  if ! command -v claude &>/dev/null; then
    info "Installing Claude Code CLI..."
    npm install -g @anthropic-ai/claude-code --silent
    success "Claude Code CLI installed"
  else
    success "Claude Code CLI already present"
  fi
}

# =============================================================================
# PHASE 3 -- Git configuration
# =============================================================================
phase_git_config() {
  step "PHASE 3 -- Git Configuration"
  git config --global user.name  "$GIT_NAME"
  git config --global user.email "$GIT_EMAIL"
  git config --global init.defaultBranch main
  git config --global core.autocrlf false
  git config --global core.eol lf
  git config --global pull.rebase false
  git config --global fetch.prune true
  success "Git global config set"

  local key_path="$HOME/.ssh/id_ed25519_aiworkspace"
  if [ ! -f "$key_path" ]; then
    mkdir -p ~/.ssh && chmod 700 ~/.ssh
    ssh-keygen -t ed25519 -C "$GIT_EMAIL" -f "$key_path" -N "" -q
    grep -q "$key_path" ~/.bashrc || {
      echo 'eval "$(ssh-agent -s)" > /dev/null 2>&1' >> ~/.bashrc
      echo "ssh-add ${key_path} > /dev/null 2>&1"    >> ~/.bashrc
    }
    success "SSH key generated: $key_path"
    echo ""
    echo -e "${BOLD}${YELLOW}ACTION -- Add this public key to GitHub/GitLab:${RESET}"
    echo ""; cat "${key_path}.pub"; echo ""
    confirm "Press y once added (or y to skip for now)" || true
  else
    success "SSH key already exists: $key_path"
  fi
}

# =============================================================================
# PHASE 4 -- Workspace directory structure
# =============================================================================
phase_workspace_structure() {
  step "PHASE 4 -- Workspace Directory Structure"
  mkdir -p "$SHARED_DIR/prompts" "$PROJECTS_DIR"
  success "Workspace created at $WORKSPACE"

  cat > "$SHARED_DIR/.env" << ENVFILE
ANTHROPIC_API_KEY=${ANTHROPIC_KEY}
OPENAI_API_KEY=${OPENAI_KEY}
GOOGLE_API_KEY=${GOOGLE_KEY}

CLAUDE_MODEL=claude-opus-4-6
GPT_MODEL=gpt-5.4
GEMINI_MODEL=gemini-2.5-flash

MAX_OUTPUT_TOKENS=16000
MAX_RETRIES=3
LOG_LEVEL=INFO
ENVFILE
  chmod 600 "$SHARED_DIR/.env"
  success "Shared .env written (chmod 600)"

  cat > "$SHARED_DIR/.env.example" << 'ENVEXAMPLE'
# Copy to .env and populate -- .env is gitignored, never committed
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AIza...

CLAUDE_MODEL=claude-opus-4-6
GPT_MODEL=gpt-5.4
GEMINI_MODEL=gemini-2.5-flash

# Token budget for Stage 1 and Stage 4 (code generation stages).
# 16000 comfortably fits a real-world Stage 1 build; bump to 20000 if truncation occurs.
MAX_OUTPUT_TOKENS=16000
MAX_RETRIES=3
LOG_LEVEL=INFO
ENVEXAMPLE

  cat > "$WORKSPACE/.gitignore" << 'GITIGNORE'
.shared/.env
.shared/.venv/
**/__pycache__/
**/*.pyc
**/.pytest_cache/
**/.mypy_cache/
**/node_modules/
**/.DS_Store
**/Thumbs.db
**/.model_cache/
GITIGNORE
  success "Workspace .gitignore written"
}

# =============================================================================
# PHASE 5 -- Shared system prompt templates
# =============================================================================
phase_shared_prompts() {
  step "PHASE 5 -- Shared System Prompt Templates"

  cat > "$SHARED_DIR/prompts/claude_coder.md" << 'PROMPT'
You are a senior software engineer writing production-quality code.

RESPONSIBILITIES:
- Implement the requested feature completely and correctly
- Write idiomatic, well-structured code following SOLID principles
- Include type annotations and docstrings on all public interfaces
- Output ONLY code files; wrap each in a markdown code block with the filepath
  on the SAME LINE as the opening fence. Examples:
  ```python src/client.py
  ```tsx src/components/Hero.tsx
  ```ts src/lib/supabase.ts
  ```json package.json

OUTPUT RULES -- CRITICAL:
- Every file block MUST have its filepath on the opening fence line
- Root-level config files use bare filename: ```json package.json (no directory prefix)
- Split large tasks into multiple focused files rather than one huge file
- If approaching the token limit, complete the current file cleanly and stop
- Do NOT rewrite or modify any files not explicitly listed in the task
- Never invent content -- use only what is provided in the task description

FILE MODIFICATION RULES:
- For ANY file change -- replacement OR insertion -- always use exact string replacement
- To insert before a known pattern: replace <pattern> with <new_content><pattern>
- NEVER use positional arithmetic (find index + offset + slice)
- NEVER use sed for multi-line replacements -- use Python with exact string matching
- Always read the file content before modifying to confirm the exact pattern exists

BRAND RULES -- CRITICAL (when brand context is provided above):
- Every visual decision MUST reference the brand guide -- colours, fonts, spacing, tone
- Never invent colours, fonts, or design tokens not present in the brand guide
- Logo: always use the exact src path specified in the brand guide
- Typography: use only the font families and weights defined in the brand guide
- Colours: use only the exact hex values or CSS variables from the brand guide
- Copy/tone: match the brand voice and terminology exactly as specified
- If the brand guide specifies a component library or CSS framework, use it exclusively

CONSTRAINTS:
- No placeholder comments like "# TODO: implement this"
- No explanatory prose outside of code blocks
- Never hardcode credentials, ports, or environment-specific values
- Raise descriptive exceptions rather than silently failing
- No console.log or print debug statements in production code

STACK-SPECIFIC RULES (apply only when relevant to the project stack):

Python:
- Follow PEP8, use type hints on all public functions
- Prefer pathlib over os.path, dataclasses over plain dicts for structured data

Node.js / TypeScript:
- Follow ESLint recommended, use strict TypeScript
- No any casts unless absolutely necessary -- prefer unknown with type guards

Next.js / React:
- In API route files (route.ts) ALWAYS use server-side clients -- never browser clients
- Validate all required fields at the top of every API route before any DB calls
- Wrap all email/notification sending in try/catch -- failures must not break main flow
- Import shared types from a central types file -- never from page or component files

Go:
- Follow effective Go conventions, handle all errors explicitly
- Use table-driven tests for all public functions

General web:
- All user inputs used in HTML must be HTML-escaped before interpolation
- All admin routes must verify authentication before any data operation
- Never expose secret keys with public/client-side prefixes
PROMPT

  cat > "$SHARED_DIR/prompts/gpt_reviewer.md" << 'PROMPT'
You are a senior code reviewer and technical writer.

Given the source code provided, produce a structured review:

## Documentation Gaps
List public functions or classes lacking adequate docstrings.
For each: file path, function/class name, and what is missing.

## Code Quality Issues
Flag logic errors, anti-patterns, and style violations.
Format: [FILE:LINE] Issue description. Suggested fix.

## Test Coverage
Identify untested code paths and suggest specific test cases.

## Suggested Improvements
Concrete, actionable refactoring with before/after code snippets.

RULES:
- Be direct -- do not restate what the code does
- Only flag genuine issues
- Priority: correctness > security > performance > style
PROMPT

  cat > "$SHARED_DIR/prompts/gemini_validator.md" << 'PROMPT'
You are a security-focused code auditor performing an independent review.
Approach the code fresh -- you have not seen any prior reviews.

## Security Findings
OWASP-categorised vulnerabilities.
Format: [SEVERITY: Critical|High|Medium|Low] Category -- Description -- Remediation

## Correctness Issues
Logic bugs, race conditions, unhandled edge cases.

## Dependency Risks
Any imports with known CVEs or unusual permissions.

## Compliance Checklist
- [ ] No hardcoded secrets
- [ ] Input validation present
- [ ] Error handling present
- [ ] Logging does not expose PII
- [ ] No SQL/command injection vectors
PROMPT

  cat > "$SHARED_DIR/prompts/claude_final.md" << 'PROMPT'
You are the lead engineer performing a final synthesis review before merge.

You have been provided:
  1. A code quality and documentation review (GPT)
  2. A security and correctness audit (Gemini)
  3. The current source code

Your task:
  A. Triage ALL findings: ACCEPT / REJECT / ESCALATE each with rationale
  B. Apply all ACCEPTED changes -- output complete corrected files
  C. Write a final-review.md summarising decisions and final state
  D. Tag escalated items [HUMAN REVIEW NEEDED] with explanation

Priority: Brand fidelity > Security > Correctness > Performance > Style

OUTPUT RULES -- CRITICAL:
- Every corrected file MUST have its filepath on the opening fence line
- Example: ```tsx src/components/Hero.tsx
- Do NOT rewrite files that have no accepted changes
- Do NOT rename or restructure files not listed in the task

BRAND RULES -- CRITICAL (when brand context is provided above):
- Do NOT change any colour, font, spacing, or copy that matches the brand guide
- If a reviewer suggests a colour change that contradicts the brand guide, REJECT it
- If Stage 1 used the correct brand colours and a reviewer flagged them as wrong,
  REJECT the reviewer suggestion and keep the brand colours
- Brand fidelity is non-negotiable -- it takes priority over reviewer preferences
PROMPT

  success "Shared prompt templates written (4 files)"
}

# =============================================================================
# PHASE 6 -- Python virtual environment
# =============================================================================
phase_shared_venv() {
  step "PHASE 6 -- Shared Python Environment"
  python3 -m venv "$SHARED_DIR/.venv"
  # shellcheck disable=SC1090
  source "$SHARED_DIR/.venv/bin/activate"
  pip install --upgrade pip --quiet

  info "Installing Python dependencies..."
  pip install --quiet \
    anthropic \
    openai \
    google-genai \
    python-dotenv gitpython tenacity rich \
    keyring keyrings.alt \
    detect-secrets pre-commit

  pip freeze > "$SHARED_DIR/requirements.txt"
  success "Shared venv at $SHARED_DIR/.venv"

  cd "$WORKSPACE"
  detect-secrets scan > "$SHARED_DIR/.secrets.baseline" 2>/dev/null || true

  cat > "$WORKSPACE/.pre-commit-config.yaml" << 'PRECOMMIT'
repos:
- repo: https://github.com/Yelp/detect-secrets
  rev: v1.4.0
  hooks:
  - id: detect-secrets
    args: ['--baseline', '.shared/.secrets.baseline']
- repo: https://github.com/pre-commit/pre-commit-hooks
  rev: v4.5.0
  hooks:
  - id: check-added-large-files
  - id: check-merge-conflict
  - id: end-of-file-fixer
  - id: trailing-whitespace
  - id: check-yaml
PRECOMMIT

  pre-commit install --quiet 2>/dev/null || warn "pre-commit install failed (non-fatal)"
  success "Security hooks configured"
}

# =============================================================================
# PHASE 7 -- Store API keys in system keyring
# =============================================================================
phase_keyring() {
  step "PHASE 7 -- Storing API Keys in System Keyring"

  # shellcheck disable=SC1090
  source "$SHARED_DIR/.venv/bin/activate"

  # WSL2 has no D-Bus secret service, so we use keyrings.alt (plaintext
  # backend at ~/.local/share/python_keyring/) as a reliable fallback.
  # The .env file remains the primary secret store; keyring is secondary.
  python3 - << PYEOF
import sys, os

os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyrings.alt.file.PlaintextKeyring")

try:
    import keyring
    import keyrings.alt.file
    backend_ok = True
except ImportError as e:
    print(f"  [WARN] keyring backend unavailable: {e}", file=sys.stderr)
    backend_ok = False

keys = {
    "ANTHROPIC_API_KEY": "${ANTHROPIC_KEY}",
    "OPENAI_API_KEY":    "${OPENAI_KEY}",
    "GOOGLE_API_KEY":    "${GOOGLE_KEY}",
}

if backend_ok:
    for name, value in keys.items():
        try:
            keyring.set_password("ai-pipeline", name, value)
            print(f"  Stored in keyring: {name}")
        except Exception as e:
            print(f"  [WARN] Could not store {name}: {e}", file=sys.stderr)
    print("  Keyring backend: ~/.local/share/python_keyring/")
else:
    print("  [INFO] Keyring skipped -- .env file is your primary secret store")

print(f"  Primary secrets: ${SHARED_DIR}/.env (chmod 600)")
PYEOF

  unset ANTHROPIC_KEY OPENAI_KEY GOOGLE_KEY
  success "API keys secured (.env + keyring backup)"
}

# =============================================================================
# PHASE 8 -- Write shared scripts
# =============================================================================
phase_write_shared_scripts() {
  step "PHASE 8 -- Writing Shared Scripts"

  # ── orchestrate.py -- downloaded from repo to ensure latest version ────────
  info "Downloading orchestrate.py from repo..."
  curl -fsSL "https://raw.githubusercontent.com/nsisongeffiong/ai-workspace-scripts/main/orchestrate.py" \
    -o "$SHARED_DIR/orchestrate.py" \
    || { error "Failed to download orchestrate.py"; exit 1; }

  # ── smoke_test.py ────────────────────────────────────────────────────────────
  cat > "$SHARED_DIR/smoke_test.py" << 'PYEOF'
#!/usr/bin/env python3
"""
Smoke test -- verifies connectivity to all three cloud providers.
Run: python3 ~/.ai-workspace/.shared/smoke_test.py
"""
import os, sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-6")
GPT_MODEL    = os.getenv("GPT_MODEL",    "gpt-5.4")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


def check(name, fn):
    try:
        result = fn()
        print(f"  [OK]   {name}: {result[:60]}")
        return True
    except Exception as e:
        print(f"  [FAIL] {name}: {e}")
        return False


def test_anthropic():
    import anthropic
    r = anthropic.Anthropic().messages.create(
        model=CLAUDE_MODEL, max_tokens=20,
        messages=[{"role": "user", "content": "Reply with only: OK"}]
    )
    return r.content[0].text.strip()


def test_openai():
    import openai
    r = openai.OpenAI().chat.completions.create(
        model=GPT_MODEL, max_completion_tokens=10,
        messages=[{"role": "user", "content": "Reply with only: OK"}]
    )
    return r.choices[0].message.content.strip()


def test_google():
    from google import genai
    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    r = client.models.generate_content(
        model=GEMINI_MODEL,
        contents="Reply with only: OK",
        config=genai.types.GenerateContentConfig(max_output_tokens=10),
    )
    return (r.text or "OK").strip()


print(f"\nSmoke Test -- Cloud API Connectivity\n")
print(f"  Claude: {CLAUDE_MODEL}")
print(f"  GPT:    {GPT_MODEL}")
print(f"  Gemini: {GEMINI_MODEL}\n")

results = [
    check(f"Anthropic ({CLAUDE_MODEL})", test_anthropic),
    check(f"OpenAI    ({GPT_MODEL})",    test_openai),
    check(f"Google    ({GEMINI_MODEL})", test_google),
]

print()
if all(results):
    print("All providers reachable. Pipeline is ready.\n")
    sys.exit(0)
else:
    print("One or more providers failed.")
    print("Check keys in ~/.ai-workspace/.shared/.env")
    print("Common causes:")
    print("  Anthropic: insufficient credits -- add funds at console.anthropic.com")
    print("  OpenAI:    check key validity at platform.openai.com")
    print("  Google:    check key at aistudio.google.com\n")
    sys.exit(1)
PYEOF

  chmod +x "$SHARED_DIR/orchestrate.py" "$SHARED_DIR/smoke_test.py"
  success "orchestrate.py and smoke_test.py written"
}

# =============================================================================
# PHASE 9 -- Smoke test
# =============================================================================
phase_smoke_test() {
  step "PHASE 9 -- API Connectivity Smoke Test"
  # shellcheck disable=SC1090
  source "$SHARED_DIR/.venv/bin/activate"
  echo ""
  if python3 "$SHARED_DIR/smoke_test.py"; then
    success "All three APIs reachable. Pipeline is ready."
  else
    warn "One or more providers failed -- see output above."
    warn "Fix the issue then re-run: python3 $SHARED_DIR/smoke_test.py"
  fi
}

# =============================================================================
# PHASE 10 -- Summary
# =============================================================================
phase_summary() {
  step "WORKSPACE SETUP COMPLETE"
  echo -e "${GREEN}${BOLD}"
  echo "  +==============================================================+"
  echo "  |    WORKSPACE READY  --  CREATE YOUR FIRST PROJECT           |"
  echo "  +==============================================================+"
  echo -e "${RESET}"
  echo -e "  ${BOLD}Create a new project:${RESET}"
  echo -e "    ${CYAN}~/new-project.sh my-api-service${RESET}"
  echo -e "    ${CYAN}~/new-project.sh auth-module --lang node${RESET}"
  echo ""
  echo -e "  ${BOLD}Run the pipeline:${RESET}"
  echo -e "    ${CYAN}cd ~/ai-workspace/projects/my-api-service${RESET}"
  echo -e "    ${CYAN}python scripts/run.py \"Implement a rate-limited HTTP client\"${RESET}"
  echo ""
  echo -e "  ${BOLD}Re-run smoke test:${RESET}"
  echo -e "    ${CYAN}python3 ~/ai-workspace/.shared/smoke_test.py${RESET}"
  echo ""
  echo -e "  ${BOLD}Update an API key:${RESET}"
  echo -e "    ${CYAN}nano ~/ai-workspace/.shared/.env${RESET}"
  echo ""
  echo -e "  ${BOLD}Override a prompt for one project:${RESET}"
  echo -e "    ${CYAN}cp ~/ai-workspace/.shared/prompts/claude_coder.md <project>/prompts/${RESET}"
  echo ""
  warn "Reload your shell: source ~/.bashrc"
  echo ""
}

# =============================================================================
# MAIN
# =============================================================================
main() {
  if [ "${1:-}" = "--reset" ]; then reset_checkpoints; shift; fi

  if [ -f "$CHECKPOINT_FILE" ]; then
    echo ""
    echo "  Resuming -- completed phases will be skipped."
    echo "  Run with --reset to start from scratch."
    echo ""
  fi

  print_banner
  phase_preflight
  phase_collect_inputs

  run_phase "phase_system_deps"          phase_system_deps
  run_phase "phase_git_config"           phase_git_config
  run_phase "phase_workspace_structure"  phase_workspace_structure
  run_phase "phase_shared_prompts"       phase_shared_prompts
  run_phase "phase_shared_venv"          phase_shared_venv
  run_phase "phase_keyring"              phase_keyring
  run_phase "phase_write_shared_scripts" phase_write_shared_scripts
  run_phase "phase_smoke_test"           phase_smoke_test
  phase_summary
}

main "$@"

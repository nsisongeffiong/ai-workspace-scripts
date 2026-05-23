#!/usr/bin/env bash
# =============================================================================
#  Apply workspace improvements to an existing installation
#  Downloads latest scripts and prompts directly from the repo.
#  Run: bash ~/update-workspace.sh
# =============================================================================

set -euo pipefail

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; RESET='\033[0m'
ok()   { echo -e "${GREEN}[OK]${RESET}    $*"; }
info() { echo -e "${CYAN}[INFO]${RESET}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${RESET}  $*"; }

REPO="https://raw.githubusercontent.com/nsisongeffiong/ai-workspace-scripts/main"
SHARED="$HOME/ai-workspace/.shared"
PROMPTS="$SHARED/prompts"

echo -e "\n${BOLD}${CYAN}Applying workspace improvements...${RESET}\n"

# ── Self-update ───────────────────────────────────────────────────────────────
# Download latest version from repo and re-exec if changed.
# UPDATED flag prevents infinite re-exec loop.
if [[ "${UPDATED:-0}" != "1" ]]; then
  info "Checking for update-workspace.sh updates..."
  SELF="$HOME/update-workspace.sh"
  OLD_SUM=$(md5sum "$SELF" 2>/dev/null | cut -d' ' -f1 || echo "none")
  if curl -fsSL "$REPO/update-workspace.sh" -o "$SELF.tmp" 2>/dev/null; then
    mv "$SELF.tmp" "$SELF" && chmod +x "$SELF"
    NEW_SUM=$(md5sum "$SELF" | cut -d' ' -f1)
    if [[ "$OLD_SUM" != "$NEW_SUM" ]]; then
      ok "update-workspace.sh updated -- re-running with latest version"
      exec env UPDATED=1 bash "$SELF"
    fi
  else
    warn "Self-update failed -- continuing with current version"
    rm -f "$SELF.tmp"
  fi
fi

# ── Validate workspace exists ─────────────────────────────────────────────────
if [[ ! -d "$SHARED" ]]; then
  echo "ERROR: Workspace not found at $HOME/ai-workspace"
  echo "Run setup-workspace.sh first."
  exit 1
fi

# ── Update orchestrate.py ─────────────────────────────────────────────────────
info "Updating orchestrate.py..."
OLD_SUM=$(md5sum "$SHARED/orchestrate.py" 2>/dev/null | cut -d' ' -f1 || echo "none")
curl -fsSL "$REPO/orchestrate.py" -o "$SHARED/orchestrate.py" \
  || { warn "Failed to download orchestrate.py -- skipping"; }
chmod +x "$SHARED/orchestrate.py"
NEW_SUM=$(md5sum "$SHARED/orchestrate.py" | cut -d' ' -f1)
[[ "$OLD_SUM" != "$NEW_SUM" ]] && ORCHESTRATE_STATUS="changed" || ORCHESTRATE_STATUS="already latest"

# ── Update new-project.sh ─────────────────────────────────────────────────────
info "Updating new-project.sh..."
OLD_SUM=$(md5sum "$HOME/new-project.sh" 2>/dev/null | cut -d' ' -f1 || echo "none")
curl -fsSL "$REPO/new-project.sh" -o "$HOME/new-project.sh" \
  || { warn "Failed to download new-project.sh -- skipping"; }
chmod +x "$HOME/new-project.sh"
NEW_SUM=$(md5sum "$HOME/new-project.sh" | cut -d' ' -f1)
[[ "$OLD_SUM" != "$NEW_SUM" ]] && NEW_PROJECT_STATUS="changed" || NEW_PROJECT_STATUS="already latest"

# ── Back up and update shared prompts ─────────────────────────────────────────
info "Backing up existing prompts..."
cp "$PROMPTS/claude_coder.md"  "$PROMPTS/claude_coder.md.bak"  2>/dev/null || true
cp "$PROMPTS/claude_final.md"  "$PROMPTS/claude_final.md.bak"  2>/dev/null || true
ok "Backups saved as .bak files"

info "Updating claude_coder.md..."
cat > "$PROMPTS/claude_coder.md" << 'PROMPT'
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
ok "claude_coder.md updated"

info "Updating claude_final.md..."
cat > "$PROMPTS/claude_final.md" << 'PROMPT'
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
ok "claude_final.md updated"

# ── Update .env model and token budget ───────────────────────────────────────
info "Updating .env model and token settings..."
ENV_FILE="$SHARED/.env"
ENV_STATUS="no changes needed"

if [[ -f "$ENV_FILE" ]]; then
  # Update CLAUDE_MODEL if it's set to an older version
  if grep -q "^CLAUDE_MODEL=claude-opus-4-6" "$ENV_FILE"; then
    sed -i 's/^CLAUDE_MODEL=claude-opus-4-6/CLAUDE_MODEL=claude-opus-4-7/' "$ENV_FILE"
    ENV_STATUS="updated"
    ok "  CLAUDE_MODEL -> claude-opus-4-7"
  fi

  # Update GPT_MODEL if on older version
  if grep -q "^GPT_MODEL=gpt-5\.4" "$ENV_FILE"; then
    sed -i 's/^GPT_MODEL=gpt-5\.4/GPT_MODEL=gpt-5.5/' "$ENV_FILE"
    ENV_STATUS="updated"
    ok "  GPT_MODEL -> gpt-5.5"
  fi

  # Update GEMINI_MODEL if on older version
  if grep -q "^GEMINI_MODEL=gemini-2\.5-flash" "$ENV_FILE"; then
    sed -i 's/^GEMINI_MODEL=gemini-2\.5-flash/GEMINI_MODEL=gemini-3.5-flash/' "$ENV_FILE"
    ENV_STATUS="updated"
    ok "  GEMINI_MODEL -> gemini-3.5-flash"
  fi

  # Update MAX_OUTPUT_TOKENS from 16000 to 20000 if not already higher
  if grep -q "^MAX_OUTPUT_TOKENS=16000" "$ENV_FILE"; then
    sed -i 's/^MAX_OUTPUT_TOKENS=16000/MAX_OUTPUT_TOKENS=20000/' "$ENV_FILE"
    ENV_STATUS="updated"
    ok "  MAX_OUTPUT_TOKENS -> 20000"
  fi

  # Add MAX_OUTPUT_TOKENS if missing entirely
  if ! grep -q "^MAX_OUTPUT_TOKENS=" "$ENV_FILE"; then
    echo "" >> "$ENV_FILE"
    echo "MAX_OUTPUT_TOKENS=20000" >> "$ENV_FILE"
    ENV_STATUS="updated"
    ok "  MAX_OUTPUT_TOKENS=20000 added"
  fi
else
  warn ".env not found at $ENV_FILE -- skipping (run setup-workspace.sh first)"
fi

# ── Terminal agents ───────────────────────────────────────────────────────────
info "Checking terminal agents..."
# Ensure nvm-managed npm is on PATH if not already available
if ! command -v npm &>/dev/null; then
  # shellcheck disable=SC1090
  [ -s "$HOME/.nvm/nvm.sh" ] && source "$HOME/.nvm/nvm.sh" 2>/dev/null || true
fi

_install_agent() {
  local name="$1" bin="$2" pkg="$3"
  if command -v "$bin" &>/dev/null; then
    ok "$name present ($("$bin" --version 2>/dev/null || echo 'version unknown'))"
  else
    info "$name not found -- installing..."
    if command -v npm &>/dev/null; then
      if npm install -g "$pkg" --silent; then
        ok "$name installed"
      else
        warn "$name install failed -- install manually: npm install -g $pkg"
      fi
    else
      warn "npm not available -- install $name manually: npm install -g $pkg"
    fi
  fi
}

_install_agent "Claude Code" "claude" "@anthropic-ai/claude-code"
_install_agent "Codex CLI"   "codex"  "@openai/codex"

# Ensure API keys are exported to shell environment for terminal agent auth.
# Gated on .env existing -- same guard as the model patching block above.
if [[ -f "$ENV_FILE" ]]; then
  grep -q 'ANTHROPIC_API_KEY' ~/.bashrc || \
    echo 'export ANTHROPIC_API_KEY=$(grep ^ANTHROPIC_API_KEY ~/ai-workspace/.shared/.env | cut -d= -f2-)' >> ~/.bashrc
  grep -q 'OPENAI_API_KEY' ~/.bashrc || \
    echo 'export OPENAI_API_KEY=$(grep ^OPENAI_API_KEY ~/ai-workspace/.shared/.env | cut -d= -f2-)' >> ~/.bashrc
  ok "API keys present in ~/.bashrc for terminal agent auth"
else
  warn "API key export skipped -- .env not found (run setup-workspace.sh first)"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}"
echo "  +==============================================================+"
echo "  |  WORKSPACE IMPROVEMENTS APPLIED                             |"
echo "  +==============================================================+"
echo -e "${RESET}"
echo "  Files updated:"
echo "    orchestrate.py   -- $ORCHESTRATE_STATUS"
echo "    new-project.sh   -- $NEW_PROJECT_STATUS"
echo "    claude_coder.md  -- updated"
echo "    claude_final.md  -- updated"
echo "    .env             -- $ENV_STATUS"
echo ""
echo "  Verification:"
_check() {
  local label="$1" file="$2"; shift 2
  local total=$# found=0
  for pattern in "$@"; do
    grep -q "$pattern" "$file" 2>/dev/null && found=$((found + 1)) || true
  done
  printf "    %-18s -- %d/%d checks OK\n" "$label" "$found" "$total"
}
_check "orchestrate.py"  "$SHARED/orchestrate.py"  "extract_code_blocks" "install_dependencies" "from_stage" "setup_brand_assets" "claude-opus-4-7" "xhigh"
_check "claude_coder.md" "$PROMPTS/claude_coder.md" "OUTPUT RULES" "FILE MODIFICATION" "BRAND RULES"
_check "claude_final.md" "$PROMPTS/claude_final.md" "OUTPUT RULES" "BRAND RULES"
_check "new-project.sh"  "$HOME/new-project.sh"     "\-\-brand" "\-\-lang" "submodule"
_check ".env"            "$ENV_FILE"                "claude-opus-4-7" "gpt-5.5" "gemini-3.5-flash" "MAX_OUTPUT_TOKENS"
echo ""
echo "  Backed up originals:"
echo "    $PROMPTS/claude_coder.md.bak"
echo "    $PROMPTS/claude_final.md.bak"
echo ""

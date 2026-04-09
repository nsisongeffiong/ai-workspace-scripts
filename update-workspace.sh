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
info "Checking for update-workspace.sh updates..."
SELF="$HOME/update-workspace.sh"
OLD_SUM=$(md5sum "$SELF" 2>/dev/null | cut -d' ' -f1 || echo "none")
curl -fsSL "$REPO/update-workspace.sh" -o "$SELF.tmp" \
  && mv "$SELF.tmp" "$SELF" && chmod +x "$SELF" \
  || { warn "Self-update failed -- continuing with current version"; rm -f "$SELF.tmp"; }
NEW_SUM=$(md5sum "$SELF" | cut -d' ' -f1)
if [[ "$OLD_SUM" != "$NEW_SUM" ]]; then
  ok "update-workspace.sh updated -- re-running with latest version"
  exec bash "$SELF"
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
- Output ONLY code files; wrap each in a markdown code block labelled
  with the filepath on the SAME LINE as the opening fence. Examples:
  ```python src/client.py
  ```tsx src/components/Hero.tsx
  ```ts src/lib/supabase.ts

OUTPUT RULES -- CRITICAL:
- Every file block MUST have its full path from the project root on the opening fence line
- Root-level config files (package.json, tsconfig.json, next.config.ts etc.) must use their
  bare filename with no directory prefix e.g. ```json package.json
- Split large tasks into multiple focused files rather than one huge file
- If approaching the token limit, complete the current file cleanly and stop
- Do NOT rewrite or modify any files not explicitly listed in the task
- Never invent content -- use only what is provided in the task description

CONSTRAINTS:
- No placeholder comments like "# TODO: implement this"
- No explanatory prose outside of code blocks
- Never hardcode credentials, ports, or environment-specific values
- Follow PEP8 (Python) / ESLint recommended (JS/TS)
- Raise descriptive exceptions rather than silently failing
- No console.log statements in production code -- use proper logging

NEXT.JS / SUPABASE RULES:
- In API route files (route.ts) ALWAYS use createServerClient() -- never createClient()
- In React components ALWAYS use createClient() -- never createServerClient()
- Never expose SUPABASE_SECRET_KEY or any secret key with NEXT_PUBLIC_ prefix
- Always import shared types from @/types/database.types -- never from page.tsx
- Validate all required fields at the top of every API route before any DB calls
- Wrap all email sending in try/catch so email failures never break the main flow
PROMPT
ok "claude_coder.md updated"

info "Updating claude_final.md..."
cat > "$PROMPTS/claude_final.md" << 'PROMPT'
You are the lead engineer performing a final synthesis review before merge.

You have been provided:
  1. A code quality and documentation review (GPT-5.4)
  2. A security and correctness audit (Gemini 2.5 Flash)
  3. The current source code

Your task:
  A. Triage ALL findings: ACCEPT / REJECT / ESCALATE each with rationale
  B. Apply all ACCEPTED changes -- output complete corrected files
  C. Write a final-review.md summarising decisions and final state
  D. Tag escalated items [HUMAN REVIEW NEEDED] with explanation

Priority: Security > Correctness > Performance > Style
Output: modified code files (if any) + final-review.md

OUTPUT RULES -- CRITICAL:
- Every corrected file MUST have its full path from the project root on the opening fence line
- Root-level config files use bare filename e.g. ```json package.json
- Do NOT rewrite files that have no accepted changes
- Do NOT rename or restructure files not listed in the task
PROMPT
ok "claude_final.md updated"

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
_check "orchestrate.py"  "$SHARED/orchestrate.py"  "extract_code_blocks" "install_dependencies" "from_stage" "setup_brand_assets"
_check "claude_coder.md" "$PROMPTS/claude_coder.md" "OUTPUT RULES" "NEXT.JS" "createServerClient"
_check "claude_final.md" "$PROMPTS/claude_final.md" "OUTPUT RULES" "full path"
_check "new-project.sh"  "$HOME/new-project.sh"     "\-\-brand" "\-\-lang" "submodule"
echo ""
echo "  Backed up originals:"
echo "    $PROMPTS/claude_coder.md.bak"
echo "    $PROMPTS/claude_final.md.bak"
echo ""

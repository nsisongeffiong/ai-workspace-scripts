#!/usr/bin/env bash
# =============================================================================
#  Multi-Model Agentic Dev Environment -- NEW PROJECT
#  Run this for every new project after workspace setup
#  Usage:  bash ~/new-project.sh <project-name> [--lang python|node|go]
#          bash ~/new-project.sh my-api-service
#          bash ~/new-project.sh my-nextjs-app --lang node
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

WORKSPACE="$HOME/ai-workspace"
SHARED_DIR="$WORKSPACE/.shared"
PROJECTS_DIR="$WORKSPACE/projects"

if [[ ! -d "$SHARED_DIR" ]]; then
  error "Workspace not found at ${WORKSPACE}"
  error "Run setup-workspace.sh first."
  exit 1
fi

PROJECT_NAME=""
LANG="python"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --lang) LANG="$2"; shift 2 ;;
    --*)    error "Unknown option: $1"; exit 1 ;;
    *)      PROJECT_NAME="$1"; shift ;;
  esac
done

if [[ -z "$PROJECT_NAME" ]]; then
  echo -e "\n${BOLD}${BLUE}---  NEW PROJECT  ---${RESET}\n"
  echo -e "Existing projects:"
  if ls "$PROJECTS_DIR" 2>/dev/null | grep -q .; then
    ls "$PROJECTS_DIR" | sed 's/^/    /'
  else
    echo "    (none yet)"
  fi
  echo ""
  read -rp "$(echo -e "${YELLOW}[INPUT]${RESET} Project name (letters, numbers, hyphens): ")" PROJECT_NAME
  while [[ -z "$PROJECT_NAME" ]]; do
    read -rp "$(echo -e "${YELLOW}[INPUT]${RESET} Name cannot be empty: ")" PROJECT_NAME
  done
  echo ""
  echo -e "  Primary language:"
  echo -e "    ${CYAN}1${RESET}) Python  (default)"
  echo -e "    ${CYAN}2${RESET}) Node.js / TypeScript"
  echo -e "    ${CYAN}3${RESET}) Go"
  echo -e "    ${CYAN}4${RESET}) Generic"
  read -rp "$(echo -e "${YELLOW}[INPUT]${RESET} Choose [1-4, default 1]: ")" lang_choice
  case "${lang_choice:-1}" in
    2) LANG="node" ;;
    3) LANG="go"   ;;
    4) LANG="generic" ;;
    *) LANG="python" ;;
  esac
fi

PROJECT_NAME=$(echo "$PROJECT_NAME" | tr -cs '[:alnum:]_-' '-' | sed 's/^-//;s/-$//')
if [[ -z "$PROJECT_NAME" ]]; then
  error "Invalid project name."; exit 1
fi

PROJECT_DIR="$PROJECTS_DIR/$PROJECT_NAME"

if [[ -d "$PROJECT_DIR" ]]; then
  error "Project already exists: ${PROJECT_DIR}"
  exit 1
fi

echo ""
echo -e "${BOLD}Creating project:${RESET}"
echo -e "  Name:     ${PROJECT_NAME}"
echo -e "  Language: ${LANG}"
echo -e "  Location: ${PROJECT_DIR}"
echo ""
confirm "Proceed?" || { echo "Cancelled."; exit 0; }

step "Creating Directory Structure"
mkdir -p "$PROJECT_DIR"/{src,reviews,docs,prompts,.model_cache}
success "Directories created"

step "Initialising Git Repository"
cd "$PROJECT_DIR"
git init -q --initial-branch=main 2>/dev/null || git init -q
git config user.name  "$(git config --global user.name  2>/dev/null || echo Dev)"
git config user.email "$(git config --global user.email 2>/dev/null || echo dev@example.com)"
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
if [ "$CURRENT_BRANCH" = "master" ]; then
  git branch -m master main 2>/dev/null || true
fi
git commit --allow-empty -m "chore: initialise ${PROJECT_NAME}" -q
success "Git repository initialised (branch: main)"

cat > .gitignore << 'GITIGNORE'
# Secrets
.env
*.key *.pem *.p12 *.pfx

# Python
__pycache__/ *.pyc .pytest_cache/ .mypy_cache/

# Node
node_modules/ .npm/ .next/ dist/

# OS / IDE
.DS_Store Thumbs.db .idea/ .vscode/settings.json

# Pipeline
reviews/*.tmp .model_cache/
GITIGNORE

cat > .gitattributes << 'GITATTR'
* text=auto eol=lf
*.sh  text eol=lf
*.py  text eol=lf
*.md  text eol=lf
*.json text eol=lf
GITATTR

success ".gitignore and .gitattributes written"

step "Writing Project-Local Pipeline Wrapper"
mkdir -p scripts
cat > scripts/run.py << PYEOF
#!/usr/bin/env python3
"""Project-local pipeline runner."""
import sys, os, subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
SHARED_DIR   = Path.home() / "ai-workspace" / ".shared"
ORCHESTRATOR = SHARED_DIR / "orchestrate.py"
VENV_PYTHON  = SHARED_DIR / ".venv" / "bin" / "python3"

if not ORCHESTRATOR.exists():
    print(f"ERROR: Shared orchestrator not found at {ORCHESTRATOR}")
    sys.exit(1)

if len(sys.argv) < 2:
    print(f'Usage: python {Path(__file__).name} "task description"')
    sys.exit(1)

task = " ".join(sys.argv[1:])
env  = {**os.environ, "PROJECT_ROOT": str(PROJECT_ROOT)}
result = subprocess.run([str(VENV_PYTHON), str(ORCHESTRATOR), task], env=env, cwd=str(PROJECT_ROOT))
sys.exit(result.returncode)
PYEOF
chmod +x scripts/run.py
success "scripts/run.py written"

step "Language Scaffold: ${LANG}"

case "$LANG" in
  python)
    cat > src/__init__.py << 'PYEOF'
"""Generated by multi-model pipeline."""
PYEOF
    cat > .env << 'ENVEOF'
# Project-local .env -- overrides shared .env
# CLAUDE_MODEL=claude-opus-4-6
# LOG_LEVEL=DEBUG
ENVEOF
    chmod 600 .env
    cat > requirements.txt << 'REQEOF'
# Add project dependencies here
REQEOF
    success "Python scaffold written"
    ;;

  node)
    cat > package.json << PKGJSON
{
  "name": "${PROJECT_NAME}",
  "version": "0.1.0",
  "description": "Generated by multi-model pipeline",
  "main": "src/index.js",
  "scripts": { "start": "node src/index.js", "test": "jest" },
  "keywords": [],
  "license": "UNLICENSED"
}
PKGJSON
    cat > src/index.js << 'JSEOF'
'use strict';
// Entry point -- generated by multi-model pipeline
JSEOF
    # postcss.config.js -- required for Tailwind CSS in Next.js projects
    cat > postcss.config.js << 'POSTCSS'
module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
POSTCSS
    # next.config.js stub
    cat > next.config.js << 'NEXTCONF'
/** @type {import('next').NextConfig} */
const nextConfig = {}
module.exports = nextConfig
NEXTCONF
    cat > .env << 'ENVEOF'
# Project-local .env -- overrides shared .env
# LOG_LEVEL=DEBUG
ENVEOF
    chmod 600 .env
    success "Node.js scaffold written (includes postcss.config.js and next.config.js)"
    ;;

  go)
    cat > go.mod << GOMOD
module github.com/yourusername/${PROJECT_NAME}

go 1.22
GOMOD
    mkdir -p src/cmd
    cat > src/cmd/main.go << 'GOEOF'
package main

import "fmt"

func main() {
	fmt.Println("generated by multi-model pipeline")
}
GOEOF
    cat > .env << 'ENVEOF'
# Project-local .env -- overrides shared .env
ENVEOF
    chmod 600 .env
    success "Go scaffold written"
    ;;

  generic)
    cat > src/.gitkeep << 'GITKEEP'
GITKEEP
    cat > .env << 'ENVEOF'
# Project-local .env -- overrides shared .env
ENVEOF
    chmod 600 .env
    success "Generic scaffold written"
    ;;
esac

step "Writing README"
cat > README.md << READMEEOF
# ${PROJECT_NAME}

> Multi-model agentic development pipeline project.

## Run the pipeline

\`\`\`bash
cd ${PROJECT_DIR}
python scripts/run.py "Describe your task here"
\`\`\`

## Pipeline stages

| Stage | Model            | Role                          |
|-------|------------------|-------------------------------|
| 1     | Claude Opus 4.6  | Initial implementation        |
| 2     | GPT-5.4          | Code quality & documentation  |
| 3     | Gemini 2.5 Flash | Security & correctness audit  |
| 4     | Claude Opus 4.6  | Final synthesis & corrections |

## Tip: large tasks

For tasks with multiple files (>3KB of instructions), save the task to a
.sh script file and run it rather than pasting directly into the terminal.

\`\`\`bash
bash ~/my-stage-task.sh
\`\`\`
READMEEOF
success "README.md written"

step "Initial Git Commit"
git add .
git reset HEAD .env              2>/dev/null || true
git reset HEAD "*.key"           2>/dev/null || true
git rm --cached -r node_modules  2>/dev/null || true
git rm --cached -r .next         2>/dev/null || true
git rm --cached -r dist          2>/dev/null || true
git rm --cached -r .model_cache  2>/dev/null || true
git ls-files --cached | grep -E "(^|/)__pycache__/" | xargs git rm --cached -r 2>/dev/null || true
git ls-files --cached | grep -E "\.pyc$" | xargs git rm --cached 2>/dev/null || true
git commit -m "chore: scaffold ${PROJECT_NAME} (${LANG})" -q
success "Initial commit made"

echo ""
git log --oneline

echo ""
echo -e "${GREEN}${BOLD}"
echo "  +==============================================================+"
echo "  |  PROJECT READY                                               |"
echo "  |  ${PROJECT_NAME}"
echo "  +==============================================================+"
echo -e "${RESET}"
echo -e "  ${BOLD}Run the pipeline:${RESET}"
echo -e "    ${CYAN}cd ${PROJECT_DIR}${RESET}"
echo -e "    ${CYAN}source ~/ai-workspace/.shared/.venv/bin/activate${RESET}"
echo -e "    ${CYAN}python scripts/run.py \"Your task here\"${RESET}"
echo ""
echo -e "  ${BOLD}For large pipeline tasks (>3KB):${RESET}"
echo -e "    Save the task to a .sh script file and run it -- do not paste large"
echo -e "    commands directly into the terminal as they get truncated."
echo -e "    ${CYAN}bash ~/my-stage-task.sh${RESET}"
echo ""
echo -e "  ${BOLD}All your projects:${RESET}"
ls "$PROJECTS_DIR" | sed 's/^/    /'
echo ""

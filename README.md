# AI Workspace Scripts

A multi-model agentic development pipeline that combines three leading AI models to write, review, and refine code automatically.

## How it works

Every task runs through four stages automatically:

| Stage | Model | Role |
|-------|-------|------|
| 1 | Claude Opus 4.7 | Initial implementation |
| 2 | GPT-5.5 | Code quality & documentation review |
| 3 | Gemini 3.5 Flash | Security & correctness audit |
| 4 | Claude Opus 4.7 | Final synthesis & corrections |

Each stage commits to a feature branch. You review the output and merge to main when satisfied.

The pipeline writes all files exactly where the AI specifies, and runs `npm install` or `pip install` automatically after stages that produce a `package.json` or `requirements.txt`.

---

## Requirements

- An **Anthropic** API key — [console.anthropic.com](https://console.anthropic.com)
- An **OpenAI** API key — [platform.openai.com](https://platform.openai.com)
- A **Google** API key — [aistudio.google.com](https://aistudio.google.com)

---

## Setup

### Windows users — set up WSL2 first

WSL2 (Windows Subsystem for Linux) lets you run Linux on Windows. The workspace scripts require it.

**Step 1 — Enable WSL2**

Open **PowerShell as Administrator** (right-click Start → Windows PowerShell (Admin)) and run:

```powershell
wsl --install
```

This installs WSL2 and Ubuntu automatically. When it finishes, **restart your computer**.

**Step 2 — Open Ubuntu**

After restarting, search for **Ubuntu** in the Start menu and open it. The first time it opens it will ask you to create a Linux username and password. Choose anything you like — this is separate from your Windows login.

**Step 3 — Update Ubuntu**

In the Ubuntu terminal run:

```bash
sudo apt update && sudo apt upgrade -y
```

**Step 4 — Continue to the installation steps below**

All further commands are run inside the Ubuntu terminal, not PowerShell.

> **Tip:** To access your Windows files from Ubuntu, your drives are mounted at `/mnt/c/`, `/mnt/d/` etc.
> For example, your Downloads folder is at `/mnt/c/Users/YourName/OneDrive/Downloads/`.

---

### Mac users

No setup needed. Open **Terminal** (Applications → Utilities → Terminal) and continue below.

---

### Linux users

No setup needed. Open your terminal and continue below.

---

## Installation

**1 — Download the setup script**

```bash
curl -fsSL https://raw.githubusercontent.com/nsisongeffiong/ai-workspace-scripts/main/setup-workspace.sh -o setup-workspace.sh
chmod +x setup-workspace.sh
```

**2 — Run it**

```bash
bash setup-workspace.sh
```

The script will:
- Install Node.js 20, Python 3, Git and all dependencies
- Ask for your Git name and email
- Ask for your three API keys (entered securely, never shown on screen)
- Create the workspace at `~/ai-workspace/`
- Set up a shared Python virtual environment
- Install all pipeline dependencies
- Download `orchestrate.py` from this repo
- Run a smoke test to confirm all three APIs are reachable

The script is safe to re-run — completed phases are skipped automatically.

---

## Create your first project

```bash
bash ~/new-project.sh my-project-name
```

You will be prompted to choose a language (Python, Node.js, Go, or Generic) and optionally attach a brand context.

**With a brand repo:**
```bash
bash ~/new-project.sh my-project --brand git@github.com:org/brand-repo.git
```

**With a local brand file:**
```bash
bash ~/new-project.sh my-project --brand ./mybrand.md
```

**With a raw URL:**
```bash
bash ~/new-project.sh my-project --brand https://raw.githubusercontent.com/org/repo/main/BRAND.md
```

**Non-interactively:**
```bash
bash ~/new-project.sh my-project --lang node --brand git@github.com:org/brand.git
```

---

## Run the pipeline

```bash
cd ~/ai-workspace/projects/my-project-name
source ~/ai-workspace/.shared/.venv/bin/activate
python scripts/run.py "Describe your task here"
```

**Example tasks:**

```bash
python scripts/run.py "Build a REST API with JWT authentication and rate limiting"
python scripts/run.py "Create a Next.js landing page with a contact form"
python scripts/run.py "Write a Python script that processes CSV files and generates reports"
```

The pipeline runs all four stages and commits each to a feature branch. When it finishes:

```bash
# Review the output
git log --oneline
cat reviews/final-review.md

# Merge to main when satisfied
git checkout main
git merge feature/<branch-name>
git push
```

---

## Resume from a specific stage

If a stage fails (e.g. a provider is temporarily unavailable), resume without re-running earlier stages:

```bash
python scripts/run.py --from-stage 3 "Your task here"
python scripts/run.py --from-stage 4 "Your task here"
```

---

## Update an existing workspace

When improvements are released, apply them to your existing workspace:

```bash
curl -fsSL https://raw.githubusercontent.com/nsisongeffiong/ai-workspace-scripts/main/update-workspace.sh -o update-workspace.sh
chmod +x update-workspace.sh
bash update-workspace.sh
```

---

## Workspace layout

```
~/ai-workspace/
  .shared/
    .env                  <- API keys (gitignored, chmod 600)
    .venv/                <- shared Python virtual environment
    orchestrate.py        <- pipeline logic (downloaded from this repo)
    prompts/
      claude_coder.md     <- Stage 1 system prompt
      gpt_reviewer.md     <- Stage 2 system prompt
      gemini_validator.md <- Stage 3 system prompt
      claude_final.md     <- Stage 4 system prompt
  projects/
    my-project/           <- your project lives here
      src/                <- source code
      brand/              <- brand submodule (if --brand was used)
      reviews/            <- AI review outputs
      scripts/run.py      <- pipeline runner
      prompts/            <- optional project-level prompt overrides
```

---

## Customise prompts for a specific project

```bash
cp ~/ai-workspace/.shared/prompts/claude_coder.md ~/ai-workspace/projects/my-project/prompts/
# Edit the copy to add project-specific rules
```

---

## Update an API key

```bash
nano ~/ai-workspace/.shared/.env
```

---

## Troubleshooting

**Smoke test fails for one provider**
- Anthropic: add credits at [console.anthropic.com](https://console.anthropic.com)
- OpenAI: check key validity at [platform.openai.com](https://platform.openai.com)
- Google: check key at [aistudio.google.com](https://aistudio.google.com)

**`nvm: command not found` after setup**
```bash
source ~/.bashrc
```

**Pipeline output is incomplete (missing files)**

The task may be too large for a single pipeline run. Split it into smaller focused tasks and run the pipeline once per task.

**A provider returns 503 (temporarily unavailable)**

Resume from the failed stage without re-running earlier ones:
```bash
python scripts/run.py --from-stage 3 "Your task here"
```

**`git push` fails with "no upstream branch"**
```bash
git push --set-upstream origin "$(git branch --show-current)"
```

---

## Scripts

| Script | Purpose |
|--------|---------|
| `setup-workspace.sh` | One-time setup on a new machine |
| `new-project.sh` | Create a new pipeline project |
| `update-workspace.sh` | Apply latest improvements to existing workspace |
| `orchestrate.py` | Pipeline logic — downloaded by setup, kept in sync with this repo |

---

## Security notes

- API keys are stored in `~/.ai-workspace/.shared/.env` with `chmod 600` permissions
- The `.env` file is gitignored and never committed
- A `detect-secrets` pre-commit hook is installed to prevent accidental secret commits
- All scripts can be inspected before running — plain bash, no obfuscation

---

*Built with Claude, GPT, and Gemini.*

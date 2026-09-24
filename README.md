# AI Workspace Scripts

A multi-model agentic development pipeline that combines three leading AI models to write, review, and refine code automatically.

## How it works

Every task runs through four stages automatically:

| Stage | Role | Default model | Job |
|-------|------|---------------|-----|
| 1 | Implementation | Claude Opus 5 | Initial implementation |
| 2 | Quality | GPT-5.6 Sol | Code quality & documentation review |
| 3 | Security | Gemini 3.6 Flash | Security & correctness audit |
| 4 | Synthesis | Claude Opus 5 | Final synthesis & corrections |

Synthesis uses the same model as implementation. Each role's model is set in `.env`, so any role can run on another provider. See [Change a role's model](#change-a-roles-model).

Each stage commits to a feature branch. You review the output and merge to main when satisfied.

The pipeline writes all files exactly where the AI specifies, and runs `npm install` or `pip install` automatically after stages that produce a `package.json` or `requirements.txt`.

---

## Requirements

An API key for the provider behind each pipeline role:

- **Implementation and synthesis** (Stages 1 and 4) — Anthropic by default: [console.anthropic.com](https://console.anthropic.com)
- **Quality review** (Stage 2) — OpenAI by default: [platform.openai.com](https://platform.openai.com)
- **Security audit** (Stage 3) — Google by default: [aistudio.google.com](https://aistudio.google.com)

Each role needs a different model. To use another provider for any role, see [Change a role's model](#change-a-roles-model).

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
- Install **Claude Code** and **Codex CLI** as terminal agents
- Export your API keys to `~/.bashrc` so both agents work immediately
- Download `orchestrate.py`, `models.py` and `smoke_test.py` from this repo
- Run a smoke test to confirm every pipeline role can reach its model

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

### Limit which files a run writes

To limit which files a run may write, add a `SCOPE:` line to the task:

```bash
python scripts/run.py "Add retry logic to the payment client.
SCOPE: src/payments/client.ts, src/payments/client.test.ts"
```

Writes to any other path from Stages 1 and 4 are refused and logged. Without a `SCOPE:` line, nothing is restricted.

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

The updater refreshes the pipeline code and shared prompts. It never changes which models or token budgets your `.env` uses, and never touches your API keys.

---

## Workspace layout

```
~/ai-workspace/
  .shared/
    .env                  <- API keys (gitignored, chmod 600)
    .venv/                <- shared Python virtual environment
    orchestrate.py        <- pipeline logic (downloaded from this repo)
    models.py             <- provider adapters and role config (downloaded from this repo)
    smoke_test.py         <- checks each role can reach its model
    prompts/
      implementation.md   <- Stage 1 system prompt
      quality.md          <- Stage 2 system prompt
      security.md         <- Stage 3 system prompt
      synthesis.md        <- Stage 4 system prompt
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
cp ~/ai-workspace/.shared/prompts/implementation.md ~/ai-workspace/projects/my-project/prompts/
# Edit the copy to add project-specific rules
```

---

## Update an API key

```bash
nano ~/ai-workspace/.shared/.env
```

---

## Change a role's model

Each role is configured in `~/ai-workspace/.shared/.env` with a provider and a model. Supported providers are `anthropic`, `openai`, `openai_compatible` and `gemini`. For example, to run the security audit on any OpenAI-compatible endpoint:

```bash
SECURITY_PROVIDER=openai_compatible
SECURITY_MODEL=<model-id>
SECURITY_BASE_URL=https://<provider-endpoint>/v1
SECURITY_API_KEY_ENV=MY_PROVIDER_API_KEY
MY_PROVIDER_API_KEY=...
```

Always set `PROVIDER` and `MODEL` together, and give each role a different model; the pipeline stops if two roles share one. Optional per-role settings (`_REASONING_EFFORT`, `_MAX_OUTPUT_TOKENS`, `_TIMEOUT_SECONDS`, `_TOKEN_PARAM`) are listed in `.env.example`. Reasoning effort values are passed to the provider unchanged, so use a value the model accepts. Run the smoke test after any change:

```bash
python3 ~/ai-workspace/.shared/smoke_test.py
```

The same role settings in a project's own `.env` override the shared ones for that project. Older `CLAUDE_MODEL`, `GPT_MODEL` and `GEMINI_MODEL` settings are converted to role settings automatically, keeping the same models: the updater converts the shared `.env`, and each project's `.env` converts on its next run. Until then the pipeline still reads them.

---

## Terminal agents

Setup installs two terminal agents for out-of-band work — fixes, refinements, and exploratory changes that happen outside a full pipeline run.

| Agent | Command | Provider |
|-------|---------|----------|
| Claude Code | `claude` | Anthropic |
| Codex CLI | `codex` | OpenAI |

Both use the API keys already in your `.env`. See [TERMINAL_AGENTS.md](./TERMINAL_AGENTS.md) for auth options, usage examples, and how to switch between API key and account-based auth.

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
| `models.py` | Provider adapters and role configuration — downloaded alongside `orchestrate.py` |
| `smoke_test.py` | Checks each role can reach its configured model |
| `TERMINAL_AGENTS.md` | Guide to Claude Code and Codex CLI terminal agents |

---

## Security notes

- API keys are stored in `~/.ai-workspace/.shared/.env` with `chmod 600` permissions
- The `.env` file is gitignored and never committed
- A `detect-secrets` pre-commit hook is installed to prevent accidental secret commits
- All scripts can be inspected before running — plain bash, no obfuscation

---

*Built with Claude, GPT, and Gemini.*

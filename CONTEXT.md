# Claude Briefing — AI Workspace Context

Read this file at the start of every new chat to understand the workspace setup.

---

## Workspace overview

This is a multi-model agentic development pipeline that runs on any Unix-based system
(Linux, macOS, or WSL2 on Windows). It combines three AI models to write, review, and fix code automatically.

| Stage | Model | Role |
|-------|-------|------|
| 1 | Claude Opus 4.6 | Initial implementation |
| 2 | GPT-5.4 | Code quality & documentation review |
| 3 | Gemini 2.5 Flash | Security & correctness audit |
| 4 | Claude Opus 4.6 | Final synthesis & corrections |

---

## Directory layout

```
~/ai-workspace/
  .shared/
    .env                  <- API keys (ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY)
    .venv/                <- shared Python virtual environment
    orchestrate.py        <- pipeline logic
    smoke_test.py         <- verifies all three APIs are reachable
    prompts/
      claude_coder.md     <- Stage 1 system prompt (has all project learnings baked in)
      gpt_reviewer.md     <- Stage 2 system prompt
      gemini_validator.md <- Stage 3 system prompt
      claude_final.md     <- Stage 4 system prompt
  projects/
    <project-name>/       <- one folder per project
      src/                <- source code
      reviews/            <- AI review outputs per pipeline run
      scripts/run.py      <- thin wrapper that calls the shared orchestrator
      prompts/            <- optional project-level prompt overrides
```

---

## How to run the pipeline

```bash
cd ~/ai-workspace/projects/<project-name>
source ~/ai-workspace/.shared/.venv/bin/activate
python scripts/run.py "Describe your task here"
```

The pipeline creates a feature branch automatically, runs all four stages, and commits each stage. When done:

```bash
git checkout main
git merge feature/<branch-name>
git push
```

---

## How to create a new project

```bash
bash ~/new-project.sh my-project-name
# Choose language: Python, Node.js, Go, or Generic
```

---

## Key rules Claude must follow in this workspace

### Output rules
- Every code file block MUST have its filepath on the opening fence line
- Example: \`\`\`tsx src/components/Hero.tsx
- Split large tasks into multiple focused files rather than one huge file
- Do NOT rewrite files not explicitly listed in the task

### File modification rules
- For ANY file change -- replacement OR insertion -- always use exact string replacement
- To insert before a known pattern: replace \`<pattern>\` with \`<new_content><pattern>\`
- NEVER use positional arithmetic (find index + offset + slice)
- NEVER use sed for multi-line replacements -- use Python with exact string matching
- Always read the file before modifying to confirm the exact pattern exists

### Script rules
- All scripts must use \`set -euo pipefail\`
- Build failure must exit before git commit
- Always use \`git push origin HEAD\` not bare \`git push\`
- Add idempotency guards so scripts are safe to run twice
- Restore files from git on build failure: \`git checkout HEAD -- <file>\`
- For large commands (>3KB) always create a .sh script file -- never paste directly into terminal

### Stack-specific rules
The shared prompts include gated sections for Python, Node.js/TypeScript, Next.js/React, and Go.
These apply automatically when the project uses that stack — no manual enabling needed.

For project-specific overrides (e.g. a particular DB client pattern or auth helper), copy the
relevant prompt from `~/.ai-workspace/.shared/prompts/` into your project's `prompts/` folder
and edit the local copy. Local copies take precedence; they are never committed to the shared repo.

### Security rules (all stacks)
- All user inputs used in HTML must be HTML-escaped before interpolation
- All admin routes must verify authentication before any data operation
- Never expose secret keys with public/client-side prefixes

---

## Useful commands

```bash
# Run smoke test to verify all three APIs
source ~/ai-workspace/.shared/.venv/bin/activate
python3 ~/ai-workspace/.shared/smoke_test.py

# Update workspace prompts with latest learnings
bash ~/update-workspace.sh

# Open Claude Code in a project
cd ~/ai-workspace/projects/<project-name>
export ANTHROPIC_API_KEY=$(grep ANTHROPIC_API_KEY ~/ai-workspace/.shared/.env | cut -d= -f2)
claude
```

---

## Common issues and fixes

| Issue | Fix |
|-------|-----|
| `nvm: command not found` | `source ~/.bashrc` |
| Pipeline output incomplete | Task too large -- split into smaller focused tasks |
| `git push` fails no upstream | `git push --set-upstream origin "$(git branch --show-current)"` |
| Build fails with type error | Check local interfaces match data schema exactly |
| Stage 3 returns 503 | Gemini overloaded -- wait 5–10 min then `python scripts/run.py --from-stage 3 "..."` |

# TERMINAL_AGENTS.md

This toolchain installs two terminal agents during setup: Claude Code and Codex CLI. Both are available for out-of-band work — fixes, refinements, and exploratory changes that happen outside a full pipeline run. Neither is wired into the 4-stage pipeline itself.

Use whichever agent you prefer, or switch between them based on the task.

---

## Claude Code

A terminal agent from Anthropic. Reads your codebase, edits files, and runs commands from natural language prompts.

**Install**
```bash
npm install -g @anthropic-ai/claude-code
```

**Auth**
Setup exports `ANTHROPIC_API_KEY` from `.env` into your shell environment, so Claude Code works immediately after install. If you run out of API credits, sign out and switch to account-based auth:

```bash
claude auth login
```

Your Pro plan session then takes precedence over the env var. Switch back to API key auth at any time by signing out:

```bash
claude auth logout
```

**Launch**
```bash
claude
```

Run from inside your project directory. Claude Code picks up the project context automatically.

**Common usage**
```bash
claude "fix the broken import in src/api/client.ts"
claude "add error handling to the payment webhook"
claude "why is the build failing"
```

---

## Codex CLI

A terminal agent from OpenAI. Same idea — reads your codebase, edits files, runs commands. Backed by OpenAI's coding-optimised models.

**Install**
```bash
npm install -g @openai/codex
```

**Auth**
Setup exports `OPENAI_API_KEY` from `.env` into your shell environment — the same key Stage 2 of the pipeline uses — so Codex works immediately after install. If you prefer to use a ChatGPT Plus, Pro, or Team subscription instead, sign in via:

```bash
codex auth
```

Your account session then takes precedence over the env var. Switch back to API key auth at any time by signing out:

```bash
codex auth logout
```

**Launch**
```bash
codex
```

**Common usage**
```bash
codex "add input validation to the registration form"
codex --full-auto "run the test suite and fix any failures"
```

Note: Codex sandboxes network access by default. If a task requires network calls (fetching a dependency, calling an API during a test), you may need to approve or adjust the sandbox settings.

---

## Choosing between them

Both agents handle the same class of tasks. Pick based on preference or try both and see which gives better results for your stack. Some things worth knowing:

- Claude Code tends to be stronger on tasks where extended reasoning helps (architectural decisions, untangling messy logic).
- Codex can be a useful second opinion — if one agent's solution doesn't feel right, route the same task to the other.
- Neither agent replaces the pipeline for greenfield work. The pipeline's value is in multi-model diversity (implement → review → audit → synthesise). Terminal agents are for the in-between work: the fix after a review, the tweak after deploy, the exploration before you're ready to run a full pipeline task.

---

## A note on unreviewed changes

Any change made via a terminal agent — Claude Code, Codex, or otherwise — bypasses the multi-model review the pipeline provides. This is expected and often fine for small targeted fixes. For larger changes, or when you want confidence before merging, run the pipeline against the result.

The Code Review Harness (separate project, `github.com/nsisongeffiong`) is being built specifically to audit this kind of out-of-band work.

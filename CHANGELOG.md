# Changelog

All notable changes to ai-workspace-scripts are recorded here.

---

## [Unreleased] — May 2026

### Prompts
- Rewrote `gemini_validator.md` with adversarial framing, structured findings format (severity + confidence levels), chained attack paths section, and explicit out-of-scope exclusions (brand tokens, infra, style). Adapted from a prompt by [@hackSultan](https://x.com/hackSultan).
- `update-workspace.sh` now rewrites `gemini_validator.md` on update (was previously left behind on updates).

---

## [May 2026] — Terminal agents

### Added
- Claude Code and Codex CLI installed as terminal peers by `setup-workspace.sh` during setup. Both are kept current by `update-workspace.sh` (install-if-missing on each run).
- `TERMINAL_AGENTS.md` added to repo — covers install, auth options, common usage, and guidance on choosing between agents.
- API keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) exported to `~/.bashrc` via lazy eval so both agents work immediately after setup. Value is read from `.env` at shell startup, not baked in as a snapshot. Account-based auth (`claude auth` / `codex auth`) takes precedence over env vars when a session exists.
- README updated with terminal agents section.

### Notes
- Neither agent is wired into the 4-stage pipeline — both are for out-of-band work only.
- Codex prompts for signin on first launch regardless of `OPENAI_API_KEY` — one-time onboarding flow, not an auth failure.
- Claude Code auto-update may fail with `ENOTEMPTY` — fix: `rm -rf ~/.nvm/versions/node/v20.20.2/lib/node_modules/@anthropic-ai/claude-code && npm install -g @anthropic-ai/claude-code`.

---

## [May 2026] — Maintenance pass + model upgrades

### Models
- Claude Opus 4.6 → Claude Opus 4.7 (Stages 1 & 4)
- GPT-5.4 → GPT-5.5 (Stage 2)
- Gemini 2.5 Flash → Gemini 3.5 Flash (Stage 3)

### Pipeline
- Stages 1 and 4 now use adaptive thinking with `effort: xhigh` — Claude decides dynamically how much reasoning to invest based on task complexity.
- `MAX_OUTPUT_TOKENS` default raised 16000 → 20000 to account for the Opus 4.7 tokenizer using up to 1.35× more tokens than 4.6.
- All Claude response content now extracted with `"".join(b.text for b in msg.content if b.type == "text")` — safe when thinking blocks are present. Previously used `content[0].text` which is unsafe with adaptive thinking enabled.
- All hardcoded model names removed from log messages, docstrings, review context headers, and git commit messages — now use env vars throughout.
- Brand-budget warning now references `MAX_OUTPUT_TOKENS` env var instead of hardcoded 8192.
- Fixed double-brace bug in `distil_brand_tokens()` API call (heredoc artifact).

### Scripts
- `update-workspace.sh` now patches `.env` model versions and `MAX_OUTPUT_TOKENS` automatically on update.
- `MAX_OUTPUT_TOKENS=20000` added to `.env` and `.env.example` templates in `setup-workspace.sh`.

### Docs
- `CONTEXT.md` — replaced Next.js/Supabase-specific stack guidance with language-agnostic content.
- `README.md` — model table updated.

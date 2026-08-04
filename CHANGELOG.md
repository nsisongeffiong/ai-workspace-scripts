# Changelog

All notable changes to ai-workspace-scripts are recorded here.

---

## [August 2026] — Model migration: Opus 5, GPT-5.6 Sol, Gemini 3.6 Flash

### Models
- Claude Opus 4.7 → **Claude Opus 5** (Stages 1 & 4)
- GPT-5.5 → **GPT-5.6 Sol** (Stage 2)
- Gemini 3.5 Flash → **Gemini 3.6 Flash** (Stage 3)

### Pipeline
- `MAX_OUTPUT_TOKENS` default raised 20000 → **64000**. Not a tokenizer change: Opus 5 uses the same tokenizer introduced with Opus 4.7, so the 1.35× inflation already accounted for in the 4.7 migration still holds. The driver is that Opus 5 has thinking on by default and `max_tokens` is a hard cap on *total* output — thinking and response text share one budget. At `xhigh` effort the thinking share is substantial. The value is a ceiling, not a reservation; only emitted tokens are billed.
- Brand token distillation ceiling raised 1500 → 8000, brand asset placement 256 → 4000, and both pre-flight calls now pass `output_config={"effort": "low"}`. At the old ceilings, thinking consumed the entire budget and returned no text — asset placement would have failed on `json.loads()` of an empty string, and `BRAND_TOKENS.md` would have been written truncated.
- Thinking is deliberately left enabled on the pre-flight calls rather than disabled. With thinking disabled, Opus 5 can occasionally emit internal XML tags into visible output, which would corrupt the JSON that the placement call parses. Low effort achieves the same token saving without that risk.
- Stages 1 and 4 remain at `xhigh`. `max` is available on Opus 5 but is deliberately not adopted in the same pass, so any regression stays attributable to the model change alone.
- `thinking={"type": "adaptive"}` is retained on Stages 1 and 4. It remains valid on Opus 5 and is equivalent to the default.
- Stage 2 and Stage 3 review ceilings left at 16000. Both GPT-5.6 and Gemini 3.6 Flash are more token-efficient than their predecessors.

### Fix
- `smoke_test.py` would have failed against Opus 5 on two counts: `max_tokens=20` left no budget for response text once thinking was on, and `r.content[0].text` raises `AttributeError` when the first block is a thinking block. Ceiling raised to 2000 with `effort: low`, and content is now extracted with the same `"".join(b.text for b in ... if b.type == "text")` pattern already used throughout `orchestrate.py`.
- `smoke_test.py` OpenAI check raised `AttributeError` on a `None` message when reasoning tokens consumed the whole ceiling. Ceiling raised to 2000 and the result is coalesced before `.strip()`.
- `smoke_test.py` Gemini check fell back to the literal string `"OK"` on an empty response, reporting a pass for a failed call. The fallback is removed and `check()` now treats an empty result as a failure.

### Scripts
- `update-workspace.sh` gained migration rules for the new stack. Rules are sequential, so a `.env` still on the 4.6-era stack migrates through both hops in a single run and lands on Opus 5 / GPT-5.6 Sol / Gemini 3.6 Flash with `MAX_OUTPUT_TOKENS=64000`.
- The Gemini migration rule is anchored with `$` so it cannot match `gemini-3.5-flash-lite`, which now exists.
- Sanity-check patterns updated for the new model strings.

### Docs
- README model table updated.

### Notes
- Gemini 3.5 Flash Cyber was evaluated for Stage 3 and rejected: it is a limited-access pilot for governments and trusted partners via CodeMender, with no public API.
- Opus 5 writes longer by default and verifies its own work unprompted. The standing caution that Stage 4 can override correct Stage 1 work may be amplified — diff Stage 4 output against Stage 1 on the first few runs.
- Anthropic advises removing carried-over "add a verification step" instructions from prompts on Opus 5. The prompt heredocs were audited and contain none, so no prompt changes were needed in this pass.

---

## [May 2026] — Pipeline improvements

### Fix
- model-supplied file paths are now resolved and validated before writing. Absolute paths, path traversal (`../`), and sensitive targets (`.git`, `.env`, `.env.local`, `.env.production`) are blocked and logged as warnings.
- `extract_code_blocks()` now returns the list of paths it wrote. Stage 1 and Stage 4 pass that list directly to `git_commit()` instead of staging broad directories like `src/`. Root-level files (e.g. `package.json`, `tsconfig.json`) are now committed correctly.
- `git_commit()` no longer swallows exceptions silently. Failures are logged at ERROR level and re-raised, stopping the pipeline rather than reporting false success.
- `distil_brand_tokens()` and `check_brand_budget()` were defined but never called. Both are now invoked at the start of `setup_brand_assets()`, so brand distillation and budget warnings run on every branded project.
- Node scaffold in `new-project.sh` used `jest` as the default test runner, but Jest was never installed. Replaced with `node --test`, which is built into Node 20 and requires no dependencies.

### Chore
- added to enforce LF line endings on `.sh`, `.py`, and `.md` files, preventing CRLF breakage when contributors clone on Windows.

---

## [May 2026] — Review token limits

### Fix
- Bumped `max_completion_tokens` (Stage 2, GPT) and `max_output_tokens` (Stage 3, Gemini) from 4096 → 16000. GPT-5.5 is a reasoning model and consumes internal tokens before producing visible output — at 4096 the response was hitting the ceiling with `finish_reason: length` and returning an empty string. Both review files were writing 0 chars as a result.
- Stage 4 now receives Stage 1's raw output directly instead of calling `read_src()`, which diffs all files since the initial scaffold commit. On multi-phase builds this was pulling the entire accumulated source into Stage 4's context, pushing token usage to the 20k ceiling and truncating output. Falls back to `read_src()` when resuming from `--from-stage 4`.

---

## [May 2026] — gemini_validator rewrite

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

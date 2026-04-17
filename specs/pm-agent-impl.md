# PM Agent Implementation

> Python CLI tool that interviews the user, audits existing n8n workflows, and produces a JSON spec the Build Agent can consume. INTERVIEW → AUDIT → DECOMPOSE → REVIEW → FIX → VALIDATE → OUTPUT.

| Field | Value |
|-------|-------|
| Status | Implemented (58/58 tests pass). First live run completed 2026-04-15 (weather brief). |
| Last Updated | 2026-04-15 |
| Depends On | pm-agent-spec.md (design), build-agent models.py (output contract), n8n API (read-only) |
| Enables | End-to-end: natural language → spec JSON → live workflow |

---

## Recent Changes

| Date | What changed | Section |
|------|-------------|---------|
| 2026-04-15 | First live end-to-end run (weather brief). Fixed: max_tokens 4096→8192 for decompose/review/fix, Windows UTF-8 encoding crash, reviewer list-vs-dict normalization, fix_spec list-unwrap guard. Deduplicated translation logic (node_catalog.py now imports from wire.py). Stripped `connections` field from specs (build agent ignores it). Updated test count 28→58. | All |
| 2026-04-13 | Fixed import path (hyphen → underscore rename), added review→fix loop, split decomposer into structural + translation layers, added model selection per phase, added sample brief fixture, specified interactive mode protocol, single-source node catalog | All |
| 2026-04-13 | Updated stale SAM.gov reference → Lead Qualification, added format rules to node_catalog prompt requirement | Current State, Implementation Plan |
| 2026-04-13 | Initial spec | All |

---

## Goal

Build a Python tool at `agents/pm_agent/` that takes a natural language description of what someone wants automated and produces a validated JSON spec file that the Build Agent can consume directly (`python -m agents.build_agent build spec.json`). The PM Agent handles the thinking — what nodes to use, how to connect them, what could go wrong — so the user just describes what they want.

## Success Criteria

- Given a natural language description, produces a valid spec JSON that passes `parse_spec()` from the build agent's `models.py`
- Interviews the user with targeted questions (skips what it can infer)
- Audits existing n8n workflows before designing (no duplicates)
- Runs adversarial review, applies fixes, and re-reviews before finalizing
- Includes test cases in the spec (happy path + edge cases + error cases)
- Works from CLI: `python -m agents.pm_agent plan "description"` → interactive interview → spec.json
- Also works non-interactively: `python -m agents.pm_agent plan --from-brief brief.md` → spec.json (no interview, infers everything from the brief)

---

## Current State

- PM Agent fully implemented and tested (58/58 tests pass)
- First live end-to-end run completed 2026-04-15: `weather-brief.md` → PM Agent → `daily-nyc-weather-summary-with-freezing-alert-spec.json` (5 steps, 13 test cases, 0 criticals after review)
- Build Agent done and working — consumes spec JSON via `parse_spec()` in `models.py`
- Echo spec (`workflows/test-data/echo-spec.json`) is a working example of the output format
- Weather spec (`workflows/test-data/daily-nyc-weather-summary-with-freezing-alert-spec.json`) is the first real PM Agent output
- n8n API client exists in `agents/build_agent/client.py` (imported by `auditor.py` for workflow audit)

---

## Prerequisite: Rename Agent Directories

**Before implementation**, rename the agent directories from hyphens to underscores so Python can import them as packages:

```
agents/build-agent/  →  agents/build_agent/
agents/pm-agent/     →  agents/pm_agent/
```

Update all references in CLAUDE.md, specs, and test imports. The CLI invocations change accordingly:
- `python -m agents.build_agent build spec.json`
- `python -m agents.pm_agent plan "description"`

This is a one-time rename. Without it, neither agent can import from the other.

---

## Design

### Architecture

The PM Agent is an LLM-powered tool. Unlike the Build Agent (which is deterministic Python making API calls), the PM Agent needs an LLM to reason about user intent, select node types, design error handling, and run adversarial review.

```
agents/pm_agent/
├── __init__.py
├── __main__.py           # CLI entry point
├── interviewer.py        # Phase 1: Conduct interview, extract requirements
├── auditor.py            # Phase 2: Check existing workflows for conflicts/reuse
├── decomposer.py         # Phase 3: LLM picks steps/gates, Python translates to n8n JSON
├── reviewer.py           # Phase 4+5: Adversarial review → fix loop (max 2 iterations)
├── validator.py          # Phase 6: Validate spec against build agent schema
├── node_catalog.py       # Single source: n8n node types, formats, rendered into prompts at runtime
├── llm.py                # Thin wrapper: call Claude with prompt, return text/JSON
├── prompts/
│   ├── interview.md      # System prompt for adaptive interview
│   ├── decompose.md      # System prompt for structural design (steps, gates, data flow)
│   ├── review.md         # System prompt for adversarial review
│   └── fix.md            # System prompt for applying review fixes to spec
└── README.md
```

### Key Design Decisions

**Two-layer decomposition: LLM designs, Python translates.** The decomposer makes two passes. First, the LLM produces a *structural* spec — steps, gates, data flow, error handling described in plain language with node types selected but parameters in pseudocode. Then, `node_catalog.py` deterministically translates the pseudocode parameters into exact n8n JSON format (IF v2 combinator, Set v3.4 nested assignments, webhook responseMode, etc.). This separates what the LLM is good at (reasoning about workflow design) from what it's bad at (getting finicky JSON nesting right).

**Review → Fix loop, not just review.** After the adversarial review finds problems, the LLM gets a second call with the spec + findings and produces an updated spec. Then re-review. Max 2 iterations — same pattern as the Build Agent's HARDEN loop. If CRITICALs remain after 2 iterations, the tool exits with the findings and lets the user decide.

```
DECOMPOSE → REVIEW → findings? → FIX → re-REVIEW → clean? → yes → validate
                                                      ↓ no
                                                   FIX → re-REVIEW (max 2)
                                                      ↓ still not clean
                                                   Exit with findings
```

**Model selection per phase.** Different phases have different reasoning demands:

| Phase | Model | Why |
|-------|-------|-----|
| Interview | `claude-haiku-4-5-20251001` | Extracting structured info from natural language — fast, cheap |
| Decompose | `claude-sonnet-4-6` | Core reasoning: node selection, gate placement, error paths |
| Review | `claude-sonnet-4-6` | Adversarial analysis needs strong reasoning |
| Fix | `claude-sonnet-4-6` | Applying fixes without breaking other parts |

Estimated cost per PM run: ~$0.02-0.08 depending on complexity. Cheaper than the workflow it's planning.

**Single-source node catalog.** `node_catalog.py` is a Python dict that serves two purposes: (1) rendered into the decompose prompt at runtime so the LLM knows what nodes are available, and (2) used by the translation layer to convert pseudocode parameters to exact n8n JSON. No separate `prompts/node_catalog.md` file — the prompt template calls `render_catalog()` to inject the catalog inline. One source, no drift.

**Claude API via Anthropic SDK.** Uses `anthropic` Python SDK. This is the one external dependency.

**The output contract is `WorkflowSpec` from `models.py`.** The PM Agent imports and validates against the same schema the Build Agent consumes. If `parse_spec()` accepts it, the Build Agent will too.

**Interactive interview protocol.** In interactive mode, the interviewer makes one LLM call with the user's description, which returns: (a) a list of inferred answers, and (b) a list of remaining questions to ask. The CLI prints the inferences for confirmation, then asks each remaining question one at a time via stdin. User responds in free text. After all answers are collected, one final LLM call consolidates into the structured requirements dict. Total: 2 LLM calls for the interview phase.

**Non-interactive mode exits on insufficient info.** `--from-brief brief.md` makes one LLM call that attempts to infer all 8 answers. If the LLM determines any critical question (Q1 outcome, Q2 trigger, Q3 stakes) can't be answered from the brief, the tool prints what's missing and exits with code 1 rather than guessing. Non-critical questions (budget, editors) use sensible defaults.

**Spec quality gate before output.** Before writing the file, validate:
- `parse_spec()` accepts it (schema valid)
- Every step with `determinism: "3.0"` has a gate after it
- `test_cases` array has at least 3 entries (happy path, edge case, error case)
- `security.credentials_needed` lists all credentials referenced in step parameters
- No review findings at CRITICAL severity remain

If any check fails, loop back to decomposer with the error.

---

## This Spec Covers

- Prerequisite directory rename (hyphen → underscore)
- CLI entry point (interactive + non-interactive modes)
- LLM-powered adaptive interview
- Existing workflow audit (via n8n API)
- Two-layer spec decomposition (LLM structural design + Python n8n translation)
- Adversarial review → fix loop
- Output validation against build agent schema
- n8n node catalog (single source for prompts + translation)
- Sample brief fixture for testing

## Out of Scope

- Build Agent (done) → `specs/build-agent-impl.md`
- Component library management → deferred
- Multi-workflow MECE planning → deferred (single workflow focus first)
- Spec iteration ("change the trigger to webhook") → deferred, currently requires a new run

---

## Sample Session

```
$ python -m agents.pm_agent plan "When a new lead is added to HubSpot, score them against our ICP using AI, and if they score above 80, notify the sales rep on Slack"

PM Agent: From your description, I infer:
  - Outcome: Score leads against ICP, notify on high scores
  - Trigger: HubSpot webhook (new contact created)
  - Systems: HubSpot, LLM API, Slack
  - Output: Slack message to sales rep

Let me confirm what I can't infer:

1. What happens if the scoring gets it wrong? (low/medium/high stakes)
   > medium — sales rep wastes time on a bad lead but nothing critical

2. Budget per run? HubSpot webhook fires per lead, LLM scoring costs tokens.
   > keep it under $0.10 per lead

PM Agent: Auditing existing workflows...
  - 0 existing workflows
  - No conflicts

PM Agent: Designing workflow...

  Workflow: HubSpot Lead Scorer
  Trigger: HubSpot webhook (new contact)
  Steps:
    1. Validate lead data (email, company non-empty) [IF]
    2. Score lead against ICP [LLM → markdown]
    3. Parse score from markdown [Code → JSON]
    4. Gate: validate score (0-100, has rationale) [IF]
    5. Route: score >= 80 → Slack, else → log only [IF]
    6. Send Slack notification [HTTP Request]

  Adversarial review (iteration 1):
  | # | Category | Finding | Severity | Resolution |
  | 1 | Security | HubSpot webhook needs auth | WARNING | Added HMAC step |
  | 2 | Failure | LLM could timeout | INFO | 30s timeout, retry 2x |
  | 3 | Scope | Score exactly 80 not handled | INFO | Changed to >= 80 |

  Adversarial review (iteration 2): Clean — 0 critical, 0 warning

  Test cases: 5 (happy path, low score, edge score 80, LLM failure, webhook auth failure)
  Estimated cost: $0.02-0.05 per lead

  Save to workflows/test-data/hubspot-lead-scorer-spec.json? [Y/n]
  > y

Spec saved. Build it:
  python -m agents.build_agent build workflows/test-data/hubspot-lead-scorer-spec.json
```

---

## Sample Brief (Non-Interactive Test Fixture)

Save to `workflows/test-data/sample-brief.md` — used to test the `--from-brief` path.

```markdown
# Webhook Echo Workflow

I need a simple workflow that receives a POST request with a JSON body
containing "name" (string) and "value" (number 0-100).

It should validate that name is non-empty and value is in range. If valid,
echo back the input with status "ok" and a timestamp. If invalid, return
status "error" with a message explaining what's wrong.

This is for testing — low stakes. No external APIs, no credentials needed.
The trigger is a webhook at path "echo-test".
```

This brief is detailed enough that `--from-brief` should produce a spec without asking any questions, and the output should closely match the existing `echo-spec.json`. This makes it a verifiable test: diff the PM Agent's output against the known-good echo spec.

---

## Implementation Plan

### Phase 0: Prerequisite

| # | Task | Done When |
|---|------|-----------|
| 0 | Rename `agents/build-agent/` → `agents/build_agent/`, `agents/pm-agent/` → `agents/pm_agent/` | Directories renamed. All imports in test files updated (`sys.path` lines, `from client import` etc.). `python -m pytest agents/build_agent/tests/ -v` still passes 54/54. CLAUDE.md and all specs updated to reference `build_agent`/`pm_agent`. CLI is `python -m agents.build_agent build ...`. |

### Phase 1: Foundation

| # | Task | Done When |
|---|------|-----------|
| 1 | Node catalog + output validator (`node_catalog.py`, `validator.py`) | **node_catalog.py**: Python dict of n8n node types with type string, current typeVersion, parameter format example (exact n8n JSON, not pseudocode), and "when to use" text. Covers: webhook (v2), scheduleTrigger (v1.2), manualTrigger (v1), httpRequest (v4.2), set (v3.4), if (v2, with combinator), code (v2), openAi (for LLM). Includes a `render_catalog()` function that formats the dict into a markdown string for prompt injection. Also includes a `translate_params(node_type, pseudocode_params) → n8n_params` function that converts pseudocode parameters to exact n8n format (reuses the translation logic from build agent's `wire.py`). **validator.py**: Imports `parse_spec` from `agents.build_agent.models`, validates a spec dict, also checks: every 3.0 step has a gate after it, test_cases has ≥3 entries, credentials_needed lists all creds in step params. Returns errors list or empty list. Confirmed: echo-spec.json passes, malformed spec fails with clear messages. |
| 2 | LLM wrapper + prompt templates (`llm.py`, `prompts/*.md`) | **llm.py**: Thin wrapper over `anthropic.Anthropic()`. Functions: `call(model, system_prompt, user_message) → str` and `call_json(model, system_prompt, user_message) → dict` (parses JSON from response, retries once on parse failure). Reads `ANTHROPIC_API_KEY` from env. **Prompts**: `interview.md` — lists 8 questions, instructs to return JSON with `inferred` and `questions_to_ask` fields. `decompose.md` — takes requirements + `{node_catalog}` placeholder, instructs to return JSON spec with steps in pseudocode params. `review.md` — takes spec + requirements, instructs to return findings array. `fix.md` — takes spec + findings, instructs to return updated spec JSON. Each prompt loaded and formatted via `load_prompt(name, **vars)` helper. Tested: `call()` returns a string from Claude, `call_json()` returns a parsed dict. |

### Phase 2: Core Pipeline

| # | Task | Done When |
|---|------|-----------|
| 3 | Interviewer (`interviewer.py`) | **Interactive mode**: (1) One Haiku call with description → returns `{"inferred": {...}, "questions": [...]}`. (2) CLI prints inferences, asks each remaining question via stdin, collects free-text answers. (3) One Haiku call with description + all answers → returns structured requirements dict. Total: 2 LLM calls. **Non-interactive mode**: One Haiku call with full brief text → returns requirements dict. If any critical field (outcome, trigger, stakes) is empty, exits with error listing what's missing. Tested: interactive mode with "Monitor SAM.gov for contracts and email a digest" skips Q1/Q2, asks Q3/Q5-Q8. Non-interactive with `sample-brief.md` returns complete requirements without errors. |
| 4 | Auditor + decomposer (`auditor.py`, `decomposer.py`) | **auditor.py**: Uses `N8nClient` from `agents.build_agent.client` to list workflows, summarizes each (name, trigger, node count), flags conflicts. Returns audit dict. **decomposer.py**: Two-layer process. (1) Sonnet call with requirements + audit + rendered node catalog → returns structural spec JSON (steps with pseudocode params, gates, test_cases). (2) Python `translate_params()` from `node_catalog.py` converts each step's pseudocode params to exact n8n JSON. (3) Validates with `parse_spec()` — if fails, one retry with error appended to prompt. Tested: given echo-level requirements, produces a spec that passes `parse_spec()` and the extended validator checks (gates after LLM steps, ≥3 test cases). |

### Phase 3: Review + Ship

| # | Task | Done When |
|---|------|-----------|
| 5 | Reviewer + fix loop (`reviewer.py`) | **review()**: Sonnet call with spec + requirements → returns `[{category, severity, finding, resolution}, ...]`. Checks 5 categories: missing steps, failure modes, scope, security, cost. **fix()**: Sonnet call with spec + findings → returns updated spec JSON, re-translated through `translate_params()`, re-validated. **review_loop()**: REVIEW → if CRITICAL/WARNING → FIX → re-REVIEW → max 2 iterations. Returns `(final_spec, final_findings)`. Tested: given echo spec, reviewer finds missing webhook auth (WARNING). Fix loop applies the fix and re-review comes back clean. |
| 6 | CLI entry point + sample brief (`__main__.py`, `workflows/test-data/sample-brief.md`) | Full pipeline: `python -m agents.pm_agent plan "description"` runs interview → audit → decompose → review loop → validate → save. `--from-brief brief.md` skips interactive interview. `--output path.json` controls save location (default: `workflows/test-data/{workflow-name}-spec.json`). Prints review findings and spec summary before saving. Confirms with user before write (skipped in `--from-brief` mode). Exit 0 on success, 1 on failure. **End-to-end test**: `--from-brief sample-brief.md` produces a spec that (a) passes `parse_spec()`, (b) describes a webhook echo workflow, (c) has ≥3 test cases, (d) can be built by `python -m agents.build_agent build`. |

---

## Testing Strategy

LLM outputs are non-deterministic. Tests validate **structure**, not exact content.

| What to test | How |
|---|---|
| Spec passes `parse_spec()` | Deterministic — schema validation |
| Every LLM step has a gate | Deterministic — iterate steps, check gates |
| Test cases ≥ 3 with happy/edge/error | Deterministic — count and check names |
| Credentials listed match step params | Deterministic — scan params for cred references |
| Interview skips inferable questions | Check `questions_to_ask` array length < 8 for a detailed description |
| Reviewer finds real issues | Check findings list is non-empty for a spec with known weaknesses |
| Fix loop converges | Check iteration count ≤ 2 and final CRITICALs = 0 |

Tests that depend on LLM output use **assertions on structure** (key exists, type correct, count in range) not **assertions on content** (exact string match).

---

## Dependencies

```
pip install anthropic
```

The `ANTHROPIC_API_KEY` env var must be set (or in `.env`).

---

## Interface with Build Agent

The PM Agent imports from the build agent package:

```python
# Validate output spec against build agent's schema
from agents.build_agent.models import parse_spec, ValidationError

# Audit existing workflows via build agent's n8n client
from agents.build_agent.client import N8nClient

# Reuse parameter translation logic
from agents.build_agent.wire import _translate_set_params, _translate_if_params
```

This ensures the output contract stays in sync. The parameter translation functions are reused rather than reimplemented — if the build agent's wire phase learned a new n8n format quirk, the PM agent gets it for free.

**Note**: The `_translate_*` functions in `wire.py` are currently private (prefixed `_`). Task 0 or Task 1 should make them public (rename to `translate_set_params`, `translate_if_params`) since they're now part of the shared interface.

---

## Open Questions

1. ~~Should the PM Agent use the Anthropic SDK or raw HTTP?~~ **Resolved**: Anthropic SDK.
2. ~~How should the interview work in non-interactive mode?~~ **Resolved**: One LLM call, exit with error if critical fields can't be inferred.
3. ~~Where is the node catalog source of truth?~~ **Resolved**: `node_catalog.py` only. Rendered into prompts at runtime via `render_catalog()`.
4. Should the PM Agent support iterating on a spec? (e.g., "change the trigger to webhook instead of cron") Currently planned as a new run — no in-place editing. Could add later.
5. How to handle the node catalog getting stale? Current plan: static Python dict, updated manually when we discover new node quirks. The `translate_params()` functions imported from build agent's `wire.py` are the authoritative source for n8n parameter formats.
6. ~~Should the `_translate_*` functions in wire.py be extracted to a shared module?~~ **Resolved (2026-04-15)**: `node_catalog.py` now imports `_translate_set_params` and `_translate_if_params` directly from `wire.py`. One source of truth, no duplication.

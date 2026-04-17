# Build Agent Implementation

> Python CLI tool that reads a PM Agent workflow spec (JSON) and builds a live n8n workflow via the REST API, following SCAFFOLD → WIRE → TEST → AUDIT → HARDEN → CODIFY → DEPLOY.

| Field | Value |
|-------|-------|
| Status | Done |
| Last Updated | 2026-04-13 |
| Depends On | build-agent-spec.md (design), n8n API (verified working) |
| Enables | Automated workflow creation from specs |

---

## Recent Changes

| Date | What changed | Section |
|------|-------------|---------|
| 2026-04-13 | Added credential resolution, non-webhook trigger testing strategy, `--dry-run` and `--resume` CLI flags, synced test_cases with PM Agent spec | Design, Implementation Plan, Open Questions |
| 2026-04-13 | Added sample spec (webhook echo), resolved test data question, split task 7, added status tracking to foundation, failure/cleanup strategy, client update pattern | Design, Implementation Plan, Open Questions |
| 2026-04-13 | Initial spec | All |

---

## Goal

Build a Python tool at `agents/build-agent/` that a Claude agent (or human) can invoke to turn a PM Agent JSON spec into a live, tested, hardened n8n workflow. The tool handles the mechanical work — API calls, node wiring, test execution, audit checks — so the agent focuses on decisions, not plumbing.

## Success Criteria

- Given a valid PM spec JSON, produces a running n8n workflow with all nodes wired and tested
- Audit checks run automatically; findings reported in structured format
- Status table printed after each phase (no silent skips)
- Works from CLI: `python -m build_agent build spec.json`
- Each phase is independently callable for debugging: `python -m build_agent scaffold spec.json`

---

## Current State

- n8n API plumbing verified (POST, PUT, GET, DELETE, activate, deactivate, webhook send, execution check)
- API quirks documented in CLAUDE.md (PUT not PATCH, `={{ }}` expression syntax, full body required on update)
- Design spec complete at `specs/build-agent-spec.md`
- `agents/build-agent/` directory exists, empty

---

## Design

### Architecture

Single Python package. No frameworks, no dependencies beyond stdlib. Talks to n8n over HTTP.

```
agents/build-agent/
├── __main__.py           # CLI entry point
├── client.py             # n8n API client (thin wrapper over urllib)
├── scaffold.py           # Phase 1: Create workflow skeleton
├── wire.py               # Phase 2: Configure and connect nodes
├── test_runner.py        # Phase 3: Send test data, verify results
├── auditor.py            # Phase 4: Security, best practices, resilience checks
├── harden.py             # Phase 5: Fix audit findings
├── deploy.py             # Phase 7: Activate and smoke test
├── models.py             # Spec JSON schema (dataclasses, validation)
├── status.py             # Build status table tracking
└── tests/                # 54 tests (integration + unit)
```

### Key Design Decisions

**Python stdlib only.** No pip installs. `urllib.request` for HTTP, `json` for parsing, `dataclasses` for models. This keeps the tool zero-dependency and easy to run in any Claude Code session.

**Spec JSON is the input contract.** The tool reads the PM Agent's JSON spec (as defined in pm-agent-spec.md output format). It does not interview users or make scope decisions.

**Each phase is a function.** `scaffold(spec, client) → workflow_id`, `wire(spec, client, workflow_id) → wired_workflow`, etc. Composable for debugging — you can run one phase at a time.

**GET-modify-PUT pattern for updates.** Since n8n has no PATCH, every update fetches the current workflow, modifies it in memory, and PUTs the full object back. The client exposes a single `update_workflow(id, modifier_fn)` method that handles GET-modify-PUT internally so callers never think about it.

**Audit checks are deterministic where possible.** Parse the workflow JSON and check for known patterns (hardcoded creds, missing auth, default names). Only use LLM for checks that require reasoning (e.g., "is this permission scope appropriate?").

**Test data is embedded in the spec.** The PM Agent knows what "right" looks like — it supplies test cases in a `test_cases` array in the spec JSON. Each test case has an `input` payload and `expected` output description. The build agent does not generate test data.

**On failure, leave the workflow for debugging.** If a phase fails mid-build, the partially-built workflow stays in n8n. The build agent reports the workflow ID and which phase failed so the user can inspect it in the n8n UI. No automatic cleanup — half-built workflows are useful for diagnosing what went wrong.

**Credential resolution happens in WIRE.** The spec uses credential names (e.g., `"sam_gov_api_key"`), not n8n credential IDs. The client exposes `list_credentials()` via `GET /api/v1/credentials`, and the wire phase resolves names → IDs before configuring nodes. If a required credential doesn't exist, wire fails with a clear error listing the missing credential name.

**Non-webhook triggers use a temporary manual trigger for testing.** Cron-triggered and event-triggered workflows can't be tested by sending webhook data. During the TEST phase, the build agent temporarily adds a manual trigger node alongside the real trigger, runs test data through the manual trigger, then removes it before DEPLOY. This is the only phase where the workflow's structure temporarily diverges from the spec.

**`--dry-run` validates without touching n8n.** `python -m build_agent build spec.json --dry-run` parses the spec, validates all required fields, resolves credential names against n8n, prints the planned node layout and connections, and exits. No workflow created. Useful for debugging PM Agent output before committing to a build.

**`--resume` picks up from a failed phase.** `python -m build_agent build spec.json --resume abc123` takes a workflow ID from a previous failed build and resumes from the last incomplete phase. The build agent GETs the workflow, inspects its state (how many nodes are configured, which connections exist), infers which phase to start from, and continues. This avoids rebuilding from scratch when a single phase fails mid-build.

**CODIFY is deferred.** The status table will show `CODIFY | skipped (deferred)` until we have 3+ workflows built and patterns to extract. No `codify.py` file for now.

---

## This Spec Covers

- n8n API client
- Sample spec fixture for development/testing
- SCAFFOLD phase
- WIRE phase
- TEST phase (webhook-based)
- AUDIT phase (3 check categories, deterministic)
- HARDEN phase (fix + re-audit loop)
- DEPLOY phase (activate + smoke test)
- CLI entry point
- Build status tracking

## Out of Scope

- PM Agent implementation → `specs/pm-agent-spec.md`
- CODIFY phase (extracting components) → deferred until we have 3+ workflows built
- Component library format → deferred
- LLM-powered audit checks → can be added later; start with deterministic checks only

---

## Sample Spec: Webhook Echo

A minimal workflow spec used as the test fixture during development. Simple enough to verify every phase works, complex enough to exercise gates and error handling.

**What it does:** Receives a webhook POST with `{"name": "...", "value": N}`, validates the input (name is non-empty, value is 0-100), and returns a response with the input echoed back plus a `status` field and `timestamp`.

```json
{
  "workflow_name": "Webhook Echo",
  "description": "Receives a POST, validates input, echoes back with status and timestamp",

  "trigger": {
    "type": "webhook",
    "path": "echo-test",
    "method": "POST",
    "description": "Webhook receiving JSON POST"
  },

  "steps": [
    {
      "id": "step_1",
      "name": "Validate Input",
      "node_type": "n8n-nodes-base.if",
      "determinism": "1.0",
      "description": "Check that name is non-empty string and value is number 0-100",
      "parameters": {
        "conditions": {
          "and": [
            { "field": "={{ $json.body.name }}", "operation": "isNotEmpty" },
            { "field": "={{ $json.body.value }}", "operation": "gte", "value": 0 },
            { "field": "={{ $json.body.value }}", "operation": "lte", "value": 100 }
          ]
        }
      },
      "input_shape": { "name": "string", "value": "number" },
      "output_shape": { "pass": "same as input", "fail": "same as input" },
      "error_handling": { "on_failure": "route_to_error_branch" }
    },
    {
      "id": "step_2",
      "name": "Build Success Response",
      "node_type": "n8n-nodes-base.set",
      "determinism": "1.0",
      "description": "Echo input back with status=ok and timestamp",
      "parameters": {
        "assignments": [
          { "name": "status", "value": "ok", "type": "string" },
          { "name": "name", "value": "={{ $json.body.name }}", "type": "string" },
          { "name": "value", "value": "={{ $json.body.value }}", "type": "number" },
          { "name": "received_at", "value": "={{ $now.toISO() }}", "type": "string" }
        ]
      },
      "input_shape": { "name": "string", "value": "number" },
      "output_shape": { "status": "string", "name": "string", "value": "number", "received_at": "string" }
    },
    {
      "id": "step_3",
      "name": "Build Error Response",
      "node_type": "n8n-nodes-base.set",
      "determinism": "1.0",
      "description": "Return error when validation fails",
      "parameters": {
        "assignments": [
          { "name": "status", "value": "error", "type": "string" },
          { "name": "message", "value": "Invalid input: name must be non-empty, value must be 0-100", "type": "string" }
        ]
      },
      "input_shape": "any",
      "output_shape": { "status": "string", "message": "string" }
    }
  ],

  "gates": [
    {
      "after_step": "step_1",
      "type": "conditional_branch",
      "description": "Route valid input to success response, invalid to error response",
      "pass_to": "step_2",
      "fail_to": "step_3"
    }
  ],

  "error_handling": {
    "global_timeout_seconds": 30,
    "on_workflow_failure": "return_error_response"
  },

  "output": {
    "destination": "webhook_response",
    "format": "JSON",
    "description": "Returns JSON response to the webhook caller"
  },

  "security": {
    "credentials_needed": [],
    "pii_handling": "none",
    "data_flow_notes": "No external calls. Input echoed back to caller."
  },

  "cost_estimate": {
    "per_run": {
      "api_calls": "0 (no external APIs)",
      "estimated_tokens": "0",
      "estimated_cost": "$0"
    }
  },

  "test_cases": [
    {
      "name": "Happy path",
      "input": { "name": "test-item", "value": 42 },
      "expected": {
        "status": "ok",
        "name": "test-item",
        "value": 42,
        "received_at": "any non-empty string"
      }
    },
    {
      "name": "Empty name rejected",
      "input": { "name": "", "value": 50 },
      "expected": {
        "status": "error",
        "message": "Invalid input: name must be non-empty, value must be 0-100"
      }
    },
    {
      "name": "Value too high rejected",
      "input": { "name": "test", "value": 150 },
      "expected": {
        "status": "error",
        "message": "Invalid input: name must be non-empty, value must be 0-100"
      }
    },
    {
      "name": "Value negative rejected",
      "input": { "name": "test", "value": -5 },
      "expected": {
        "status": "error",
        "message": "Invalid input: name must be non-empty, value must be 0-100"
      }
    },
    {
      "name": "Missing fields rejected",
      "input": {},
      "expected": {
        "status": "error"
      }
    }
  ],

  "components_used": [],
  "components_needed": []
}
```

This spec exercises: webhook trigger, IF node (gate), two Set nodes (success/error branches), conditional routing, input validation, and all 5 test categories. Simple enough to debug by hand, complete enough to prove the build pipeline works.

---

## Implementation Plan

### Phase 1: Foundation

| # | Task | Done When |
|---|------|-----------|
| 1 | ~~n8n API client + status tracker (`client.py`, `status.py`)~~ | ✅ Done. 10 integration tests + 8 unit tests pass. URLError handling added during audit. |
| 2 | ~~Spec parser + models + sample fixture (`models.py`, `workflows/test-data/echo-spec.json`)~~ | ✅ Done. 13 tests pass (7 parsing + 6 validation). Echo spec fixture saved. |

### Phase 2: Build Pipeline

| # | Task | Done When |
|---|------|-----------|
| 3 | ~~SCAFFOLD phase (`scaffold.py`)~~ | ✅ Done. 9 integration tests pass. Creates trigger + step nodes, positions left-to-right, error branches below. |
| 4 | ~~WIRE phase (`wire.py`)~~ | ✅ Done. 7 integration tests pass. Translates spec IF conditions to n8n v2 format (requires `combinator: "and"`), Set assignments to n8n nested format. Credential resolution deferred — echo spec has none. |

### Phase 3: Verify + Harden

| # | Task | Done When |
|---|------|-----------|
| 5 | ~~TEST phase (`test_runner.py`)~~ | ✅ Done. 7 integration tests pass. Full pipeline: scaffold → wire → activate → send 5 test cases → verify responses → deactivate. Happy path + 4 validation failures all correct. Supports "any non-empty string" matcher for dynamic fields like timestamps. |
| 6 | ~~AUDIT phase (`auditor.py`) + HARDEN loop (`harden.py`)~~ | ✅ Done. 3 audit categories (security, best practices, resilience) with deterministic checks. Harden loop auto-fixes: timeout, error save settings, HTTP retry. Unfixable findings (hardcoded creds, default names) left for human review. |

### Phase 4: Ship

| # | Task | Done When |
|---|------|-----------|
| 7 | ~~DEPLOY phase (`deploy.py`) + CLI (`__main__.py`)~~ | ✅ Done. Full CLI: `python -m agents.build-agent build spec.json` runs all 7 phases. `--dry-run` validates without touching n8n. `validate` and `scaffold` as standalone commands. Exit code 0/1. On failure, prints workflow ID for debugging. `--resume` deferred to next iteration. |

---

## Open Questions

1. ~~Should we use `requests` library?~~ **Resolved**: No. stdlib `urllib` only — zero dependencies.
2. ~~How should test data be supplied?~~ **Resolved**: Embedded in the spec JSON under `test_cases`. The PM Agent supplies test cases with `input` and `expected` fields. The build agent does not generate test data.
3. ~~What happens on failure mid-build?~~ **Resolved**: Leave the partially-built workflow in n8n for debugging. Report the workflow ID and failed phase. No automatic cleanup.
4. ~~Should the CLI support `--dry-run` mode?~~ **Resolved**: Yes. `--dry-run` validates the spec, resolves credentials, prints planned node layout, and exits without creating anything.

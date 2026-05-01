# n8n Modify Agent Spec

> Changes existing n8n workflows safely: fetch live state, classify the change, diff, snapshot, apply, re-test, re-audit, deploy or roll back. Never starts from a blank workflow — always operates on something live.

| Field | Value |
|-------|-------|
| Status | Draft |
| Last Updated | 2026-05-01 |
| Depends On | n8n instance (read/write API), Build Agent (test_runner + auditor + harden), PM Agent (Phase 2 only — for structural re-plans) |
| Enables | Iterating on deployed workflows without rebuilding from scratch; Zaki-grade "change X to Y" edits with rollback |

---

## Recent Changes

| Date | What changed | Section |
|------|-------------|---------|
| 2026-05-01 | Initial spec | All |
| 2026-05-01 | Credentials guarantee, audit-delta fingerprinting, spec-vs-live authority, `rename_workflow` edit type, snapshot retention default, UI-collision policy | Identity Preservation Rules, Phase 7, Tactical edits, Open Questions |

---

## Where This Differs from the PM Agent and Build Agent

The PM Agent plans from a blank slate. The Build Agent builds from a finished spec. The Modify Agent does neither — it operates on the **delta** between a live workflow and a desired change, and the change is usually small.

That has three consequences worth being explicit about:

**The unit of work is a diff, not a workflow.** SCAFFOLD doesn't apply — the workflow already exists. Wiring is partial — you're touching 1-3 nodes, not all of them. Connections may not change at all. The phase set is borrowed from the Build Agent but biased toward verification-after-the-fact: most of the work is figuring out *what to change* and *whether the change broke anything*, not building structure.

**Identity is load-bearing.** A node's `id` ties it to execution history. A credential reference ties it to the credential store. A webhook path ties it to upstream callers. Replacing a node by deleting + re-creating loses all of that. The Modify Agent's central rule is: **preserve identity unless the change explicitly demands otherwise**. This is the opposite of the Build Agent, which generates fresh IDs for everything.

**Rollback is a first-class phase.** The Build Agent can throw away a half-built workflow if something goes wrong — nothing was live yet. The Modify Agent is editing something users (or other workflows) depend on. Every change starts with a snapshot, every failure path ends with restore-from-snapshot, and DEPLOY isn't done until the post-change smoke test passes against real traffic shape.

**The PM Agent is optional.** For tactical changes — rename a node, change a webhook path, tweak a Set assignment, swap a model — there's no planning to do. The Modify Agent extracts the change deterministically (or with a small LLM call) and applies it. The PM Agent only enters the loop for structural changes (add/remove steps, rewire), where re-decomposition is genuinely needed. This is why Phase 1 of the rollout ships without PM Agent integration at all.

---

## What It Is

The Modify Agent takes a live n8n workflow and a description of a change, and produces a modified version of the same workflow — same `id`, same credentials, same execution history — that has been re-tested and re-audited. If anything goes wrong, it restores the pre-change snapshot.

It is **read/write** on n8n, **read** on the PM Agent's spec store, and **invokes** the Build Agent's test/audit/harden modules as library code (not as a CLI subprocess).

## Inputs

- **Workflow ID** — the live workflow to modify (must exist; agent fails fast if not)
- **Change description** — natural language from the user ("rename the webhook to qualify-leads-v2", "add a Slack notification after the qualification step")
- **n8n API access** — read/write, with API key
- **Original spec** (optional but preferred) — the JSON spec the workflow was built from. If absent, the agent reconstructs a working spec by reading the live workflow JSON. Reconstructed specs are good enough for tactical changes but lossy for structural ones.
- **Existing test cases** — pulled from the original spec's `test_cases` array. If no spec exists, the user must supply at least one happy-path test before the agent will deploy.

## Output

- The same workflow ID, modified in place
- A **change log entry** describing what changed in plain English and JSON-diff form
- Test results (re-ran spec test_cases against the modified workflow)
- Audit results (re-ran security/best-practices/resilience checks)
- A **snapshot ID** pointing to the pre-change workflow JSON (for manual rollback if needed later)
- Modify status table (phase-by-phase progress, mirrors Build Agent's table)

If the change cannot be applied safely, the output is a **rollback notice** — the workflow is restored to its pre-change state, and the report explains why the modify failed.

---

## Process

The phase order is: FETCH → CLASSIFY → PLAN → SNAPSHOT → APPLY → TEST → AUDIT → HARDEN → DEPLOY, with ROLLBACK as a cross-cutting failure path.

### Phase 1: FETCH — Pull Live State

Before classifying anything, pull the current truth.

**Actions:**
1. `GET /api/v1/workflows/{id}` — full workflow JSON (nodes, connections, settings, active state)
2. Load the original spec from disk if available (`specs/<workflow-name>.json` or wherever the PM Agent stored it)
3. `GET /api/v1/executions?workflowId={id}&limit=10` — recent execution history (used to flag "this workflow has been failing recently — modify with extra care")
4. Note the workflow's active state — production workflows get the deactivate-before-modify treatment in Phase 5

**Validation:**
- Workflow exists and is reachable via API
- If active and currently executing (recent execution within last 60s), warn the user and ask whether to wait or proceed

**Why this is its own phase:** The classifier and differ both need the live JSON. Fetching once at the top means we operate on a consistent snapshot — not a moving target if the user is also editing in the n8n UI.

### Phase 2: CLASSIFY — Tactical or Structural

This is the routing decision. It determines whether the PM Agent is invoked.

**Tactical change** — touches existing nodes, doesn't change the graph shape:
- Rename a node
- Change a parameter value (webhook path, cron schedule, Set assignment, IF condition operand, LLM model name, system prompt text)
- Toggle a setting (`continueOnFail`, `retryOnFail`, timeout)
- Update a credential reference (swap one stored credential for another)
- Edit a single expression

**Structural change** — changes the graph shape:
- Add a step (new node + new connections)
- Remove a step (delete node + reconnect neighbors)
- Reorder steps
- Add/remove a gate
- Split or merge branches
- Change a trigger type (webhook → cron, etc.)

**Implementation:** A small LLM call with the change description and a list of the workflow's nodes returns one of `tactical` or `structural` plus a structured edit list (for tactical) or a structural-change description (for structural). The prompt includes few-shot examples of both. If the LLM is uncertain, default to `structural` (safer — triggers more verification).

**Phase 1 of rollout: refuses to handle `structural`** with a clear message ("this change adds/removes nodes — re-run with the PM Agent for now"). Phase 2 of rollout enables the structural path.

### Phase 3a: PLAN (Tactical Path) — Build the Edit List

For tactical changes, produce a deterministic edit list.

```json
{
  "edits": [
    {
      "type": "set_node_parameter",
      "node_id": "step_1",
      "path": "parameters.path",
      "old_value": "qualify-lead",
      "new_value": "qualify-lead-v2"
    },
    {
      "type": "rename_node",
      "node_id": "step_2",
      "old_name": "Build Qualified Response",
      "new_name": "Build Qualified Response (v2)"
    }
  ]
}
```

Edit types (Phase 1):
| Edit type | What it does | Identity rule |
|-----------|-------------|---------------|
| `set_node_parameter` | Sets a value at a JSON path inside a node's parameters | Node ID preserved |
| `rename_node` | Changes node name | ID preserved; connections updated to use new name |
| `set_node_setting` | Toggles `continueOnFail`/`retryOnFail`/etc. | ID preserved |
| `update_credential_ref` | Swaps credential ID (credential must already exist) | Node ID preserved |
| `set_workflow_setting` | Edits workflow-level settings (timeout, error workflow) | Workflow ID preserved |
| `rename_workflow` | Changes the workflow's display name | Workflow ID preserved (callers don't depend on name) |

**Validation before APPLY:**
- Every `node_id` in the edit list exists in the live workflow
- `old_value` matches what's currently in the workflow (catches stale assumptions — if the user is editing in the UI in parallel, we abort)
- Every JSON path is reachable

If any edit fails validation, abort before SNAPSHOT — no harm done.

### Phase 3b: PLAN (Structural Path) — Re-Plan the Slice

**This phase is gated behind Phase 2 of the rollout. Phase 1 stops at CLASSIFY for structural changes.**

For structural changes, the Modify Agent hands the change to the PM Agent:

1. Reconstruct or load the current spec
2. Send to PM Agent: `(current_spec, change_description)` → `updated_spec`
3. PM Agent runs its decompose + adversarial review on the updated spec, treating the existing structure as the starting point
4. Diff the updated spec against the live workflow:
   - Steps in updated spec but not live → **add** (new node IDs, new connections)
   - Steps in live but not in updated spec → **remove** (delete node, reconnect neighbors)
   - Steps in both → **update** (use tactical edit primitives from 3a)
5. Produce an edit list combining tactical edits + structural edits (`add_node`, `remove_node`, `add_connection`, `remove_connection`)

**Identity preservation rule for structural changes:**
- Steps that survive the re-plan keep their original node IDs
- Steps that are added get fresh IDs
- Steps that are removed lose their execution history (unavoidable — the node is gone)
- The matching is done by step `id` field in the spec (not by name or position) — this is why specs use stable step IDs

**When the PM Agent's updated spec diverges too much** (more than 50% of nodes added/removed), the Modify Agent escalates: "this is a rebuild, not a modify — recommend running Build Agent against the new spec and cutting over". It does not silently apply a near-total rewrite.

### Phase 4: SNAPSHOT — Save Rollback Point

Before touching anything live:

1. Save the full workflow JSON from Phase 1 to `build-logs/snapshots/<workflow_id>-<timestamp>.json`
2. Record the snapshot path in the modify status table
3. The snapshot is the rollback target — if anything in APPLY/TEST/AUDIT fails irrecoverably, we PUT this exact JSON back

Snapshots are kept indefinitely (they're small). A periodic cleanup job is out of scope for this spec.

### Phase 5: APPLY — Execute Edits

1. **If the workflow is active**, deactivate it first: `POST /api/v1/workflows/{id}/deactivate`. Record that we deactivated it so DEPLOY knows to re-activate.
2. **GET → modify → PUT pattern** (the n8n API requires the full body for updates):
   - `GET /api/v1/workflows/{id}` to get the current state (already cached from Phase 1, but re-fetch in case the user edited in the UI between FETCH and APPLY — if the JSON has changed since Phase 1's snapshot, abort and ask the user to re-run)
   - Apply each edit from the edit list to an in-memory copy
   - For renames: also walk `connections` and rewrite any references to the old node name
   - For structural removes: rewire — the predecessor of the removed node connects to the successor, in order
   - `PUT /api/v1/workflows/{id}` with the modified JSON

3. Verify the PUT returned 200 and the response reflects the edits

**Apply order matters for structural changes:**
- Add new nodes first (so connections have somewhere to point)
- Add new connections
- Remove old connections
- Remove old nodes
- Apply tactical edits last

This avoids transient invalid states where a connection points to a deleted node.

### Phase 6: TEST — Re-Run Spec Test Cases

The Modify Agent re-uses the Build Agent's `test_runner.py` as a library. It does not re-implement testing.

1. Load test cases from the original spec (or, if reconstructed, the user-supplied happy-path test)
2. For tactical changes, re-run **all** test cases. The change is small but tests are cheap and catch surprise regressions.
3. For structural changes, re-run all test cases plus any new test cases the PM Agent added during re-decomposition

**Pass criteria:**
- All test cases that passed pre-change still pass post-change
- New test cases (added in structural re-plan) pass

**Failure handling:**
- Any test that passed before and fails now → ROLLBACK (the change broke something)
- Any new test that fails → HARDEN may be able to fix it; if HARDEN exhausts iterations, ROLLBACK

### Phase 7: AUDIT — Re-Run Security/Best-Practices/Resilience

Reuse the Build Agent's `auditor.py` as a library. Run all three audits on the modified workflow.

**Difference from Build Agent's AUDIT:** the Modify Agent only flags findings that are **new since the snapshot**. If the original workflow had a pre-existing WARNING (e.g., a default node name that nobody fixed), it's not the modify's job to surface it again. The user is changing a webhook path, not signing up for a full audit.

Implementation: run the audit on both the snapshot and the modified workflow, fingerprint each finding by `(severity, finding_code, node_id)`, and report only the fingerprints present in the modified set but not the snapshot set. Findings with the same code and node ID but different message text are considered the same finding (message text wording is not stable across audits).

This requires the auditor to attach a stable `finding_code` to each finding (e.g. `WEBHOOK_NO_AUTH`, `LLM_NO_GATE`, `EXPRESSION_STRING_CAST_BOOLEAN`). Adding `finding_code` to the auditor's output is a prerequisite for shipping Phase 1 of the Modify Agent.

**New CRITICALs always block.** Even if the user "didn't ask for the audit", we don't deploy a new credential leak.

### Phase 8: HARDEN — Fix New Findings (Loop, Max 3)

Same loop semantics as the Build Agent: fix new CRITICALs and WARNINGs, re-audit, repeat until clean or 3 iterations exhausted. If still not clean, ROLLBACK.

### Phase 9: DEPLOY — Re-Activate and Smoke Test

1. If we deactivated in Phase 5, re-activate: `POST /api/v1/workflows/{id}/activate`
2. Smoke test: send one happy-path test case through the live webhook (`/webhook/{path}`, not `/webhook-test/{path}` — we want to verify production routing works)
3. Verify the smoke test response matches expectation
4. Save the change log entry to `build-logs/changes/<workflow_id>-<timestamp>.json`

If the smoke test fails: ROLLBACK. The TEST phase used `/webhook-test/` and passed; if production routing broke, that's the modify's fault and we're done.

### Cross-Cutting: ROLLBACK

Any phase from APPLY onward can trigger ROLLBACK:

1. `PUT /api/v1/workflows/{id}` with the snapshot JSON (full restore)
2. If we deactivated and the snapshot was active, re-activate
3. Verify the post-rollback workflow matches the snapshot exactly (`GET` and compare)
4. Output a rollback report explaining which phase failed and why
5. Exit non-zero — the user knows the modify did not happen

ROLLBACK never modifies the snapshot file and never deletes it. Manual rollback after the fact is always possible by re-running with the snapshot path.

---

## Identity Preservation Rules

This is the central correctness property of the Modify Agent. Stated as rules:

| Resource | Default behavior | When it changes | Why it matters |
|----------|------------------|-----------------|----------------|
| Workflow `id` | Always preserved | Never (would break callers) | Webhook URLs, executeWorkflow references, execution history all key off this |
| Node `id` | Preserved when the node survives the change | Only when the node is removed | Execution history per-node keys off this |
| Node `name` | Preserved unless edit is `rename_node` | Explicit user request | Used as the connection key in `connections` object — rename rewrites all references |
| Node `position` | Always preserved | Only for newly-added nodes (placed near their predecessor) | UI muscle memory; layout is meaningful to users |
| Credential references | Always preserved | Only via explicit `update_credential_ref` edit | Re-keying credentials breaks the workflow silently |
| Webhook `path` | Preserved unless explicit parameter edit | User explicitly changes it | External systems call this URL — change with care |
| `connections` for unaffected nodes | Always preserved | Only when nodes around them are added/removed/renamed | Preserves the graph shape |
| `settings` (timeout, error workflow, etc.) | Preserved | Only via explicit `set_workflow_setting` edit | Operational behavior |
| Workflow active state | Preserved (deactivate-then-reactivate around APPLY) | Never silently deactivated permanently | Active workflows must stay active across modifies |

**The rule of thumb:** if the user did not explicitly ask for a thing to change, it does not change. The Modify Agent does not "clean up" anything along the way — no renames-for-style, no opportunistic credential consolidation, no node-position rearranging. Drift is the user's job to manage.

### Credentials guarantee

The Modify Agent **never creates credentials**. It only swaps references via the explicit `update_credential_ref` edit (and the credential ID it points to must already exist in n8n's credential store).

This is load-bearing because the Build Agent's HARDEN phase (which the Modify Agent reuses) auto-generates webhook auth tokens when it sees an unauthenticated webhook. Re-running HARDEN naively on a deployed workflow would silently rotate tokens out from under live callers.

The audit-delta logic in Phase 7 already suppresses this — pre-existing findings (e.g. "webhook has no auth") aren't surfaced again, so HARDEN has nothing to fix. But this is an emergent property; the spec calls it out so future audit/HARDEN changes don't break it. If a HARDEN fix would create a credential, the Modify Agent skips it with a CRITICAL log entry: "credential creation refused in modify mode — re-run with `--allow-credential-creation` if you want this behavior".

### Spec vs live authority

When both an original spec and a live workflow exist, they serve different purposes:

- **Live workflow** is authoritative for **structure** — node IDs, connections, parameters, credential references. The user may have edited in the n8n UI; whatever the API returns is the truth.
- **Original spec** is authoritative for **test cases and intent** — what the workflow is supposed to do, which scenarios should pass, what `expected` output shape looks like.

If they disagree on structure (a step in the spec doesn't appear in the live workflow), the Modify Agent trusts the live workflow and re-derives the spec to match. If they disagree on test cases (the spec asserts a response shape the live workflow no longer returns), TEST will fail and ROLLBACK fires — exactly what we want, because that's a real regression the modify could either cause or expose.

If no original spec exists, the Modify Agent reconstructs structural information from the live workflow and **requires the user to supply at least one happy-path test case** before any tactical edit deploys. Structural changes refuse to run without an original spec — the divergence detection in Phase 3b can't work without a baseline.

---

## Tactical vs Structural Classifier

Reference table for the classifier prompt:

| Change request | Class | Edit primitive |
|----------------|-------|----------------|
| "Rename the webhook path to qualify-v2" | Tactical | `set_node_parameter` on `parameters.path` |
| "Change the cron to run hourly instead of daily" | Tactical | `set_node_parameter` on `parameters.rule.interval` |
| "Use Claude Sonnet instead of Haiku" | Tactical | `set_node_parameter` on `parameters.model` |
| "Update the system prompt for the scoring step" | Tactical | `set_node_parameter` on `parameters.systemMessage` |
| "Add a Slack notification after the qualification step" | Structural | `add_node` + `add_connection` × 2 |
| "Remove the email step — we don't need it anymore" | Structural | `remove_node` + connection rewire |
| "The workflow should retry 5 times instead of 3" | Tactical | `set_node_setting` on `retryOnFail`/`maxTries` |
| "Add a gate that rejects items with empty company field" | Structural | `add_node` (IF) + connection split |
| "Change webhook trigger to a daily cron at 9am" | Structural | trigger replacement = remove + add |
| "Fix the typo in the rejection message" | Tactical | `set_node_parameter` on Set assignment value |
| "Reorganize the workflow to score first, then validate" | Structural | reordering = several remove + add operations on connections |

---

## Output Format

### Modify Status Table (Mandatory)

After each phase, the Modify Agent outputs a status table. Same format as the Build Agent — phase, status, notes.

```
## Modify Status: Lead Qualification Webhook (workflow_id: abc123)

| Phase | Status | Notes |
|-------|--------|-------|
| FETCH | done | Live workflow has 3 nodes, last execution 2h ago, currently active |
| CLASSIFY | done | Tactical: 2 edits (rename node, change webhook path) |
| PLAN | done | Edit list validated against live workflow |
| SNAPSHOT | done | Saved to build-logs/snapshots/abc123-20260501-143022.json |
| APPLY | done | Deactivated, applied 2 edits, PUT returned 200 |
| TEST | done | 4/4 spec test cases pass |
| AUDIT | done | 0 new findings vs snapshot |
| HARDEN | skipped | No new findings to fix |
| DEPLOY | done | Re-activated, smoke test passed |

Snapshot: build-logs/snapshots/abc123-20260501-143022.json
Total modify time: 18 seconds
```

### Change Log Entry

Saved to `build-logs/changes/<workflow_id>-<timestamp>.json`:

```json
{
  "modify_id": "uuid",
  "workflow_id": "abc123",
  "workflow_name": "Lead Qualification Webhook",
  "started_at": "2026-05-01T14:30:22Z",
  "completed_at": "2026-05-01T14:30:40Z",
  "user_request": "Rename the webhook to qualify-v2 and update the node name to match",
  "classification": "tactical",
  "edits_applied": [
    {
      "type": "set_node_parameter",
      "node_id": "trigger-1",
      "path": "parameters.path",
      "old_value": "qualify-lead",
      "new_value": "qualify-v2"
    },
    {
      "type": "rename_node",
      "node_id": "trigger-1",
      "old_name": "Lead Webhook",
      "new_name": "Lead Webhook v2"
    }
  ],
  "snapshot_path": "build-logs/snapshots/abc123-20260501-143022.json",
  "test_results": { "passed": 4, "failed": 0 },
  "audit_results": { "new_critical": 0, "new_warning": 0, "new_info": 0 },
  "deploy_outcome": "active, smoke test passed",
  "human_summary": "Renamed webhook from qualify-lead to qualify-v2. External callers must update their URL."
}
```

The `human_summary` is generated by an LLM call on the edit list — Zaki reads this, not the JSON.

---

## Implementation Notes

### File Layout

```
agents/modify_agent/
  __init__.py
  __main__.py        # CLI entry point
  fetcher.py         # Phase 1: pull live workflow + spec + executions
  classifier.py      # Phase 2: tactical vs structural (LLM call)
  planner.py         # Phase 3: build edit list (tactical) or call PM Agent (structural)
  snapshot.py        # Phase 4: save rollback point + retention cleanup (last 30 / 90 days)
  applier.py         # Phase 5: execute edits via PUT, with identity preservation
  audit_diff.py      # Phase 7: fingerprint findings, return delta vs snapshot
  rollback.py        # Cross-cutting: restore from snapshot
  change_log.py      # Phase 9: write change log entry, generate human summary
  prompts/
    classify.md           # tactical-vs-structural classifier prompt
    extract_edits.md      # natural language → edit list (output schema must match Edit type table)
    summarize_change.md   # edit list → human-readable summary
  tests/
```

### Build Agent prerequisites

The Modify Agent depends on these changes to the Build Agent before Phase 1 can ship:

- **Audit-delta fingerprinting uses the auditor's existing `check` field.** Today's auditor already attaches a stable code per finding (e.g. `'hardcoded_credentials'`, `'webhook_no_auth'`). The Modify Agent's `audit_diff.py` fingerprints by `(severity, check, node_name)` — no auditor change required. Audit `Finding`s that don't carry `node_name` (workflow-level findings) fingerprint by `(severity, check)` only. If new audit checks are added, they must continue setting `check` for the delta logic to work.
- **`harden.py` must accept a `disable_credential_creation: bool` flag** (or check `os.environ.get('MODIFY_MODE')`). When set, harden's webhook-auth-creation path is skipped and the finding is left in place rather than fixed. Modify Agent passes `disable_credential_creation=True` always.

### Reuse, Not Reimplementation

The Modify Agent imports from the Build Agent rather than duplicating:
- `agents.build_agent.client` — n8n REST client (GET/PUT/activate/deactivate)
- `agents.build_agent.test_runner` — re-running spec test cases
- `agents.build_agent.auditor` — running the three audit passes
- `agents.build_agent.harden` — fixing audit findings
- `agents.build_agent.wire` — translation functions for Set/IF parameters (when an edit touches one)
- `agents.build_agent.status` — status table formatting

The Modify Agent imports from the PM Agent only for Phase 2 of the rollout:
- `agents.pm_agent.decomposer` — re-decompose for structural changes
- `agents.pm_agent.reviewer` — adversarial review on the updated spec

### Dependencies

- Same as Build Agent (Python stdlib only) for tactical path
- Same as PM Agent (`anthropic` SDK) for classifier and structural path
- Phase 1 rollout can ship without `anthropic` if classifier defaults to "tactical only, regex-extract from request"

### CLI Usage

```bash
# Tactical change (Phase 1)
python -m agents.modify_agent change <workflow_id> "rename the webhook to qualify-v2"

# Tactical change with explicit edit list (skip classifier — for scripts)
python -m agents.modify_agent change <workflow_id> --edits edits.json

# Structural change (Phase 2)
python -m agents.modify_agent change <workflow_id> "add a Slack notification after qualification"

# Dry run — show plan, snapshot, and predicted PUT body but don't apply
python -m agents.modify_agent change <workflow_id> "..." --dry-run

# Manual rollback to a known snapshot
python -m agents.modify_agent rollback <workflow_id> --snapshot build-logs/snapshots/abc123-20260501-143022.json

# List recent changes for a workflow
python -m agents.modify_agent history <workflow_id>
```

### What It Needs Access To

- n8n API (read/write)
- The original spec file (best effort — falls back to live JSON reconstruction)
- LLM API (for classifier, edit extraction, change summary)
- `build-logs/snapshots/` and `build-logs/changes/` directories (created on first run)

### What It Does NOT Do

- Build new workflows (that's the Build Agent)
- Plan from scratch (that's the PM Agent)
- Create credentials (those exist; Modify Agent only swaps references — see Credentials guarantee)
- Modify workflows it didn't get a snapshot of (no "blind" edits)
- Apply structural changes in Phase 1 (escalates to PM Agent)
- Rename workflows silently (workflow rename is a tactical edit, but it must be explicitly requested — no opportunistic naming cleanup)
- Touch other workflows (one workflow per modify run — no implicit cross-workflow rewrites)
- Merge concurrent UI edits (abort-and-retry, never silent overwrite)

---

## Rollout Phases

### Phase 1 — Tactical Only (Ship First)

**In scope:**
- FETCH, CLASSIFY (with `structural` → escalation message), PLAN (tactical), SNAPSHOT, APPLY, TEST, AUDIT, HARDEN, DEPLOY, ROLLBACK
- All five tactical edit types
- CLI: `change` (tactical only), `rollback`, `history`
- Reuse Build Agent test_runner / auditor / harden as libraries
- Snapshot + rollback fully working

**Out of scope:**
- Any change that adds, removes, or rewires nodes
- PM Agent integration

**Done when:**
- Can rename a node, change a webhook path, swap a model, edit a Set assignment, toggle retry — all on a live deployed workflow — with snapshot+rollback working in failure tests
- Test suite covers each edit type, plus a rollback-on-test-failure test, plus a rollback-on-audit-failure test
- Modify status table renders correctly for all paths

This phase is the dividing line. Most real change requests are tactical — ship this first and use it before building Phase 2.

### Phase 2 — Structural

**In scope:**
- PLAN (structural) — invokes PM Agent decomposer + reviewer
- All five structural edit types (`add_node`, `remove_node`, `add_connection`, `remove_connection`, plus the trigger-replacement case)
- Edit list combiner (tactical + structural in the same change)
- Updated identity preservation rules for structural changes (step ID matching across re-plans)
- Escalation path when the PM Agent's updated spec is too divergent (>50% node churn)

**Done when:**
- Can add a Slack notification step after the qualification step on a deployed workflow with all original tests still passing and one new test for the Slack step
- Can remove an unused step with all remaining tests still passing
- Divergence escalation triggers correctly (verified with a test case that flips a workflow's purpose)

---

## Modify Quality Gate

A modify is NOT done until all of these are true:

```
□ Snapshot saved and verified readable
□ All edits validated against live workflow before APPLY
□ APPLY returned 200 and PUT body reflected in subsequent GET
□ All pre-existing test cases pass
□ No new CRITICAL audit findings
□ No new WARNING audit findings (or HARDEN resolved them)
□ Workflow active state matches pre-change state (deactivated workflows stay deactivated; active workflows are re-activated)
□ Smoke test passed against /webhook/ (for active workflows with webhook triggers)
□ Change log entry written
□ Human summary generated and reviewed (in interactive mode) or saved (in scripted mode)
```

If any box fails, ROLLBACK fires and the modify exits non-zero.

---

## Decisions and Open Questions

### Decided (defaults shipped with Phase 1)

1. **Snapshot retention.** Keep the last 30 snapshots per workflow, age out anything older than 90 days. Cleanup runs at the start of every modify (cheap; just `os.stat` + delete). Disk cost: ~30 snapshots × ~50 KB = 1.5 MB per workflow, capped.
2. **Concurrent edits in the n8n UI.** Abort-and-retry only — no merge attempt. If the live workflow JSON differs from the Phase 1 snapshot when APPLY tries to read it again, the modify exits with "workflow was edited externally; re-run". This is conservative but it's the only option that's actually safe (the alternative is silently overwriting UI changes).
3. **Reconstructed specs.** Tactical changes work on reconstructed specs (per the Spec vs Live Authority rules above). Structural changes refuse to run without an original spec.
4. **Multi-workflow changes.** Out of scope. The Modify Agent operates on one workflow at a time. If a change implies cross-workflow updates, the user runs Modify multiple times.

### Still open

1. **Test coverage for changes the spec doesn't cover.** If the user asks "rename node X" but no test exercises node X, the TEST phase may pass trivially. Tracking which nodes each test case actually executes (via execution logs after a test run) and warning when a modify touches an "untested" node would help — but adds significant complexity. Defer to Phase 2.
2. **Structural divergence threshold.** The 50% node-churn cutoff for "this is a rebuild" is a guess. Tune once we have real data.
3. **`--allow-credential-creation` opt-in.** The credentials guarantee says HARDEN-time credential creation is refused by default. Should the opt-in flag exist at all, or should credential creation only ever happen in Build Agent? Ship without the flag for Phase 1; revisit if users actually hit cases where they want it.

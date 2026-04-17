# n8n Build Agent Spec

> Builds n8n workflows from specs via the REST API: scaffold, wire, test, audit (loop until clean), harden, codify, deploy. Never plans — only builds.

| Field | Value |
|-------|-------|
| Status | Active |
| Last Updated | 2026-04-13 |
| Depends On | PM Agent spec (JSON), n8n instance (read/write API access) |
| Enables | Live, tested, hardened n8n workflows |

---

## Recent Changes

| Date | What changed | Section |
|------|-------------|---------|
| 2026-04-13 | Renamed TDD Agent → Build Agent; added "Where This Differs from a Software TDD Agent" — no real TDD loop, audits are JSON analysis not code review, codify means sub-workflow extraction | Differences |
| 2026-04-13 | Added audit loop, Litmus Test, workflow status table, codification phase, "Done When" verification | Process, all phases |
| 2026-04-13 | Initial spec | All |

---

## Where This Differs from a Software TDD Agent

This agent borrows its phase discipline from N2O's software tdd-agent, but the mechanics are fundamentally different. Being explicit about this prevents the N2O framework from being misread as a 1:1 template.

**No RED/GREEN/REFACTOR loop.** In software TDD, you write a failing test first, then make it pass. Here there is no test harness — the TEST phase runs sample data through the workflow and inspects results. That's acceptance testing, not TDD. The phased discipline (scaffold before wiring, test before deploying) is still valuable — hence the rename from "TDD Agent" to "Build Agent."

**The Litmus Test applies differently.** In software, "if I break the function, will this test fail?" is checkable by actually breaking the function and re-running the suite. In n8n, you'd have to manually misconfigure a node and re-run test data. There's no automated regression. The Litmus Test is still a useful thought exercise for designing test cases, but it can't be enforced the way it is in code.

**Audits are mostly static JSON analysis, not LLM code review.** The software agent's 3-subagent audit reads source code and reasons about patterns. Most of the n8n audit checks (hardcoded credentials, missing webhook auth, missing retry config) are deterministic — parse the workflow JSON and look for known fields. The spec should distinguish programmatic checks from LLM-driven review and default to programmatic where possible.

**CODIFY means extracting sub-workflows, not documenting patterns.** In the software agent, codification writes skill files that get loaded into future conversations. Here, it means exporting reusable workflow JSON into a component library. The operation is closer to publishing a package than writing documentation.

**The spec handoff is leakier.** The software PM agent gives tasks with a `done_when` field and lets the build agent decide implementation. This PM agent tries to specify node types, parameters, and data shapes — but uses pseudocode expressions (e.g., `{{config.naics_codes}}`) that aren't valid n8n syntax. The build agent must translate, which means it's making design decisions the spec says it shouldn't. Accept this: the build agent will need to interpret the spec, not just execute it.

---

## What It Is

The Build Agent takes a workflow spec (produced by the PM Agent) and builds a working, tested, hardened n8n workflow via the n8n REST API. It follows a strict phase order: SCAFFOLD → WIRE → TEST → AUDIT → HARDEN → CODIFY → DEPLOY.

## Inputs

- **Workflow spec** — the JSON spec from the PM Agent (see pm-agent-spec.md)
- **n8n API access** — read/write, with API key
- **Credential store** — credential IDs for any services the workflow needs (never creates credentials, only references existing ones)
- **Component library** — reusable sub-workflows or node configurations

## Output

- A live, activated n8n workflow
- Test results (pass/fail for each test case)
- Audit report (security, best practices, resilience findings)
- Workflow documentation (auto-generated from the spec + build)
- Build status table (phase-by-phase progress)
- Codified patterns (if any reusable patterns were extracted)

---

## Process

### Phase 1: SCAFFOLD — Define the Structure

Create the workflow skeleton before configuring anything.

**Actions:**
1. `POST /api/v1/workflows` with:
   - Workflow name and description from spec
   - Empty/placeholder nodes for each step in the spec
   - Trigger node matching spec trigger type
   - Error handler nodes where spec defines error paths
   - Gate nodes (IF/Switch) where spec defines gates

2. Position nodes on the canvas in a readable layout:
   - Left to right flow, main path horizontal
   - Error branches below the main path
   - Gates visually distinct (branching point)
   - ~250px horizontal spacing, ~150px vertical for branches

3. Name every node descriptively from the spec (never "HTTP Request 1")

**Validation:**
- Workflow exists in n8n with correct number of nodes
- Every node from the spec has a corresponding node in the scaffold
- No nodes are connected yet (that's the next phase)

```
API call: POST /api/v1/workflows
Body: {
  "name": spec.workflow_name,
  "nodes": [
    // trigger node
    {
      "id": uuid(),
      "name": spec.trigger.description,
      "type": trigger_type_map[spec.trigger.type],
      "typeVersion": latest,
      "position": [250, 300],
      "parameters": {}  // empty — configured in WIRE phase
    },
    // one placeholder per step
    ...spec.steps.map((step, i) => ({
      "id": uuid(),
      "name": step.name,
      "type": step.node_type,
      "typeVersion": latest,
      "position": [250 + (i+1) * 250, 300],
      "parameters": {}  // empty — configured in WIRE phase
    })),
    // gate nodes
    // error handler nodes
  ],
  "connections": {},  // empty — wired in WIRE phase
  "settings": {
    "executionTimeout": spec.error_handling.global_timeout_seconds,
    "saveExecutionProgress": true,
    "saveDataErrorExecution": "all",
    "saveDataSuccessExecution": "all"
  }
}
```

### Phase 2: WIRE — Connect and Configure One Node at a Time

For each node in order, from trigger to output:

1. **Configure the node** — set parameters from the spec
2. **Set credentials** — reference credential IDs from the credential store
3. **Set input mapping** — expressions that pull data from the previous node's output
4. **Connect to previous node** — add the connection in the connections object
5. **Test this node in isolation** — if possible, execute with sample data
6. **Verify output** — does the output match the expected output_shape from the spec?

**Key rule:** Wire one node at a time. Don't move to node N+1 until node N is configured, connected, and verified.

```
For each step in spec.steps:
  
  1. Configure:
     PUT /api/v1/workflows/{id}
     Update node parameters from step.parameters
  
  2. Connect:
     PUT /api/v1/workflows/{id}
     Add connection from previous node to this node in connections object
  
  3. Verify:
     Check node config matches spec
     If testable in isolation, run and check output shape
```

**Connection format:**
```json
"connections": {
  "Previous Node Name": {
    "main": [
      [
        { "node": "Current Node Name", "type": "main", "index": 0 }
      ]
    ]
  }
}
```

**For gate nodes (IF/Switch):**
```json
"connections": {
  "Gate Node Name": {
    "main": [
      // output 0: condition true (pass)
      [{ "node": "Next Step", "type": "main", "index": 0 }],
      // output 1: condition false (fail)
      [{ "node": "Error Handler", "type": "main", "index": 0 }]
    ]
  }
}
```

### Phase 3: TEST — Run End-to-End With Test Data

Create test cases from the spec and run the full workflow.

**Test case categories:**

| Category | What to test | Example |
|---|---|---|
| Happy path | Normal, expected input | Valid API response with 5 results |
| Empty input | No data to process | API returns 0 results |
| Large input | Volume beyond normal | API returns 500 results |
| Malformed input | Unexpected data shapes | Missing fields, wrong types, null values |
| API failure | External service down | 500 error, timeout, rate limited |
| LLM failure | LLM returns bad output | Missing fields, out-of-range values, refusal |
| Gate rejection | Validation catches bad data | Score outside 0-100, missing required field |

**Execution method:**
- Workflow must have a webhook trigger for testing (or use manual trigger)
- Send test data via `POST /webhook-test/{path}`
- Check execution result via `GET /api/v1/executions/{id}`

**For each test case, verify:**
- [ ] Workflow completed (didn't hang or crash)
- [ ] Nodes executed in expected order
- [ ] Gates passed/failed as expected
- [ ] Output matches expected shape and content
- [ ] Error paths triggered when they should
- [ ] No infinite loops
- [ ] Execution time within timeout

### The Litmus Test

For every test case, apply this check:

> **"If I break the node's actual functionality, will this test case catch it?"**

If the answer is "no", the test is **fake** and must be rewritten.

**Common fake workflow tests to AVOID:**

| Fake Test | Why It's Fake | Real Test |
|-----------|--------------|-----------|
| "Workflow executed successfully" | Passes even if output is garbage | Check output matches expected shape AND content |
| "Node produced output" | Passes even if output is wrong | Check specific field values against expected |
| "No errors thrown" | Passes even if workflow silently skipped all processing | Check that expected nodes ran in expected order |
| "Email was sent" | Passes even if email body is empty/wrong | Check email contains expected content, recipients, subject |
| "LLM returned response" | Passes even if LLM hallucinated | Check response against gate criteria (schema + value ranges) |

**Why this matters:** A test suite full of fake tests gives false confidence. You think the workflow works, deploy it, and it fails in production with data you never actually validated against.

### Phase 4: AUDIT — Three Automated Reviews

Run all three audits on the completed workflow. Each audit produces findings categorized as CRITICAL (blocks deploy), WARNING (flagged for review), or INFO (suggestion).

**Audit 1: Security**

Check the workflow JSON (`GET /api/v1/workflows/{id}`) for:

| Check | How | Severity |
|---|---|---|
| Hardcoded credentials | Scan node parameters for API keys, tokens, passwords | CRITICAL |
| Missing webhook auth | Check webhook nodes for `authentication` parameter | CRITICAL |
| Overly broad permissions | Review what data/systems each node accesses | WARNING |
| PII in LLM calls | Check if PII flows to external LLM APIs | WARNING |
| Data sent to unexpected endpoints | Verify all URLs match expected services | CRITICAL |
| Credentials in expressions | Scan expression fields for credential-like strings | CRITICAL |

**Audit 2: Best Practices**

| Check | How | Severity |
|---|---|---|
| Descriptive node names | No default names like "HTTP Request", "IF", "Code" | WARNING |
| Error handling on API calls | Every HTTP/API node has `continueOnFail` or error branch | WARNING |
| Retry on external calls | API nodes have retry configured | WARNING |
| Rate limiting | Workflows with external triggers have throttling | WARNING |
| LLM output validation | Every LLM node is followed by a gate | WARNING |
| Workflow timeout set | `settings.executionTimeout` is not 0/unlimited | INFO |
| Node count reasonable | Workflow doesn't exceed ~30 nodes (split if so) | INFO |

**Audit 3: Resilience**

| Check | How | Severity |
|---|---|---|
| Idempotency | Can this workflow run twice on the same trigger safely? | WARNING |
| Downstream failure handling | What if the output destination is down? | WARNING |
| Input validation | Is webhook/trigger input validated before processing? | WARNING |
| Alerting on failure | Is there a notification when the workflow fails? | WARNING |
| Dead letter handling | Are failed items captured for retry? | INFO |

### Phase 5: HARDEN — Fix Audit Findings (Loop Until Clean)

**This is a loop, not a single pass.** Fix findings, re-audit, repeat until no CRITICALs and no WARNINGs remain. Max 3 iterations — if still not clean after 3, escalate to PM Agent for scope review.

```
AUDIT → findings → HARDEN → re-AUDIT → clean? → yes → continue
                                          ↓ no
                                    HARDEN → re-AUDIT (max 3 loops)
                                          ↓ still not clean
                                    Escalate to PM Agent
```

For each CRITICAL and WARNING finding:

1. Fix the issue by updating the workflow via `PUT /api/v1/workflows/{id}`
2. Re-run the affected test cases
3. Verify the fix didn't break other paths
4. Re-run the specific audit check to confirm resolution

**Common hardening actions:**

| Finding | Fix |
|---|---|
| Missing error handling | Add error branch with alerter component |
| Missing retry | Set `retryOnFail: true`, `maxTries: 3`, `waitBetweenTries: 1000` on node |
| Missing webhook auth | Add `authentication` parameter with header auth or basic auth |
| Missing LLM output validation | Add IF node after LLM node checking required fields |
| No alerting | Add Slack/email node on error paths |
| No rate limiting | Add wait/throttle node at workflow entry |
| Hardcoded credential | Move to n8n credential store, reference by ID |

### Phase 6: CODIFY — Extract Patterns

After hardening, before deploying, check if this build produced anything reusable.

**Ask three questions:**
1. Did we build a node configuration that would be useful in 3+ future workflows?
2. Did we discover a failure mode or edge case that others should know about?
3. Did we create a gate/validation pattern that should be standardized?

**If yes to any:**
- Extract the reusable piece into a component (sub-workflow JSON or code snippet)
- Add it to the component library manifest with: description, when to use, configuration options
- Add test data and expected outputs for the component
- Update the patterns/anti-patterns list if a new do/don't emerged

**If no:** Skip this phase. Don't force codification — only extract genuine patterns.

**This is the compounding mechanism.** Every workflow you build makes the next one faster because the PM Agent can reference more components and the Build Agent can reuse more patterns.

### Phase 7: DEPLOY — Activate and Monitor

1. **Activate:** `POST /api/v1/workflows/{id}/activate`
2. **Smoke test:** Send one real request through the webhook and verify end-to-end
3. **Monitor first runs:** Check `GET /api/v1/executions?workflowId={id}` for the first 5 executions
4. **Verify alerting:** Trigger a deliberate failure and confirm the alert fires
5. **Generate documentation:** Auto-generate from spec + workflow JSON

---

## Trigger Type Mapping

| Spec trigger type | n8n node type | Notes |
|---|---|---|
| `manual` | `n8n-nodes-base.manualTrigger` | For testing; replace with webhook for production |
| `cron` | `n8n-nodes-base.scheduleTrigger` | Uses cron expression from spec |
| `webhook` | `n8n-nodes-base.webhook` | Needs path, method, auth config |
| `polling` | `n8n-nodes-base.scheduleTrigger` + HTTP | Cron that hits an API to check for changes |
| `event` | `n8n-nodes-base.webhook` | External system pushes to our webhook |
| `chained` | `n8n-nodes-base.executeWorkflowTrigger` | Triggered by another workflow |

## Error Handling Mapping

| Spec error action | n8n implementation |
|---|---|
| `retry` | Node-level: `retryOnFail: true` with `maxTries` and `waitBetweenTries` |
| `skip_item_and_continue` | `continueOnFail: true` + downstream filter for failed items |
| `alert_and_stop` | Error branch → alerter component → Stop node |
| `fallback` | Error branch → alternative node (e.g., different API, cached data) |
| `human_review` | Error branch → Slack/email with approval link → Wait node |

---

## Implementation Notes

### How It Runs

The Build Agent is a programmatic agent that:
- Reads the PM Agent's spec file
- Makes API calls to the n8n REST API
- Runs test data through webhooks
- Checks execution results via the API
- Produces a report

It can be implemented as:
- A Claude Code agent/skill that calls the n8n API via HTTP
- An n8n workflow that builds other workflows (meta-workflow)
- A standalone script (Python/Node) that orchestrates the build

### What It Needs Access To

- n8n API (read/write) — full workflow CRUD, execution monitoring
- Credential store (read-only) — to reference existing credential IDs
- Component library — to import reusable sub-workflows
- LLM API — for audit checks that require reasoning (security review, best practices)
- Test data — embedded in the spec JSON under `test_cases` (supplied by PM Agent)

### What It Does NOT Do

- Interview users (that's the PM Agent)
- Create credentials (those must exist before the Build Agent runs)
- Make scope decisions (those are resolved in the spec)
- Modify other workflows (only touches the workflow it's building)

---

## Workflow Status Table (Mandatory)

After each phase, the Build Agent outputs a status table. This is not optional — it's how the user tracks progress and how we ensure no phase is silently skipped.

```
## Build Status: [Workflow Name]

| Phase | Status | Notes |
|-------|--------|-------|
| SCAFFOLD | done | 8 nodes created, positioned left-to-right |
| WIRE | done | 8/8 nodes configured and connected |
| TEST | done | 7/7 test cases pass (2 happy, 2 edge, 3 error) |
| AUDIT: Security | done | 0 critical, 1 warning (missing webhook auth) |
| AUDIT: Best Practices | done | 0 critical, 1 warning (default node name) |
| AUDIT: Resilience | done | 0 critical, 0 warning |
| HARDEN | done | 2 warnings fixed, re-audit clean |
| CODIFY | done | Extracted `sam_gov_paginator` component |
| DEPLOY | done | Workflow abc123 active, smoke test passed |

Audit loop iterations: 2 (clean on second pass)
Total build time: 12 minutes
```

**CRITICAL: Do NOT truncate this workflow.** Every phase must appear in the status table. Stopping at TEST and skipping AUDIT/HARDEN/CODIFY/DEPLOY is incomplete.

---

## "Done When" Verification

The Build Agent verifies each step's "Done When" criteria from the PM Agent spec. A step is not complete until its "Done When" is met, not just until the node exists and runs.

| Step Status | Meaning |
|-------------|---------|
| Scaffolded | Node exists with correct type, no configuration |
| Wired | Node configured, connected, credentials set |
| Tested | "Done When" criteria verified with test data |
| Audited | Security + best practices + resilience checks pass |
| Hardened | All CRITICAL/WARNING findings resolved |
| Deployed | Running in production, smoke test passed |

---

## Open Questions

1. ~~Should the Build Agent store test data alongside workflow JSON for regression testing on updates?~~ **Resolved**: Test cases are embedded in the PM Agent's spec JSON under `test_cases`. The spec file IS the test data store — re-running the build agent against the same spec re-runs the same tests.
2. ~~How to handle the audit loop if a fix introduces new findings?~~ **Resolved**: Cap at 3 iterations. If still not clean, the spec has a design issue — escalate to PM Agent.
3. How should the CODIFY phase integrate with the component library? Direct file write, or PR-style review?

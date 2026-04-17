# n8n PM Agent Spec

> Plans n8n workflows from natural language: interview, audit, decompose, verify, hand off. Never builds — only plans.

| Field | Value |
|-------|-------|
| Status | Active |
| Last Updated | 2026-04-13 |
| Depends On | n8n instance (read-only API access), LLM API |
| Enables | Build Agent (consumes the spec this agent produces) |

---

## Recent Changes

| Date | What changed | Section |
|------|-------------|---------|
| 2026-04-13 | Synced with Build Agent implementation: replaced SAM.gov example with Lead Qualification spec that uses real n8n syntax, added format rules, expanded interview to Q5-Q8, standardized gate format | Interview, Output Format, Differences |
| 2026-04-13 | Added "Where This Differs from Software PM Agent" — spec is design intent not executable contract, missing n8n-specific interview questions | Differences |
| 2026-04-13 | Added Pre-Task Checklist (Phase 2.5), structured Adversarial Review (Phase 2.75), MECE verification, "Done When" quality criteria, spec template | Process, Output Format |
| 2026-04-13 | Initial spec | All |

---

## Where This Differs from the Software PM Agent

The software PM agent scopes features and produces tasks with `done_when` criteria — the Build Agent decides how to implement. This PM agent tries to go further: specifying node types, parameters, connection shapes, and data flow. That creates a problem.

**The spec is too detailed to be pure planning, too vague to be executable.** The Build Agent can only translate a few node types mechanically (Set, IF). For everything else, spec parameters are *design intent* that the Build Agent passes through to n8n as-is. This means parameters must use real n8n field names and expression syntax (`={{ $json.field }}`), not pseudocode. The PM Agent doesn't need to know every n8n quirk, but it does need to produce JSON that's close enough for the Build Agent to wire without guessing.

**Recommendation:** Keep specs grounded in n8n reality. Use real n8n expression syntax in parameters. Use the gate format the Build Agent understands (see Output Format). When unsure about exact n8n parameter names, leave `parameters` minimal and add a `description` field — the Build Agent can interpret descriptions for simple cases, and a human can fill in exact parameters for complex ones.

**The interview is missing n8n-specific questions.** The 4-question framework (outcome, trigger, stakes, success criteria) transfers well from software planning, but n8n workflows need additional questions. See Q5-Q8 below.

---

## What It Is

The PM Agent takes a natural language description of what someone wants automated and produces a structured workflow spec that the Build Agent can build. It does not build anything. It plans.

## Inputs

- **User description** — natural language, could be one sentence ("notify me when a new SAM.gov contract matches our NAICS codes") or a paragraph
- **Existing workflow inventory** — list of active workflows from `GET /api/v1/workflows`
- **Component library manifest** — list of available reusable components with descriptions

## Output

A **workflow spec** in a structured format the Build Agent consumes directly. See "Output Format" below.

---

## Process

### Phase 1: Interview

The PM Agent asks the user up to eight questions. It can infer answers from the initial description and only ask what's missing. Q1-Q4 scope the workflow. Q5-Q8 collect n8n-specific details the Build Agent needs.

**Q1: What are you trying to accomplish?**
- Outcome, not mechanism
- "I want leads that go cold for 5 days to get a follow-up email" not "I want a cron job that queries the CRM"
- If the user describes mechanism, the PM Agent extracts the outcome and confirms

**Q2: How should this be triggered?**
- Manual (user clicks / sends a message)
- Scheduled (cron — every N minutes/hours/days, specific time)
- Event-driven (webhook — external system pushes, or polling — we check periodically)
- Chained (output of another workflow)
- If the user doesn't know, the PM Agent recommends based on the use case

**Q3: What happens if it gets something wrong?**
- Low stakes → lighter validation, log and move on
- Medium stakes → automated validation + spot checks
- High stakes → human approval gate before output
- This determines the number and placement of gates in the workflow

**Q4: What does "right" look like?**
- Expected output format and content
- How to detect wrong output (negative examples)
- Whether correctness can be checked automatically (schema, rules, LLM review)
- This determines what validation nodes to include

**Q5: What systems and APIs are involved?**
- Each external system needs credentials in n8n's credential store
- Ask for credential names as they appear in n8n (or close enough to resolve)
- This also surfaces rate limits, auth mechanisms, and data sensitivity

**Q6: What data volume per run?**
- 1 item vs 10,000 changes the architecture completely
- LLM-per-item patterns are expensive at volume — budget matters here
- Loops and pagination become necessary above ~100 items

**Q7: What's the budget per run?**
- Don't wait for adversarial review to discover a $500/day workflow
- Estimate early: (API calls × cost) + (LLM tokens × price) × expected volume
- If the estimate surprises the user, redesign before decomposing

**Q8: Who needs to edit or monitor this workflow?**
- Technical user → lighter version control, more flexibility
- Non-technical user (Zaki) → plain-English change descriptions, simplified rollback, PM Agent interview on edits

### Phase 2: Audit Existing Workflows

Before designing anything new, the PM Agent checks what already exists.

```
Query: GET /api/v1/workflows
For each existing workflow:
  - Does it have the same or similar trigger?
  - Does it produce the same or similar output?
  - Does it touch the same systems/APIs?
  - Could this new workflow be an extension of an existing one?

Check component library:
  - Which existing components apply to this workflow?
  - What new components will likely be needed?
```

**Decisions after audit:**
- Build new workflow vs. extend existing one
- Which components to reuse vs. build fresh
- Potential conflicts with existing workflows (same trigger, same data, race conditions)

### Phase 3: Decompose Into Workflow Spec

The PM Agent produces the structured spec (see Output Format).

Key decisions at this stage:
- **Node type selection** — which n8n node types to use for each step
- **Determinism level per step** — is this a 1.0 deterministic operation (API call, data transform, schema check) or a 3.0 LLM operation (analysis, summarization, classification)?
- **Gate placement** — where to validate between steps. Gates must use `conditional_branch` type with `pass_to`/`fail_to` step IDs
- **Error paths** — what happens when each step fails
- **Data shape** — what fields flow between nodes
- **Parameters** — use real n8n expression syntax (`={{ $json.field }}`). For Set and IF nodes, follow the format in the example spec. For other node types, use actual n8n parameter names

**Node types the Build Agent translates automatically:**
| Node type | What the Build Agent does |
|-----------|--------------------------|
| `n8n-nodes-base.set` | Translates `assignments` array to n8n v3.4 nested format |
| `n8n-nodes-base.if` | Translates `conditions.and` array to n8n v2 format with `combinator` |
| All others | Passes `parameters` through to n8n as-is — must use exact n8n field names |

### Phase 3.5: Pre-Task Checklist (Verify Before Handing Off)

Before translating the spec into something the Build Agent can build, run this checklist. It catches problems before they become expensive.

```
□ 1. AUDIT EXISTING WORKFLOWS (Phase 2 output)
    - Confirmed no duplicates or conflicts
    - Identified reusable components
    - Noted any workflows this one should chain with

□ 2. MECE CHECK (for multi-workflow systems)
    - No overlaps between this workflow and others
    - Cross-refs for out-of-scope items ("scoring handled by X workflow")
    - Together with existing workflows, the full automation need is covered

□ 3. STEP QUALITY CHECK
    - Every step has a clear "Done When" (not "works" or "done")
    - Every step specifies determinism level (1.0 deterministic or 3.0 LLM)
    - Every step has error handling defined
    - Every LLM step has a gate after it

□ 4. SCOPE CHECK WITH USER
    - Step count reasonable? (3-10 nodes typical, 30 max)
    - Cost estimate acceptable?
    - Any obvious gaps before adversarial review?
    - Proceed to Phase 4 (Adversarial Review)
```

### "Done When" Quality for Steps

Each step in the spec needs a clear, testable "Done When" — the Build Agent uses these to verify each step works correctly.

| Step Type | Bad "Done When" | Good "Done When" |
|-----------|-----------------|-------------------|
| API call | "Gets data" | "Returns JSON array of items, each with `id` and `title`; retries 3x on 5xx; returns empty array on 404" |
| IF gate | "Checks input" | "Routes to success branch when `email` and `company` are non-empty; routes to error branch otherwise" |
| Set node | "Formats output" | "Returns `{status: 'qualified', email, company, qualified_at}` where `qualified_at` is ISO timestamp" |
| LLM scoring | "Scores items" | "Returns `fit_score` (0-100), `rationale` (non-empty string), `recommendation` (pursue/watch/skip); gate rejects if fields missing or score out of range" |

### Phase 4: Adversarial Review

Before handing off to Build Agent, the PM Agent stress-tests its own spec. This is structured, not freeform — work through each category systematically.

**Category 1: Missing Steps**
- Is there an implicit step between A and B? (data transformation, auth, rate limiting, pagination)
- Does step N need the output of a step that doesn't exist yet?

**Category 2: Failure Modes** (for EACH node)
- What if the API is down? (timeout, 5xx)
- What if data is malformed? (missing fields, wrong types, null values)
- What if the LLM hallucinates? (out-of-range values, refusal, missing fields)
- What if rate limited? (429, retry-after header)

**Category 3: Scope**
- Over-engineering — could fewer nodes do the same thing?
- Under-engineering — are we skipping validation we'll regret?
- Scope creep — is this one workflow trying to do two jobs? Split it.

**Category 4: Security**
- What credentials are needed? Do they exist in the credential store?
- What data flows where? Any PII to external services?
- Are webhook endpoints authenticated?

**Category 5: Cost**
- API calls per run (fixed + variable based on input size)
- Token estimate per LLM call
- Total cost per run at expected volume
- Monthly cost projection

**Resolution process:** The PM Agent presents findings in a table:

```
| # | Category | Finding | Severity | Resolution |
|---|----------|---------|----------|------------|
| 1 | Missing Step | No pagination on SAM.gov API call | WARNING | Added loop with offset parameter |
| 2 | Failure Mode | LLM could return score > 100 | WARNING | Gate already validates range |
| 3 | Scope | Email formatting is complex enough to split | INFO | Keep as single step for now |
```

If there are CRITICAL findings or ambiguities that require user input, the PM Agent asks before finalizing. The spec is updated with resolutions before handoff.

---

## Output Format

The PM Agent produces a JSON spec that the Build Agent consumes directly. The spec format must match what the Build Agent's `models.py` can parse and what `wire.py` can translate.

**Format rules (learned from implementation):**
- **Expressions** must use n8n syntax: `={{ $json.field }}`, not `{{field}}`
- **Gate format** must use `conditional_branch` with `pass_to`/`fail_to` step IDs — the Build Agent doesn't support `schema_check` or `on_fail: "retry_step"` yet
- **Parameters** for Set and IF nodes must follow the structure in the echo spec example (see `build-agent-impl.md`). For other node types, use real n8n parameter names — they're passed through to the API as-is
- **Test cases** must have concrete JSON `input` objects (sent as webhook POST body), not prose descriptions. The `expected` object is matched key-by-key against the webhook response. Use `"any non-empty string"` for dynamic fields like timestamps
- **Trigger** webhook types need a `path` and optionally `method` (defaults to POST)

```json
{
  "workflow_name": "Lead Qualification Webhook",
  "description": "Receives a lead from HubSpot, validates required fields, scores against ICP, returns qualification result",

  "trigger": {
    "type": "webhook",
    "path": "qualify-lead",
    "method": "POST",
    "description": "HubSpot sends lead data on form submission"
  },

  "steps": [
    {
      "id": "step_1",
      "name": "Validate Lead Fields",
      "node_type": "n8n-nodes-base.if",
      "determinism": "1.0",
      "description": "Check that email and company are present",
      "parameters": {
        "conditions": {
          "and": [
            { "field": "={{ $json.body.email }}", "operation": "isNotEmpty" },
            { "field": "={{ $json.body.company }}", "operation": "isNotEmpty" }
          ]
        }
      },
      "output_shape": { "pass": "same as input", "fail": "same as input" },
      "error_handling": { "on_failure": "route_to_error_branch" }
    },
    {
      "id": "step_2",
      "name": "Build Qualified Response",
      "node_type": "n8n-nodes-base.set",
      "determinism": "1.0",
      "description": "Return qualified status with lead data echoed",
      "parameters": {
        "assignments": [
          { "name": "status", "value": "qualified", "type": "string" },
          { "name": "email", "value": "={{ $json.body.email }}", "type": "string" },
          { "name": "company", "value": "={{ $json.body.company }}", "type": "string" },
          { "name": "qualified_at", "value": "={{ $now.toISO() }}", "type": "string" }
        ]
      },
      "output_shape": { "status": "string", "email": "string", "company": "string", "qualified_at": "string" }
    },
    {
      "id": "step_3",
      "name": "Build Rejection Response",
      "node_type": "n8n-nodes-base.set",
      "determinism": "1.0",
      "description": "Return rejection when required fields are missing",
      "parameters": {
        "assignments": [
          { "name": "status", "value": "rejected", "type": "string" },
          { "name": "reason", "value": "Missing required fields: email and company", "type": "string" }
        ]
      },
      "output_shape": { "status": "string", "reason": "string" }
    }
  ],

  "gates": [
    {
      "after_step": "step_1",
      "type": "conditional_branch",
      "description": "Route valid leads to qualification, invalid to rejection",
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
    "description": "Returns qualification result to HubSpot webhook caller"
  },

  "security": {
    "credentials_needed": [],
    "pii_handling": "Email addresses flow through but are not stored or sent to external services",
    "data_flow_notes": "Lead data received via webhook, processed in-memory, response returned to caller"
  },

  "cost_estimate": {
    "per_run": {
      "api_calls": "0 (no external APIs in v1)",
      "estimated_tokens": "0",
      "estimated_cost": "$0"
    }
  },

  "test_cases": [
    {
      "name": "Happy path — valid lead",
      "input": { "email": "jane@acme.com", "company": "Acme Corp" },
      "expected": {
        "status": "qualified",
        "email": "jane@acme.com",
        "company": "Acme Corp",
        "qualified_at": "any non-empty string"
      }
    },
    {
      "name": "Missing email — rejected",
      "input": { "email": "", "company": "Acme Corp" },
      "expected": {
        "status": "rejected",
        "reason": "Missing required fields: email and company"
      }
    },
    {
      "name": "Missing company — rejected",
      "input": { "email": "jane@acme.com", "company": "" },
      "expected": {
        "status": "rejected"
      }
    },
    {
      "name": "Empty payload — rejected",
      "input": {},
      "expected": {
        "status": "rejected"
      }
    }
  ],

  "components_used": [],
  "components_needed": [],

  "review_notes": [
    "v1 is validation-only. v2 will add LLM-based ICP scoring (step between validation and response)",
    "When ICP scoring is added, an IF gate will be needed after the LLM step to validate score format (schema_check gate type is not yet supported by the Build Agent — use conditional_branch with field checks)"
  ]
}
```

---

## Implementation Notes

### How It Runs

The PM Agent is an LLM-powered conversational agent. It can run as:
- An n8n workflow itself (webhook trigger → LLM nodes for interview → output spec JSON)
- A Claude Code skill/agent that produces the spec file
- A standalone script that talks to an LLM API

The output is always the JSON spec above, which the Build Agent consumes.

### What It Needs Access To

- n8n API (read-only) — to audit existing workflows
- Component library manifest — to check available components
- User conversation — to conduct the interview
- LLM API — for reasoning, decomposition, adversarial review

### What It Does NOT Do

- Create workflows
- Configure nodes
- Set credentials
- Activate anything
- Touch the n8n instance in any write capacity

The PM Agent is read-only on n8n and write-only on the spec file.

---

## Spec Quality Gate

The spec is NOT ready for the Build Agent until all of these are true:

```
□ Every step has a "Done When" that a test can verify
□ Every LLM step has a gate after it
□ Every API call has error handling defined
□ test_cases array includes happy path, edge cases, and error cases
□ Adversarial review completed with all CRITICALs resolved
□ MECE verified (if part of a multi-workflow system)
□ User approved the spec
□ Cost estimate reviewed and accepted
```

If any box is unchecked, the PM Agent loops back — it does not hand off an incomplete spec.

---

## Open Questions

1. Should the PM Agent maintain a registry of all workflow specs it has produced (for cross-referencing during audit)?
2. ~~Should the spec output be JSON or markdown?~~ **Resolved**: JSON for the Build Agent contract, with markdown annotations for human review. The JSON spec IS the contract; markdown descriptions within it are for readability.

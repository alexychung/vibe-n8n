# Vibe-n8n: Overall Workflow

> Disciplined workflow creation for n8n: plan before you build, test before you ship, extract patterns as you go.

| Field | Value |
|-------|-------|
| Status | Active |
| Last Updated | 2026-04-13 |
| Depends On | n8n instance (Railway/Docker), API key |
| Enables | a federal client automation, client workflow delivery |

---

## Recent Changes

| Date | What changed | Section |
|------|-------------|---------|
| 2026-04-13 | Added "Relationship to N2O Software Workflow" — where the analogy holds and where it breaks | Relationship |
| 2026-04-13 | Added pyramid structure, MECE, state tracking, codification step | All |
| 2026-04-13 | Initial spec | All |

---

## Relationship to N2O Software Workflow

This system borrows its structure from N2O's software development agents (pm-agent → build-agent). The transferable ideas are: **PM plans, builder builds**; adversarial review before building; MECE for multi-workflow systems; status tables for visibility; and "don't skip phases" discipline.

The mechanics are different. Be precise about where the analogy holds and where it breaks:

| Concept | Software (N2O) | n8n Workflows |
|---------|---------------|---------------|
| **Testing** | Automated test suite (RED/GREEN/REFACTOR). Tests re-run on every change. Regressions caught automatically. | Manual acceptance testing — run sample data, inspect output. No automated regression suite. No "failing test" to start from. |
| **Audit** | 3 parallel Sonnet subagents reading source code (Pattern Compliance, Gap Analysis, Testing Posture). | Mix of deterministic JSON checks (credential scanning, missing auth) and LLM review (architecture quality). Spec should distinguish which checks are programmatic vs. LLM-driven. |
| **Codification** | Document patterns in `.claude/skills/` — loaded into future conversations automatically. | Extract reusable sub-workflows as JSON files into a component library. Closer to publishing a package than documenting a pattern. |
| **"Done When"** | Automated test verifies it. Binary, repeatable, runs in CI. | Checklist verified by running the workflow once. Can't automatically re-verify retry behavior or failure paths without simulating failures. |
| **Spec handoff** | PM gives tasks with `done_when` text. Build Agent decides implementation. | PM spec includes implementation details (node types, parameters, data shapes) but can't get them exactly right because it has no write access. Build Agent must translate pseudocode expressions into real n8n syntax — it IS making design decisions. |

**Bottom line:** The PM → Builder separation and the phased discipline are the real value. Don't lean on "TDD" as a label — this is a build-and-verify workflow, not test-driven development.

---

## The Pipeline

```
User describes what they want
         |
         v
   +-----------+
   | PM Agent  |  Interview → Audit → Decompose → Adversarial Review
   +-----------+
         |
         | workflow spec (JSON)
         v
   +-----------+
   | Build Agent |  Scaffold → Wire → Test → Audit → Harden → Codify → Deploy
   +-----------+
         |
         | live workflow + report
         v
   +-----------+
   | Running   |  Monitoring → Versioning → Security alerts
   | Workflow  |
   +-----------+
```

---

## End-to-End Flow

### Step 1: User Input

The user describes what they want in natural language. This can come from:
- A chat interface (Slack, web UI, CLI)
- A GRAIL discovery document (for client engagements)
- A ticket/request in a project management tool

**Example:** "When a new lead is added to our HubSpot CRM, check if they match our ICP using AI, and if they score above 80, create a task for the sales rep and send them a Slack notification."

### Step 2: PM Agent Plans

The PM Agent runs its process (see pm-agent-spec.md):

1. **Interview** — asks clarifying questions if needed (trigger type, stakes, success criteria)
2. **Audit** — checks existing n8n workflows for overlaps or reusable pieces
3. **Decompose** — breaks the request into a step-by-step workflow spec
4. **Adversarial review** — stress-tests the spec for missing steps, failure modes, over/under-engineering

**Output:** A structured JSON workflow spec.

**Human checkpoint:** The spec is presented to the user for approval before building begins. The user can request changes, which loop back through decompose → review.

### Step 3: Build Agent Builds

The Build Agent takes the approved spec and builds (see build-agent-spec.md):

1. **Scaffold** — creates the workflow skeleton in n8n via API
2. **Wire** — configures and connects nodes one at a time, testing each
3. **Test** — runs end-to-end with test data (happy path, edge cases, failures)
4. **Audit** — security, best practices, resilience checks
5. **Harden** — fixes audit findings
6. **Deploy** — activates and monitors initial runs

**Output:** A live, tested, hardened n8n workflow.

### Step 4: Running Workflow

Once deployed, the workflow enters the operational phase:

- **Monitoring** — execution success/failure rates, cost per run, latency
- **Versioning** — every edit auto-saves, diffs described in plain English, Git-backed
- **Security** — ongoing credential expiry checks, error rate anomalies, cost spikes
- **Updates** — when the user wants changes, it goes back through PM Agent (lightweight interview for edits)

---

## System Architecture

```
+------------------------------------------------------------------+
|                        Vibe-n8n System                            |
|                                                                   |
|  +------------------+     +------------------+                    |
|  |    PM Agent      |     |    Build Agent     |                    |
|  |                  |     |                  |                    |
|  | - Interview      |---->| - Scaffold       |                    |
|  | - Audit existing |     | - Wire           |                    |
|  | - Decompose      |     | - Test           |                    |
|  | - Review         |     | - Audit          |                    |
|  |                  |     | - Harden         |                    |
|  +--------+---------+     | - Deploy         |                    |
|           |               +--------+---------+                    |
|           | reads                  | reads/writes                 |
|           v                        v                              |
|  +--------------------------------------------------+             |
|  |              n8n Instance (Railway/Docker)        |             |
|  |                                                   |            |
|  |  REST API: /api/v1/                               |            |
|  |  - GET/POST/PUT/DELETE /workflows                  |            |
|  |  - POST /workflows/{id}/activate                  |            |
|  |  - GET /executions                                |            |
|  |                                                   |            |
|  |  Credential Store                                 |            |
|  |  Workflow Engine                                  |            |
|  |  Webhook Server                                   |            |
|  +--------------------------------------------------+             |
|                                                                   |
|  +------------------+     +------------------+                    |
|  | Component Library|     | Security Layer   |                    |
|  |                  |     |                  |                    |
|  | - LLM w/ retry   |     | - Pre-deploy     |                    |
|  | - Schema validate|     |   review         |                    |
|  | - Alerter        |     | - Credential     |                    |
|  | - Rate limiter   |     |   monitoring     |                    |
|  | - PII detector   |     | - Cost anomaly   |                    |
|  |                  |     |   detection      |                    |
|  +------------------+     +------------------+                    |
+------------------------------------------------------------------+
```

---

## Key Handoffs

### PM Agent → Build Agent

The workflow spec JSON is the contract between the two agents. The PM Agent writes it, the Build Agent reads it. Neither agent does the other's job.

**The spec must contain everything the Build Agent needs to build without asking questions:**
- Every node, its type, its parameters
- Every connection between nodes
- Every gate and what it checks
- Every error path and what it does
- Credential references (by name, not by value)
- Test criteria (what "right" looks like)

If the Build Agent encounters an ambiguity in the spec, it fails back to the PM Agent — it does not make scope decisions.

### Build Agent → Deployed Workflow

The Build Agent hands off:
- A workflow ID in n8n (activated)
- Test results (all test cases with pass/fail)
- Audit report (all findings with resolution status)
- Generated documentation

### User → PM Agent (for updates)

When a user wants to change a running workflow:
1. PM Agent pulls the current workflow from n8n API
2. Lightweight interview: "You want to change X. That affects Y. Is that intentional?"
3. Produces an updated spec (diff from current)
4. Build Agent applies the changes, re-tests, re-audits

---

## Component Library

Reusable building blocks that both agents know about. The PM Agent references them in specs. The Build Agent imports them during build.

| Component | Node Type | What It Does |
|---|---|---|
| `llm_call_with_retry` | Sub-workflow | LLM call with exponential backoff, token tracking, model fallback |
| `schema_validator` | Code node | Validates JSON against a schema, routes pass/fail |
| `alerter` | Sub-workflow | Sends formatted alerts to Slack/email with context |
| `rate_limiter` | Code + Wait | Throttles execution to N runs per time period |
| `pii_detector` | Code node | Scans text for PII patterns before external sends |
| `credential_rotator` | Sub-workflow | Checks credential expiry, rotates, alerts on failure |
| `file_upload_handler` | Sub-workflow | Validates format/size, stores in configured location |
| `webhook_auth` | Code node | Validates incoming webhook auth (header, basic, HMAC) |

Components are stored as:
- Exportable workflow JSON files (for sub-workflows)
- Code snippets (for Code nodes)
- Node configuration templates (for standard node setups)

**Adding a new component:**
1. Build Agent identifies a reusable pattern during build
2. Extracts it into a standalone component
3. Adds test data and expected outputs
4. Documents: what it does, when to use it, configuration options
5. Registers it in the component manifest

**Criteria for extraction:**
- Will it be used in 3+ workflows?
- Is it non-trivial (not just a single API call)?
- Does it encapsulate error handling or security that people would otherwise forget?

---

## Security Layer

Automated, not optional. Runs at two points:

### Pre-Deploy (Build Agent Audit Phase)

LLM-powered review of the workflow JSON before activation. Checks credentials, permissions, data flow, error handling. See build-agent-spec.md Audit section.

### Ongoing (Post-Deploy)

Periodic checks on running workflows:

| Check | Frequency | Alert On |
|---|---|---|
| Credential expiry | Daily | Credentials expiring within 7 days |
| Error rate | Per execution | Error rate > 20% over last 10 runs |
| Execution volume | Hourly | Volume > 5x normal for this workflow |
| Cost per run | Per execution | Cost > 2x average for this workflow |
| Unauthorized changes | On save | Workflow edited outside of PM/Build pipeline |

---

## Version Control

All versioning is invisible to non-technical users.

**What n8n provides natively:**
- Auto-save (1-5 seconds)
- Draft/publish separation
- Version history with restore
- Git-backed environments

**What we add:**
- LLM-generated change descriptions (diff → plain English)
- Security review on publish
- PM Agent interview on significant changes
- Simplified rollback UI

**Flow:**
```
User edits workflow in n8n editor
  → Auto-saved
  → Diff generated against previous version
  → LLM describes change in plain English
  → Stored in version history + Git commit
  → User clicks "Publish"
  → Security review runs
  → Pass: goes live
  → Fail: plain-language warning, user decides
```

---

## Patterns and Anti-Patterns

### Do

- **Name everything descriptively.** "HubSpot Lead ICP Scorer" not "HTTP Request 3"
- **Test with real-shaped data.** One row doesn't catch volume problems
- **Build error paths first.** Before wiring the happy path, wire what happens when it fails
- **One workflow, one job.** If it does two things, split it
- **LLM outputs markdown, formatter converts to JSON.** Don't make the reasoning node double as a formatter
- **Use components.** Check the library before building from scratch

### Don't

- **Don't hardcode credentials.** Ever. Use n8n's credential store.
- **Don't skip the PM Agent.** "It's simple" is how you get broken workflows
- **Don't let LLM nodes run without output validation.** Always gate LLM output
- **Don't build 15-node chains without checkpoints.** Gate every 3-4 nodes
- **Don't ignore cost.** Track tokens and API calls per run
- **Don't trust webhook inputs.** Validate everything, check auth

---

## Build Order (What to Build First)

### Phase 1: Foundation
1. n8n instance running (Docker locally, Railway for prod)
2. API key configured
3. First manual workflow built via API (prove the API works)

### Phase 2: Build Agent (bottom-up)
4. Script that creates a workflow from a hand-written spec
5. Script that wires nodes and connections
6. Script that runs test data through a webhook and checks results
7. Script that audits a workflow JSON for security/best-practices
8. Combine into the Build Agent pipeline

### Phase 3: PM Agent (top-down)
9. Prompt/agent that conducts the 4-question interview
10. Prompt that decomposes answers into the spec format
11. Prompt that runs adversarial review on a spec
12. Integration with n8n API for workflow audit
13. Combine into the PM Agent pipeline

### Phase 4: Integration
14. PM Agent → spec file → Build Agent end-to-end
15. Component library with first 3-4 components
16. Security layer (pre-deploy review)
17. Version control hooks

### Phase 5: Polish
18. Non-technical user interface for edits/rollbacks
19. Ongoing monitoring and alerting
20. Dynamic repricing and multi-channel support (a federal client-style)

---

## State Tracking

Every workflow build is tracked through its phases. Both agents maintain a **workflow build log** — a lightweight record of what happened, when, and the outcome.

### Build Log Format

Each workflow build produces a log entry:

```json
{
  "build_id": "uuid",
  "workflow_name": "SAM.gov Contract Monitor",
  "spec_version": 1,
  "started_at": "2026-04-13T10:00:00Z",
  "phases": {
    "pm_interview": { "status": "done", "duration_s": 120 },
    "pm_audit": { "status": "done", "findings": 2 },
    "pm_decompose": { "status": "done", "spec_version": 1 },
    "pm_adversarial": { "status": "done", "issues_found": 1, "issues_resolved": 1 },
    "build_scaffold": { "status": "done", "node_count": 8 },
    "build_wire": { "status": "done", "nodes_wired": 8 },
    "build_test": { "status": "done", "pass": 5, "fail": 0 },
    "build_audit": { "status": "done", "critical": 0, "warning": 2, "info": 1 },
    "build_harden": { "status": "done", "fixes": 2 },
    "build_codify": { "status": "skipped", "reason": "deferred" },
    "build_deploy": { "status": "done", "workflow_id": "abc123" }
  },
  "completed_at": "2026-04-13T10:45:00Z"
}
```

The Build Agent outputs a **status table** after each phase:

```
## Build Status: SAM.gov Contract Monitor

| Phase | Status | Notes |
|-------|--------|-------|
| SCAFFOLD | done | 8 nodes created |
| WIRE | done | All nodes configured and connected |
| TEST | done | 7/7 test cases pass |
| AUDIT: Security | done | 0 critical, 1 warning |
| AUDIT: Best Practices | done | 0 critical, 1 warning |
| AUDIT: Resilience | done | 0 critical, 0 warning |
| HARDEN | done | 2 warnings fixed |
| DEPLOY | pending | |

Next: Activating workflow
```

---

## MECE Principle for Multi-Workflow Systems

When building multiple workflows for the same system (e.g., a federal client has contract monitoring, scoring, and notification workflows), apply MECE:

- **Mutually Exclusive**: Each workflow does exactly one job. No two workflows handle the same trigger or produce the same output.
- **Collectively Exhaustive**: Together, the workflows cover the entire automation need. No gaps.
- **Cross-reference**: Each workflow spec notes what's out of scope and which other workflow handles it.

**Bad** (overlapping):
```
# contract-monitor.md
Steps: Search SAM.gov, Score contracts, Send email

# contract-scorer.md
Steps: Score contracts, Generate report  ← OVERLAP on scoring!
```

**Good** (MECE):
```
# contract-monitor.md
Steps: Search SAM.gov, Filter by NAICS
Out of scope: Scoring → contract-scorer workflow

# contract-scorer.md
Steps: Score filtered contracts, Generate report
Input: Output of contract-monitor workflow
```

---

## Pattern Codification

After building 3+ workflows, patterns emerge. The system should codify them.

**When to extract a pattern:**
- Same node configuration used in 3+ workflows
- Same error handling approach repeated
- Same gate/validation structure recurring

**What gets codified:**
- New component in the component library (sub-workflow or code snippet)
- New entry in the patterns/anti-patterns section
- Updated PM Agent knowledge (so it recommends the pattern in future specs)

**The codification step happens after DEPLOY**, not before. You need real production data to validate a pattern, not just theory.

---

## Open Questions

1. Where should build logs be stored? Options: n8n execution history, separate SQLite DB, or a dedicated "meta" workflow that tracks builds.
2. Should the PM Agent be an n8n workflow itself, or a Claude Code skill that calls the n8n API?
3. ~~How should components be versioned?~~ **Resolved**: As exportable workflow JSON files in a `components/` directory, Git-tracked.

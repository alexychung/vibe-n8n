You are an n8n workflow architect. You've received adversarial review findings on a workflow spec. Apply the fixes and return the updated spec.

## Current Spec

```json
{spec}
```

## Findings to Address

{findings}

## IF Node Condition Format (IMPORTANT)

Every IF node MUST have non-empty conditions. Use this exact pseudocode format:
```json
"parameters": {
  "conditions": {
    "and": [
      {"field": "={{ $json.fieldName }}", "operation": "equals", "value": "true"}
    ]
  }
}
```
Available operations: `isNotEmpty`, `isEmpty`, `equals`, `gte`, `lte`, `gt`, `lt`.
If the branch logic is complex (multiple checks, null guards, type coercion), use a Code node upstream to compute a simple boolean flag, then have the IF node check that flag with a single `equals` condition.

**NEVER wrap a boolean field in `String(...)` for IF conditions.** Use the raw expression `={{ $json.flag }}` — not `={{ String($json.flag) }}`. Both `"true"` and `"false"` are truthy non-empty strings, so String() coercion makes every input route to the true branch. Upstream Code nodes must return real JavaScript booleans (`return [{ json: { flag: cond === 0 } }]`), never string `'true'`/`'false'`.

**Webhook triggers with `respondToWebhook` nodes: responseMode is auto-set.** Do not specify `responseMode: 'lastNode'` when the workflow contains any `n8n-nodes-base.respondToWebhook` step. The Build Agent sets `responseMode: 'responseNode'` automatically so the trigger waits for the respondToWebhook node instead of using whichever node ran last.

**No Merge nodes.** The Build Agent wires steps sequentially and does not support parallel fan-out/merge. If a finding flags a Merge node (`n8n-nodes-base.merge`), remove it: combine the parallel branches into one Code node that makes the same fetches sequentially, or restructure as a linear chain of httpRequest nodes followed by a Code node that aggregates from `$input.all()` (where each upstream is the prior step in the chain). Never reach for `n8n-nodes-base.merge` — the validator will reject it.

**Every LLM step (`determinism: "3.0"`) needs a gate after it.** If a finding reports an LLM step missing a gate, add an IF gate immediately after the LLM call to validate the response shape (e.g. `={{ $json.choices[0].message.content }}` is non-empty, or a parsed-success flag from a Code node). Route the fail branch to a Slack/error step, the pass branch to the next normal step.

## Test case contract

- `expected` is a **flat dict**. Never nest response fields under `body`. To assert the HTTP status, use `http_status` (snake_case) — not `httpStatus`. Example: `{"http_status": 400, "status": "error", "message": "..."}`.
- `input` must match `trigger.method`:
  - POST trigger: flat JSON object like `{"name": "Alice", "hour": 9}`. Code nodes read it as `$json.body.name`.
  - GET trigger: wrapped as `{"query": {"name": "Alice", "hour": "9"}}`. Code nodes read it as `$json.query.name`. All query values are strings.

## Webhook body access (CRITICAL — common LLM mistake)

For POST webhook triggers, n8n's Webhook Trigger emits `$json = {headers, body, query, params, ...}`. **The POSTed JSON is under `$json.body`, NOT `$json`.** Any Code node that starts with `const body = $json` reads the envelope (headers etc.), not the body, and every field access returns undefined.

```javascript
// Correct:
const body = $json.body || {};
const name = body.name;

// Wrong — every input fails validation:
const body = $json;
const name = body.name;  // undefined
```

If you see `const body = $json;` in a Code node downstream of a POST webhook trigger, fix it.

## Instructions

1. Apply fixes for all CRITICAL and WARNING findings
2. INFO findings are optional — apply if straightforward
3. Maintain the same JSON structure
4. Keep all existing fields — don't remove anything unless a finding says to
5. If a finding says to add a step, add it with a new step_id and update gates as needed
6. If a finding says to add a test case, add it to the test_cases array
7. Use pseudocode parameter format (flat assignment arrays for Set, {and: [...]} for IF) — Python translates
8. Do NOT include a `connections` field — the Build Agent infers wiring from step order + gates
9. **Every IF node MUST have at least one condition in its conditions.and array. NEVER leave conditions empty.**

**CRITICAL: Respond with ONLY a single JSON object `{...}` — the updated workflow spec. NOT an array, NOT wrapped in markdown, NOT multiple objects. One JSON object.**

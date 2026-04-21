# Time-of-Day Greeting Webhook

Accepts HTTP POST requests with name and hour parameters, validates inputs, and returns a time-appropriate greeting or a 400 error with a descriptive message.

- **Trigger:** webhook at `/greeting` (POST)
- **Nodes:** 7

## Required Credentials

None.

### Auto-generated webhook auth

HARDEN auto-created an `httpHeaderAuth` credential per webhook node to replace the default unauthenticated setup. When importing this JSON into another n8n instance, re-create a **Header Auth** credential and re-point the webhook node(s) at it.

## Calling the Webhook

This workflow requires header-based auth on its webhook: `X-Webhook-Auth`.

Tokens were generated during HARDEN and stored in `build-logs/{slug}-auth.env` — they are not included here because
this README is meant to be checked in. When importing into another
n8n instance, create a new `Header Auth` credential (Settings →
Credentials → New → Header Auth) and point the webhook node at it.

Example request:

```bash
curl -X POST "$N8N_BASE_URL/webhook/{path}" \
  -H "X-Webhook-Auth: $WEBHOOK_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"your": "payload"}'
```

## Import

### Via n8n UI
1. Open your n8n instance → Workflows → **Import from File**
2. Select `time-of-day-greeting-webhook.json` — opens on the canvas
3. Configure the credentials listed above (if any)
4. Toggle **Active**

### Via API
```bash
curl -X POST "$N8N_BASE_URL/api/v1/workflows" \
  -H "X-N8N-API-KEY: $N8N_API_KEY" \
  -H "Content-Type: application/json" \
  --data-binary @time-of-day-greeting-webhook.json
```

# Webhook Echo

Receives a POST, validates input, echoes back with status and timestamp

- **Trigger:** webhook at `/echo-test` (POST)
- **Nodes:** 4

## Required Credentials

None.

## Import

### Via n8n UI
1. Open your n8n instance → Workflows → **Import from File**
2. Select `webhook-echo.json` — opens on the canvas
3. Configure the credentials listed above (if any)
4. Toggle **Active**

### Via API
```bash
curl -X POST "$N8N_BASE_URL/api/v1/workflows" \
  -H "X-N8N-API-KEY: $N8N_API_KEY" \
  -H "Content-Type: application/json" \
  --data-binary @webhook-echo.json
```

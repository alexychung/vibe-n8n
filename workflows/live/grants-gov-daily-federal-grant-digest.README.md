# Grants.gov Daily Federal Grant Digest

Fetches federal grant opportunities from Grants.gov daily, filters by nonprofit focus areas, funding ceiling, and deadline, generates personalized fit blurbs via OpenAI, and posts a curated digest to an internal Slack webhook endpoint.

- **Trigger:** cron on schedule `0 7 * * 1-5`
- **Nodes:** 18

## Required Credentials

None.

## Import

### Via n8n UI
1. Open your n8n instance → Workflows → **Import from File**
2. Select `grants-gov-daily-federal-grant-digest.json` — opens on the canvas
3. Configure the credentials listed above (if any)
4. Toggle **Active**

### Via API
```bash
curl -X POST "$N8N_BASE_URL/api/v1/workflows" \
  -H "X-N8N-API-KEY: $N8N_API_KEY" \
  -H "Content-Type: application/json" \
  --data-binary @grants-gov-daily-federal-grant-digest.json
```

# Customer Health Score and Churn Risk Alert Pipeline

Weekday 6am Pacific pipeline that correlates Amplitude usage, Zendesk support, and Stripe billing signals to generate AI health scores for ~200 accounts, then routes critical/at-risk alerts to Slack channels, CSM emails, and executive stakeholders while suppressing healthy accounts. Accounts are processed sequentially via SplitInBatches (batchSize=1) with 300ms inter-account pacing.

- **Trigger:** cron
- **Nodes:** 43

## Required Credentials

Create these in n8n (Settings → Credentials) before activating:

- `Amplitude API key (HTTP Basic Auth)`
- `Zendesk Basic Auth (email:api_token)`
- `Stripe Secret Key (Bearer token)`
- `OpenAI API key (Bearer token)`
- `SendGrid API key (Bearer token) – requires both d-critical-health and d-at-risk-health templates`
- `Slack incoming webhook URLs for #cs-critical and #cs-at-risk`
- `PagerDuty Events API v2 integration key`
- `Internal CSM Directory API auth token (must return stripe_customer_id and zendesk_org_id per account)`

## Import

### Via n8n UI
1. Open your n8n instance → Workflows → **Import from File**
2. Select `customer-health-score-and-churn-risk-alert-pipeline.json` — opens on the canvas
3. Configure the credentials listed above (if any)
4. Toggle **Active**

### Via API
```bash
curl -X POST "$N8N_BASE_URL/api/v1/workflows" \
  -H "X-N8N-API-KEY: $N8N_API_KEY" \
  -H "Content-Type: application/json" \
  --data-binary @customer-health-score-and-churn-risk-alert-pipeline.json
```

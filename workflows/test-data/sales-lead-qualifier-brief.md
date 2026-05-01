# Inbound Sales Lead Qualifier

When someone fills out the "Talk to Sales" form on our marketing site, the
form posts to a webhook (we control the endpoint). Right now everything goes
into a single Slack channel and our SDRs triage manually, which means tier-1
prospects sit for hours behind tire-kickers. I want the workflow to do the
first-pass qualification automatically.

The form gives us: work email, full name, company name, role, headcount band
(1-10, 11-50, 51-200, 201-1000, 1000+), and a free-text "what are you trying
to solve" field. From the email domain we should look up the company — use
Clearbit's company enrichment endpoint (we have an API key in the credentials
store under `CLEARBIT_API_KEY`). That gives us industry, estimated revenue,
and tech stack. If Clearbit returns nothing or 404s, don't fail the whole
workflow — just proceed without enrichment and tag the lead as "unenriched".

Then score the lead. We care about: company size (51-200 is our sweet spot,
1000+ is too enterprise for our current motion, 1-10 is too small), industry
fit (B2B SaaS, fintech, and healthcare are good; consumer, agencies, and
crypto are bad), and the free-text field — does the prospect describe a
problem we actually solve, or are they fishing? Use an LLM to read the
free-text and rate problem-fit on a 1-5 scale with a one-sentence reason.
Combine the three signals into a tier: A (route to AE immediately), B (route
to SDR queue), C (auto-respond with a nurture-sequence link, no human
involved).

For tier A, post to the #sales-hot Slack channel via our webhook poster
(https://internal.example/slack/post) AND create a record in HubSpot via
their contacts API (key is `HUBSPOT_API_KEY`). For tier B, create the HubSpot
record but post to #sales-queue instead. For tier C, just send the auto-reply
via SendGrid (`SENDGRID_API_KEY`) with our nurture template ID `d-abc123`.

Webhook should respond within 3 seconds with `{"status": "received",
"lead_id": "..."}` regardless of tier — the form needs a fast response.

Stakes are high. If a tier-A lead gets misrouted to nurture, we lose deals.
If the workflow goes down silently, we lose deals. We need alerting when the
LLM scoring step fails or when HubSpot rejects a record.

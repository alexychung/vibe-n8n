# SaaS Customer Health Monitor

I run customer success at a B2B SaaS company and we keep finding out a
customer is going to churn AFTER they've already decided. By the time their
QBR comes around it's too late. I want a daily job that pulls signals from
three places, correlates them, and pushes anyone whose health is deteriorating
into the right intervention queue.

Run it weekday mornings at 6am Pacific (so the CSM team has it by their 9am
standup). Pull from:

1. Our product analytics (Amplitude). For each account, get the last 30 days
   of usage: daily active users, key feature events (we care about
   `report_generated`, `dashboard_shared`, `api_call`), and session count.
   API key is in `AMPLITUDE_API_KEY`. Their export API is async and slow —
   if it takes more than 60 seconds, give up and skip that account, log it.

2. Zendesk for support tickets in the last 30 days, grouped by account. We
   want ticket count, average first-response time, and any tickets tagged
   `escalation` or `bug-blocker`. Auth is basic auth, creds in
   `ZENDESK_EMAIL` + `ZENDESK_API_TOKEN`.

3. Our billing system (Stripe). Pull subscription status, MRR, and any
   payment failures in the last 90 days. Key is `STRIPE_API_KEY`.

For each account where we have data from at least two of the three sources,
ask an LLM to assess overall health and produce: a score 0-100, a
single-sentence headline of the biggest concern (or "stable" if none), and
2-3 specific intervention suggestions tied to actual signals (not generic
advice — if the suggestion would apply to any customer, it's useless).

Buckets:
- Score 0-40: critical, needs exec attention. Post to #cs-critical Slack
  channel AND email the assigned CSM AND email the VP of CS. Use SendGrid
  (`SENDGRID_API_KEY`), template ID `d-critical-health`.
- Score 41-65: at risk, CSM intervention needed. Post to #cs-at-risk and
  email the CSM only.
- Score 66-100: healthy, do nothing (don't even log — too noisy).

Mapping from account ID to assigned CSM and CSM email is in our internal
directory at https://internal.example/cs/csm-lookup?account_id={id}. If
that endpoint 404s for an account, default to vp-cs@example.com and note
"unassigned" in the alert.

Volume: ~200 accounts. Don't blow up Amplitude or Zendesk's rate limits —
they'll throttle us. Pace the requests.

Stakes: very high. This is replacing manual triage that is currently
happening (badly). Missing a critical-bucket account for a day could mean
losing the renewal. If the whole workflow fails, page me — alert webhook
at https://internal.example/pagerduty/cs-monitor.

We've never deployed an LLM-driven workflow before, so I want the LLM
prompt itself reviewable somewhere — don't bury it.

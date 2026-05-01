# Federal Grants Watchlist

We help nonprofits chase federal grant opportunities and right now my team is
manually checking Grants.gov every morning, which is killing us. I need
something that runs on its own and sends us a short list of grants worth
looking at, every weekday morning before standup (call it 7am Eastern).

The way we decide if a grant is worth pursuing: it has to match one of our
focus areas (right now: workforce development, rural broadband, veteran
services, early childhood education), the funding ceiling has to be above
$250k (anything smaller isn't worth the proposal cost), and the application
deadline has to be at least 21 days out (we won't take rush jobs). If a grant
fits all three, we want it on the list. If it's borderline — say it's in a
focus area we're growing into, or the ceiling is just under $250k — flag it
but don't drop it, because sometimes those are worth a conversation.

For each one that makes the list, I want a quick blurb (2-3 sentences) on what
the funder is looking for and why we'd be a fit, then the link to the
opportunity, the agency, the funding ceiling, and the deadline. The blurb
matters more than anything — that's what gets people to actually click. If
your blurb is generic ("supports community programs"), it's useless.

Send the digest as an HTTP POST to our internal Slack-poster service at
https://internal.gpsfed.example/slack/grants — it expects `{"summary": "...",
"items": [{...}]}` and handles the actual Slack formatting. If the digest is
empty (nothing matched), still post something — just a short "nothing today"
note so we know the workflow ran.

Stakes are medium-high. Missing a grant once isn't fatal but if this thing
silently breaks for a week we'll miss a real one. We need to know if it fails.

Use Grants.gov's public search API. We don't have keys for any LLM service yet
— pick one and tell me which.

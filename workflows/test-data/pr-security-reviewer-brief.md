# GitHub PR Security Reviewer

Our security team can't keep up with PR review on the platform team's repos
and the eng director wants an automated first-pass that flags risky changes
before a human gets pulled in. The bot doesn't replace human review — it
shrinks the surface so humans only look at PRs that actually need security
eyes.

GitHub will fire a webhook (we configure the org webhook) on pull_request
events with action `opened` or `synchronize` (i.e. new PR or new push to
existing PR). The webhook payload tells us the repo, PR number, and head
SHA. Only run on three repos for now: `acme/api`, `acme/web`, `acme/infra`.
Ignore everything else (respond 200 fast, do nothing).

For each in-scope PR, fetch the diff via the GitHub API
(`/repos/{owner}/{repo}/pulls/{number}/files`, auth header
`Authorization: Bearer $GITHUB_TOKEN`). The response is paginated — for now
assume PRs under 100 files, we'll worry about pagination later if we hit it,
but log a warning if the response says there are more pages.

Pass the diff to an LLM with a prompt that looks for: hardcoded secrets or
API keys, new dependencies (changes to requirements.txt, package.json,
go.mod, Cargo.toml), changes to auth/authz code (anything under `auth/`,
`middleware/`, or files matching `*permission*`, `*role*`), changes to SQL
queries that look like string concatenation rather than parameterized
queries, and any new `eval()` or `exec()` calls. The LLM should return a
JSON object with a `risk_level` (none/low/medium/high) and a `findings` array
where each finding has `file`, `line_range`, `category`, and `explanation`.

If risk_level is `none` or `low` — do nothing visible (just log internally
that we ran). If `medium` — post a non-blocking comment on the PR with the
findings, formatted as a markdown table. If `high` — post the comment AND
request a review from the @acme/security team via the GitHub reviewers API.
Never block the merge — humans decide.

Webhook should ack within 2 seconds (GitHub will retry if we're slow). The
LLM call and posting can take longer — that's fine, GitHub doesn't care once
we've acked.

Stakes: high for false negatives (real security issue slips by), medium for
false positives (annoys developers, they'll ignore the bot). We'd rather err
toward fewer findings with high confidence than spray noise.

If the workflow itself errors (LLM down, GitHub API down), don't post
anything to the PR — silent failure is better than a confusing comment.
But fire an alert to the security team's Slack via
https://internal.example/slack/sec-alerts.

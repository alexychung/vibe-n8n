# Multi-User vibe-n8n

> Each user signs in and sees only their own specs, builds, and deployed workflows. App-level isolation on top of a shared n8n instance + Neon Postgres for user/ownership state.

| Field | Value |
|-------|-------|
| Status | Draft (not yet implemented) |
| Last Updated | 2026-04-23 |
| Depends On | Neon Postgres project, `asyncpg`, `argon2-cffi` |
| Enables | Multi-tenant SaaS deployment of vibe-n8n |

---

## Recent Changes

| Date | What changed | Section |
|------|-------------|---------|
| 2026-04-23 | Initial draft | All |

---

## Goal

Let multiple users sign up / log in to the same vibe-n8n deployment and have their own isolated view of:

1. **Specs and requirements** they've planned (PM Agent output).
2. **Builds** they've run (Build Agent invocations and outcomes).
3. **Deployed workflows** they've shipped to the shared n8n instance.
4. **Executions** of their workflows (filtered from the shared n8n executions list).

Everything else stays as-is: one n8n instance on Railway, one Anthropic key, one OpenAI key, one Build/PM Agent codebase.

---

## Non-Goals

Stating these up front because they're tempting scope creep:

- **True tenant isolation inside n8n.** n8n's public API is instance-scoped — one API key, no per-user auth. Users logged into the n8n UI directly would still see everyone's workflows. We do **not** try to patch this. See "Trust model" below.
- **Per-user API keys / secrets.** All Anthropic + OpenAI calls hit the shared keys. Usage attribution and per-user rate limits come later if needed.
- **Teams / shared workflows.** Single-user ownership only. No `share-with` semantics, no roles, no ACLs. Add if needed.
- **OAuth / SSO.** Email + password only for v1. Add Google OAuth later as a drop-in.
- **Migration of existing filesystem specs.** Existing `workflows/test-data/web-*-spec.json` become "system specs" visible to all (or none) — decide at cutover. Not worth back-filling ownership.
- **Reworking the CLIs.** `python -m agents.pm_agent` and `python -m agents.build_agent` keep running as local-user tools with no auth. Only the web app is multi-user.

---

## Trust Model (read this before building)

**What we protect against:** curious / mistaken / mildly adversarial other users of the same vibe-n8n deployment seeing each other's work.

**What we do NOT protect against:** a user with direct n8n UI access (same instance) seeing all workflows, or someone with the raw n8n API key calling it directly. If your threat model requires that level of isolation, you need one n8n instance per tenant — which is an order of magnitude more work than this spec.

Trust boundary: the **web app is the only way in**. If you trust the web app's auth, you get per-user views; if you bypass it (n8n UI, direct API calls, DB access), you see everything.

---

## Architecture

```
           ┌─────────────┐
           │   browser   │
           └──────┬──────┘
                  │ cookie (session_token)
         ┌────────▼────────┐
         │  vibe-n8n web   │──┐
         │  (FastAPI)      │  │
         └────┬────────────┘  │
              │               │
    ┌─────────▼─────┐    ┌────▼────────┐
    │  Neon (PG)    │    │  n8n REST   │
    │  users,       │    │  (tags =    │
    │  sessions,    │    │   filter)   │
    │  specs,       │    └─────────────┘
    │  builds,      │
    │  ownership    │
    └───────────────┘
```

Every authenticated request is stamped with `user_id`. Reads are filtered by `user_id`. Writes (new spec, new build, new n8n workflow) set `user_id` on the row and — crucially — **tag the n8n workflow with `vibe_owner:<user_id>`** so the workflow list filter has something to match on.

---

## Schema (Postgres / Neon)

```sql
-- Users
CREATE TABLE users (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email      CITEXT UNIQUE NOT NULL,
  pw_hash    TEXT NOT NULL,        -- argon2id
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_login TIMESTAMPTZ
);

-- Sessions (cookie auth; token = random 32 bytes, hex-encoded)
CREATE TABLE sessions (
  token_hash  BYTEA PRIMARY KEY,    -- sha256 of the cookie; never store the raw token
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at  TIMESTAMPTZ NOT NULL,
  last_used   TIMESTAMPTZ NOT NULL DEFAULT now(),
  user_agent  TEXT
);
CREATE INDEX sessions_expires_at_idx ON sessions(expires_at);

-- Specs produced by the PM Agent (via web app)
CREATE TABLE specs (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  workflow_name  TEXT,
  spec_json      JSONB NOT NULL,
  brief_text     TEXT,               -- nullable; only if produced from a brief
  requirements   JSONB,              -- nullable; only if from the interview flow
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX specs_user_created_idx ON specs(user_id, created_at DESC);

-- Builds (Build Agent invocations)
CREATE TABLE builds (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  spec_id        UUID REFERENCES specs(id) ON DELETE SET NULL,
  n8n_workflow_id TEXT,               -- populated on success
  status         TEXT NOT NULL,       -- running | success | failed
  exit_code      INT,
  log            TEXT,                -- full stdout capture
  started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at    TIMESTAMPTZ
);
CREATE INDEX builds_user_started_idx ON builds(user_id, started_at DESC);
CREATE INDEX builds_n8n_workflow_idx ON builds(n8n_workflow_id);

-- Ownership index of n8n workflows.
-- Authoritative source of who-owns-what; the n8n tag is a redundant hint.
CREATE TABLE workflow_owners (
  n8n_workflow_id TEXT PRIMARY KEY,
  user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX workflow_owners_user_idx ON workflow_owners(user_id);
```

**Why JSONB for specs/requirements instead of the filesystem?** Filesystem works for single-user but doesn't survive a Railway container restart cleanly (ephemeral disk). JSONB in Neon is durable + queryable + per-user-filterable with a single index. Keep filesystem paths for Build Agent subprocess inputs (it reads from disk); the web app writes a tmpfile, shells out, then captures the result back into JSONB.

**Why hash the session token?** Same reason you hash passwords: a DB breach shouldn't hand out live session cookies.

---

## Auth Flow

### Signup

```
POST /api/auth/signup   { email, password }
  → hash with argon2id (cost tuned ~100ms on Railway)
  → INSERT users
  → create session row
  → Set-Cookie: vibe_session=<token>; HttpOnly; Secure; SameSite=Lax; Max-Age=2592000 (30d)
  → 200 { user: { id, email } }
```

### Login

Same shape as signup but verifies password and only creates a session. Password-wrong returns 401 with a uniform delay (don't leak whether the email exists).

### Session validation (middleware)

Replaces the current `BasicAuthMiddleware`. For every request:

1. Read `vibe_session` cookie.
2. Hash → look up in `sessions`. Reject if missing, expired, or not-looked-up within 7 days (sliding idle timeout).
3. Attach `request.state.user_id` + `request.state.user_email` for route handlers.
4. Update `sessions.last_used` asynchronously (fire-and-forget, like `log_request`).

Public (unauthenticated) paths: `/api/health`, `/api/auth/signup`, `/api/auth/login`, `/static/*`, `/` (serves the login page when no cookie present).

### Logout

```
POST /api/auth/logout
  → DELETE sessions WHERE token_hash = ?
  → Set-Cookie: vibe_session=; Max-Age=0
```

---

## Endpoint Changes

Everything currently public-within-auth takes a `user_id` and filters on it. Concrete changes:

| Endpoint | Change |
|----------|--------|
| `GET /api/workflows` | Return only n8n workflows where `id IN (SELECT n8n_workflow_id FROM workflow_owners WHERE user_id = ?)` |
| `GET /api/workflows/{id}` | 404 if not owned by user |
| `POST /api/workflows/{id}/run` | 404 if not owned |
| `POST /api/workflows/{id}/activate|/deactivate|DELETE` | 404 if not owned. DELETE also removes the `workflow_owners` row. |
| `GET /api/workflows/{id}/executions` | 404 if not owned |
| `GET /api/specs` | Return rows from `specs` table where `user_id = ?` instead of filesystem scan |
| `GET /api/specs/content` | Look up by spec `id`, not by filesystem path |
| `POST /api/plan` | Write spec → `specs` table with `user_id`. Returns `spec_id` + JSON (no file path). |
| `POST /api/build` | Take `spec_id` (not `spec_path`). Resolve to a tmpfile, run Build Agent subprocess, capture stdout into `builds.log`, extract `workflow_id`, `INSERT workflow_owners`, tag the n8n workflow. |
| `POST /api/interview/start|finish` | Persist to `specs.requirements` (JSONB) keyed by the session's user |
| `POST /api/stt` | Unchanged (no persistence) |

New endpoints:
- `POST /api/auth/signup`
- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/me` → `{ email, created_at }` — frontend uses this to know "am I logged in?" on page load

---

## n8n Workflow Tagging

On every Build Agent deploy (server-side, after reading `workflow_id` from the stream):

```python
await n8n_request('POST', f'/api/v1/workflows/{workflow_id}', body={
    'tags': existing_tags + [f'vibe_owner:{user_id}'],
})
```

The `workflow_owners` row is the **authoritative** ownership record; the tag exists only so someone debugging in the n8n UI can tell whose workflow they're looking at. The web app never trusts the tag for access control — it trusts the DB row.

Workflow names could also be prefixed (`[user@example.com] Daily Stripe Summary`) for UI legibility; leave as a config knob.

---

## Frontend Changes

- **Login page** (`/login`) — vanilla form, POSTs to `/api/auth/login`, redirects to `/`.
- **Signup page** — same.
- **Header** — replace the single "vibe-n8n" title with user email + Logout button.
- **On 401 response** from any `/api/*` call, redirect to `/login`.
- **On page load**, call `GET /api/me` and conditionally redirect.

No other tab UX changes: Plan / Workflows / Specs already key off what the backend returns, and the backend now returns filtered data.

---

## Open Questions

- **Password reset flow.** v1 can skip; v1.1 needs email delivery (SES / Resend / Postmark — pick cheap).
- **Admin user.** Needed for debugging ("whose spec is this?"). Add an `is_admin` BOOL on `users` and a `GET /api/admin/specs` that doesn't filter. Defer.
- **Rate limiting.** Anthropic + OpenAI calls cost money. Per-user monthly budget cap on `POST /api/plan` and `POST /api/interview/*` is probably the right granularity. Defer until someone abuses it.
- **Existing workflows.** On first deploy, there's ~1 n8n workflow with no owner row. Either auto-assign to the first admin account on signup, or mark as "system-owned" and visible to all. Either's fine; pick when migrating.
- **`webhook-test` mode auth.** Test webhooks are currently open to anyone who knows the path. Not a new problem but worth noting for a multi-tenant deploy.

---

## Work Breakdown (rough)

Roughly 2 days of focused work, in this order:

1. **Neon project + connection pool** (30 min) — `asyncpg` pool at app startup, migrations as plain SQL files under `web/migrations/`.
2. **Users + sessions tables + argon2 hashing** (2h) — `/api/auth/*` endpoints, replace `BasicAuthMiddleware` with `SessionMiddleware`.
3. **Login / signup frontend** (2h) — two simple HTML pages, same styling as current UI.
4. **`specs` table + migrate `POST /api/plan` to write there** (2h) — drop the filesystem-based spec store; update `GET /api/specs` and `GET /api/specs/content`.
5. **`builds` + `workflow_owners` tables** (2h) — hook into the Build Agent stream completion; tag n8n workflow on success.
6. **Filter workflow/executions endpoints by `workflow_owners`** (1h).
7. **Admin backfill command** (30 min) — one-off script to assign existing n8n workflows to an admin user.
8. **Test with two accounts** (1h) — sign up two users, each creates a workflow, confirm neither sees the other's.

---

## Testing Plan

Before any of this merges, need:

- Unit tests for password hashing round-trip, session creation + lookup + expiry.
- Integration test for the full auth flow against a real Neon branch (Neon branches are ephemeral → good for CI).
- Two-user isolation test: create workflow as user A, confirm user B gets 404 on detail / empty list on `/api/workflows`.
- Cookie security: HttpOnly, Secure in production, SameSite=Lax.
- Session expiry: expired cookie → 401, not 500.

---

## What NOT to build in v1

Tempting but out-of-scope:

- Per-user API key rotation
- SSO / OAuth
- Audit log UI ("who deleted this workflow?")
- Soft-delete + recovery
- Email verification on signup
- Teams / shared workflows
- Custom domain per tenant

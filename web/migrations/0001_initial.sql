-- Initial schema for vibe-n8n multi-user mode.
-- See specs/multi-user-spec.md for the full design.

CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- for gen_random_uuid()

CREATE TABLE IF NOT EXISTS users (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email      CITEXT UNIQUE NOT NULL,
  pw_hash    TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_login TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash  BYTEA PRIMARY KEY,
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at  TIMESTAMPTZ NOT NULL,
  last_used   TIMESTAMPTZ NOT NULL DEFAULT now(),
  user_agent  TEXT
);
CREATE INDEX IF NOT EXISTS sessions_expires_at_idx ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);

CREATE TABLE IF NOT EXISTS specs (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  workflow_name  TEXT,
  spec_json      JSONB NOT NULL,
  brief_text     TEXT,
  requirements   JSONB,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS specs_user_created_idx ON specs(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS builds (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  spec_id         UUID REFERENCES specs(id) ON DELETE SET NULL,
  n8n_workflow_id TEXT,
  status          TEXT NOT NULL,
  exit_code       INT,
  log             TEXT,
  started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS builds_user_started_idx ON builds(user_id, started_at DESC);
CREATE INDEX IF NOT EXISTS builds_n8n_workflow_idx ON builds(n8n_workflow_id);

-- Authoritative ownership of n8n workflows. Workflows in n8n that are NOT
-- in this table belong to nobody — invisible to all users in multi-user mode.
CREATE TABLE IF NOT EXISTS workflow_owners (
  n8n_workflow_id TEXT PRIMARY KEY,
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS workflow_owners_user_idx ON workflow_owners(user_id);

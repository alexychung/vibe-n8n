# vibe-n8n web UI

FastAPI wrapper around the PM Agent, Build Agent outputs, and the n8n instance. Two tabs:

- **Plan** — natural-language brief (typed or dictated via Whisper) → streamed PM Agent output → spec JSON.
- **Workflows** — list deployed workflows, open them in n8n, and fire webhook-triggered workflows with a body.

## Run locally

```bash
pip install -r web/requirements.txt

# uvicorn is run from the repo root so `agents.*` imports resolve.
uvicorn web.app:app --reload --port 8000
# open http://localhost:8000
```

Required env vars (read from `.env` in repo root if not already set):

| Var                 | Purpose                                         |
|---------------------|-------------------------------------------------|
| `N8N_BASE_URL`      | Where the backend calls n8n (internal URL OK)   |
| `N8N_PUBLIC_URL`    | URL the browser uses for "Open in n8n" links    |
| `N8N_API_KEY`       | n8n API key                                     |
| `ANTHROPIC_API_KEY` | PM Agent LLM                                    |
| `OPENAI_API_KEY`    | Whisper STT (voice input). Optional.            |
| `WEB_AUTH_PASSWORD` | Shared secret for HTTP Basic Auth. Single-user mode only. |
| `WEB_AUTH_USER`     | Username for Basic Auth. Defaults to `admin`.   |
| `DATABASE_URL`      | Postgres connection string (Neon). Setting this turns on **multi-user mode** — signup/login at `/login`, per-user spec/workflow isolation. Migrations run on startup. |
| `COOKIE_SECURE`     | `1`/`true` to set `Secure` flag on session cookies. Required for HTTPS deploys. Defaults off so localhost dev works. |
| `TEST_DATABASE_URL` | Used only by `web/tests/test_multi_user.py`. Should be a Neon branch — tests don't clean up. |

## Deploy to Railway

There's already an n8n service in the Railway project. Add a second service for this app:

1. **New Service → Deploy from GitHub** (or `railway up` from the repo root).
2. Set the root directory to `/` and the Dockerfile path to `web/Dockerfile` — or use `web/railway.json` which does this automatically.
3. Set env vars:
   - `N8N_BASE_URL` → use Railway's private network URL for the n8n service (e.g. `http://n8n.railway.internal:5678`).
   - `N8N_PUBLIC_URL` → the public n8n URL (e.g. `https://n8n-production-ff79.up.railway.app`).
   - `N8N_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` → copy from the n8n service / your local `.env`.
4. Health check is already wired to `/api/health`.

Railway will inject `$PORT`; the Dockerfile respects it.

## Endpoints

```
GET  /api/health                → liveness
GET  /api/config                → frontend feature flags
GET  /api/workflows             → list workflows w/ webhook info + edit_url
GET  /api/workflows/{id}        → single workflow
POST /api/workflows/{id}/run    → {mode, body, query, headers} → POST webhook
POST /api/plan                  → {brief} → SSE stream of PM Agent stdout, final event carries the spec
POST /api/stt                   → multipart audio → Whisper transcript
GET  /                          → index.html
```

Notes:

- **Running workflows**: only webhook-triggered workflows are runnable from the UI (n8n's public API has no manual-execute endpoint). Scheduled/manual workflows show a "run from the n8n UI" note.
- **PM Agent**: the backend shells out to `python -m agents.pm_agent plan --from-brief` so it can stream progress without refactoring the agent. Output specs land in `workflows/test-data/web-{session}-spec.json`.
- **Logging**: each request is appended to `build-logs/web-requests.jsonl`. PM/Build agent inputs continue to land in `build-logs/pm-inputs.jsonl` and `build-logs/build-inputs.jsonl`.

# vibe-n8n

AI agents that plan and build [n8n](https://n8n.io) workflows from natural language.

Two Python agents cooperate: a **PM Agent** interviews you, audits your existing workflows, and produces a reviewable JSON spec. A **Build Agent** takes that spec and builds the workflow in n8n via the REST API — scaffold, wire, test, audit, harden, deploy, export.

```
brief (natural language)
     │
     ▼  python -m agents.pm_agent
┌──────────────────────────────────────────────────┐
│ PM AGENT                                         │
│  interview → audit → decompose → review → spec   │
└──────────────────────────────────────────────────┘
     │
     ▼  spec.json
┌──────────────────────────────────────────────────┐
│ BUILD AGENT                                      │
│  scaffold → wire → test → audit → harden         │
│            → codify → deploy → export            │
└──────────────────────────────────────────────────┘
     │
     ▼
deployed n8n workflow + workflows/live/{slug}.json + README
```

## Install

```bash
git clone https://github.com/alexychung/vibe-n8n.git
cd vibe-n8n
pip install anthropic          # only the PM Agent needs a dependency
```

Create a `.env` file in the project root:

```bash
N8N_BASE_URL=http://localhost:5678
N8N_API_KEY=...                # Settings → n8n API → Create API key
ANTHROPIC_API_KEY=sk-ant-...   # only needed for the PM Agent
```

## Quick start

Start your n8n instance (`n8n start` locally), then:

```bash
# Plan a workflow from a natural-language brief
python -m agents.pm_agent plan --from-brief workflows/test-data/weather-brief.md

# Or interview the user directly
python -m agents.pm_agent plan "Monitor SAM.gov and email me a daily digest"

# Build a workflow from a spec
python -m agents.build_agent build workflows/test-data/echo-spec.json
```

After a successful build, the Build Agent writes:

- `workflows/live/{slug}.json` — portable workflow (no credentials, no IDs)
- `workflows/live/{slug}.README.md` — trigger summary, required credentials, import instructions

Ship those two files to anyone with their own n8n instance; they import with two clicks and plug in their own credentials.

## How it works

### PM Agent (`agents/pm_agent/`)

| Phase | What it does |
|-------|--------------|
| Interview | Adaptive Q&A (interactive or inferred from a brief file) |
| Audit | Checks existing n8n workflows for conflicts or reusable components |
| Decompose | LLM picks steps + gates; Python translates to n8n-shaped JSON |
| Review | Adversarial review loop (max 2 iterations) catches ambiguity, security gaps, untestable "done when" criteria |
| Validate | Checks the spec against the Build Agent's schema before handoff |

### Build Agent (`agents/build_agent/`)

| Phase | What it does |
|-------|--------------|
| Scaffold | Creates an empty workflow and positions nodes left-to-right |
| Wire | Configures each node's parameters and connects them, translating spec shapes to n8n's (IF combinators, Set nested assignments, etc.) |
| Test | POSTs every test case from the spec, verifies responses |
| Audit | Runs deterministic checks: security, best practices, resilience |
| Harden | Fixes critical and warning findings, loops back to Audit (max 3) |
| Codify | Extracts reusable sub-workflows (deferred until 3+ builds) |
| Deploy | Activates the workflow, runs a smoke test |
| Export | Writes portable JSON + README to `workflows/live/` |

A status table prints after every phase — no silent skips.

## CLI reference

```bash
# PM Agent
python -m agents.pm_agent plan "<brief>"                       # interactive
python -m agents.pm_agent plan --from-brief brief.md           # non-interactive
python -m agents.pm_agent plan --from-brief brief.md --output specs/my.json

# Build Agent
python -m agents.build_agent build spec.json                   # full pipeline
python -m agents.build_agent build spec.json --dry-run         # validate only
python -m agents.build_agent build spec.json --no-export       # skip EXPORT
python -m agents.build_agent build spec.json --export-dir=out  # custom dir
python -m agents.build_agent validate spec.json                # parse + validate spec only
python -m agents.build_agent scaffold spec.json                # run SCAFFOLD only (debug)
python -m agents.build_agent list                              # list all deployed workflows
python -m agents.build_agent export <wf-id> spec.json          # re-export without rebuilding
```

## Tests

```bash
python -m pytest agents/build_agent/tests/ -v    # 237 tests
python -m pytest agents/pm_agent/tests/ -v       # 58 tests
```

Build Agent integration tests require a running n8n instance at `$N8N_BASE_URL` with a valid `$N8N_API_KEY`.

## Project layout

```
agents/
  pm_agent/           # Planning agent (requires anthropic SDK)
  build_agent/        # Building agent (Python stdlib only)
specs/                # Design docs for both agents
workflows/
  test-data/          # Sample briefs and specs for testing
  live/               # Auto-exported workflows after deploy
components/           # Reusable sub-workflows (populated by CODIFY)
```

## Design principles

- **Plan before you build.** No wiring nodes until the PM Agent has produced and verified a spec.
- **"Done When" must be testable.** Every step needs a concrete, verifiable completion criterion.
- **Audit loops, not audit passes.** AUDIT → HARDEN loops until clean, up to 3 iterations.
- **Mandatory status table.** Every phase appears in the output, done or skipped. No silent failures.
- **Litmus test for tests.** "If I break the node's actual functionality, will this test catch it?" If no, the test is fake.
- **Codify patterns.** Extract reusable sub-workflows after 3+ deployments.

See `specs/` for the full design rationale.

## Status

- ✅ PM Agent: implemented (58/58 tests passing, first live run 2026-04-15)
- ✅ Build Agent: implemented (237/237 tests passing), EXPORT phase + `list`/`export` CLI commands added 2026-04-17
- 🚧 Multi-branch wiring (3+ terminal `respondToWebhook` branches) — single biggest gap blocking complex PM specs
- 🚧 Credential resolution in WIRE — deferred, needed for any workflow using external APIs
- 🚧 CODIFY phase — deferred until 3+ workflows are deployed
- 🚧 Railway deployment of hosted n8n — planned

## License

(TBD)

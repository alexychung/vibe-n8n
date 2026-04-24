"""FastAPI wrapper around n8n + the PM/Build agents.

Serves a minimal frontend at `/`, plus:
  GET  /api/config                  — public config (n8n URL for browser links)
  GET  /api/workflows               — list workflows (+ webhook trigger info)
  POST /api/workflows/{id}/run      — fire a webhook-triggered workflow
  POST /api/plan                    — SSE stream of PM Agent output
  POST /api/stt                     — transcribe audio via OpenAI Whisper
  GET  /api/health                  — health check for Railway

Environment:
  N8N_BASE_URL        — where the backend talks to n8n (may be internal)
  N8N_PUBLIC_URL      — what the browser uses for deep-links (defaults to N8N_BASE_URL)
  N8N_API_KEY         — n8n API key
  ANTHROPIC_API_KEY   — for the PM Agent
  OPENAI_API_KEY      — for Whisper
"""
import asyncio
import base64
import datetime
import json
import os
import pathlib
import secrets
import sys
import tempfile
import uuid
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from web import auth, db

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC_DIR = pathlib.Path(__file__).resolve().parent / 'static'
LOG_PATH = PROJECT_ROOT / 'build-logs' / 'web-requests.jsonl'

# Expose pm_agent modules so we can reuse the LLM helpers for the interactive
# interview. pm_agent uses bare imports (`from llm import ...`) and expects its
# directory on sys.path.
sys.path.insert(0, str(PROJECT_ROOT / 'agents' / 'pm_agent'))


def load_env():
    """Load .env from project root if not already set."""
    env_path = PROJECT_ROOT / '.env'
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())


load_env()

N8N_BASE_URL = os.environ.get('N8N_BASE_URL', 'http://localhost:5678').rstrip('/')
N8N_PUBLIC_URL = os.environ.get('N8N_PUBLIC_URL', N8N_BASE_URL).rstrip('/')
N8N_API_KEY = os.environ.get('N8N_API_KEY', '')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')

# Shared-secret Basic Auth. If WEB_AUTH_PASSWORD is unset, the app is open
# (intended for local dev). Optional WEB_AUTH_USER defaults to "admin".
WEB_AUTH_PASSWORD = os.environ.get('WEB_AUTH_PASSWORD', '')
WEB_AUTH_USER = os.environ.get('WEB_AUTH_USER', 'admin')

SECURE_COOKIES = os.environ.get('COOKIE_SECURE', '').lower() in ('1', 'true', 'yes')

app = FastAPI(title='vibe-n8n')


@app.on_event('startup')
async def _startup():
    await db.init_pool()


@app.on_event('shutdown')
async def _shutdown():
    await db.close_pool()


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """HTTP Basic Auth gate. Used in single-user mode (no DATABASE_URL).

    Skipped entirely when multi-user mode is active — SessionMiddleware
    handles auth instead.
    """

    OPEN_PATHS = {'/api/health'}

    async def dispatch(self, request: Request, call_next):
        if db.is_enabled():
            return await call_next(request)
        if not WEB_AUTH_PASSWORD:
            return await call_next(request)
        if request.url.path in self.OPEN_PATHS:
            return await call_next(request)

        auth_header = request.headers.get('authorization', '')
        if auth_header.startswith('Basic '):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode('utf-8', errors='replace')
                user, _, pw = decoded.partition(':')
                if secrets.compare_digest(user, WEB_AUTH_USER) and secrets.compare_digest(pw, WEB_AUTH_PASSWORD):
                    return await call_next(request)
            except Exception:
                pass

        return Response(
            status_code=401,
            content='Authentication required',
            headers={'WWW-Authenticate': 'Basic realm="vibe-n8n"'},
        )


# Both middlewares are mounted; each is a no-op in the wrong mode.
app.add_middleware(auth.SessionMiddleware, secure_cookies=SECURE_COOKIES)
app.add_middleware(BasicAuthMiddleware)


def _log_request_sync(event: dict):
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open('a', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False) + '\n')
    except Exception:
        pass


def log_request(event: dict):
    """Fire-and-forget log write. Runs in a thread to avoid blocking the
    event loop; safe to call from sync code too (falls back to a direct
    write when no loop is running)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _log_request_sync(event)
        return
    loop.run_in_executor(None, _log_request_sync, event)


# ---------- n8n client ----------

async def n8n_get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f'{N8N_BASE_URL}{path}',
            headers={'X-N8N-API-KEY': N8N_API_KEY},
        )
        if r.status_code >= 400:
            raise HTTPException(r.status_code, f'n8n: {r.text}')
        return r.json()


def extract_webhook_infos(workflow: dict) -> list[dict]:
    """Return all webhook-trigger entries in the workflow.

    Each entry: {path, method, production_url, test_url, node_name}.
    Returns [] if the workflow has no webhook triggers.
    """
    out = []
    for node in workflow.get('nodes', []):
        if node.get('type') != 'n8n-nodes-base.webhook':
            continue
        params = node.get('parameters', {}) or {}
        path = params.get('path', '')
        if not path:
            continue
        method = (params.get('httpMethod') or 'POST').upper()
        out.append({
            'path': path,
            'method': method,
            'production_url': f'{N8N_PUBLIC_URL}/webhook/{path}',
            'test_url': f'{N8N_PUBLIC_URL}/webhook-test/{path}',
            'node_name': node.get('name', ''),
        })
    return out


def extract_webhook_info(workflow: dict) -> Optional[dict]:
    """Back-compat: return the first webhook only. Prefer extract_webhook_infos."""
    infos = extract_webhook_infos(workflow)
    return infos[0] if infos else None


# ---------- routes ----------

@app.get('/api/health')
def health():
    return {'ok': True, 'n8n_base_url': N8N_BASE_URL}


@app.get('/api/config')
def config():
    return {
        'n8n_public_url': N8N_PUBLIC_URL,
        'has_openai': bool(OPENAI_API_KEY),
        'has_anthropic': bool(os.environ.get('ANTHROPIC_API_KEY')),
        'multi_user': db.is_enabled(),
    }


# ---------- auth endpoints (multi-user mode) ----------

EMAIL_RE = __import__('re').compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


class AuthCredsRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=200)
    password: str = Field(..., min_length=8, max_length=200)


def _require_multi_user():
    if not db.is_enabled():
        raise HTTPException(503, 'multi-user mode disabled (DATABASE_URL unset)')


@app.post('/api/auth/signup')
async def signup(req: AuthCredsRequest, request: Request):
    _require_multi_user()
    email = req.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, 'invalid email')
    user = await auth.create_user(email, req.password)
    token = await auth.create_session(user['id'], request.headers.get('user-agent', ''))
    response = JSONResponse({'user': {'id': user['id'], 'email': user['email']}})
    auth.set_session_cookie(response, token, secure=SECURE_COOKIES)
    return response


@app.post('/api/auth/login')
async def login(req: AuthCredsRequest, request: Request):
    _require_multi_user()
    user = await auth.authenticate(req.email, req.password)
    if user is None:
        raise HTTPException(401, 'invalid email or password')
    token = await auth.create_session(user['id'], request.headers.get('user-agent', ''))
    response = JSONResponse({'user': {'id': user['id'], 'email': user['email']}})
    auth.set_session_cookie(response, token, secure=SECURE_COOKIES)
    return response


@app.post('/api/auth/logout')
async def logout(request: Request):
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        await auth.delete_session(token)
    response = JSONResponse({'ok': True})
    auth.clear_session_cookie(response, secure=SECURE_COOKIES)
    return response


@app.get('/api/me')
async def me(request: Request):
    """Returns {user: null} when not signed in (no 401)."""
    if not db.is_enabled():
        return {'user': None, 'multi_user': False}
    token = request.cookies.get(auth.SESSION_COOKIE)
    if not token:
        return {'user': None, 'multi_user': True}
    sess = await auth.lookup_session(token)
    if sess is None:
        return {'user': None, 'multi_user': True}
    return {'user': {'id': sess['user_id'], 'email': sess['email']}, 'multi_user': True}


@app.get('/api/workflows')
async def list_workflows():
    data = await n8n_get('/api/v1/workflows')
    workflows = data.get('data', [])
    out = []
    for w in workflows:
        webhooks = extract_webhook_infos(w)
        out.append({
            'id': w.get('id'),
            'name': w.get('name'),
            'active': bool(w.get('active')),
            'updated_at': w.get('updatedAt'),
            'webhook': webhooks[0] if webhooks else None,  # compat
            'webhooks': webhooks,
            'edit_url': f'{N8N_PUBLIC_URL}/workflow/{w.get("id")}',
        })
    return out


@app.get('/api/workflows/{workflow_id}')
async def get_workflow(workflow_id: str):
    w = await n8n_get(f'/api/v1/workflows/{workflow_id}')
    webhooks = extract_webhook_infos(w)
    return {
        'id': w.get('id'),
        'name': w.get('name'),
        'active': bool(w.get('active')),
        'webhook': webhooks[0] if webhooks else None,  # compat
        'webhooks': webhooks,
        'node_count': len(w.get('nodes', [])),
        'edit_url': f'{N8N_PUBLIC_URL}/workflow/{w.get("id")}',
    }


class RunRequest(BaseModel):
    body: Optional[dict] = None
    query: Optional[dict] = None
    headers: Optional[dict] = None
    mode: str = 'production'  # 'production' or 'test'
    webhook_index: int = 0   # which webhook to fire (when multiple)


@app.post('/api/workflows/{workflow_id}/run')
async def run_workflow(workflow_id: str, req: RunRequest):
    """Fire a webhook-triggered workflow. Only works for webhook-triggered workflows."""
    w = await n8n_get(f'/api/v1/workflows/{workflow_id}')
    webhooks = extract_webhook_infos(w)
    if not webhooks:
        raise HTTPException(400, 'Workflow has no webhook trigger — run it from the n8n UI instead.')
    if req.webhook_index < 0 or req.webhook_index >= len(webhooks):
        raise HTTPException(400, f'webhook_index {req.webhook_index} out of range (0..{len(webhooks)-1})')
    webhook = webhooks[req.webhook_index]
    if req.mode == 'production' and not w.get('active'):
        raise HTTPException(400, 'Workflow is not active. Activate it in n8n, or use mode=test.')

    url = webhook['production_url'] if req.mode == 'production' else webhook['test_url']
    method = webhook['method']
    headers = dict(req.headers or {})

    log_request({
        'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'kind': 'run_workflow',
        'workflow_id': workflow_id,
        'mode': req.mode,
        'method': method,
    })

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            if method == 'GET':
                r = await client.get(url, params=req.query or {}, headers=headers)
            else:
                r = await client.request(
                    method,
                    url,
                    json=req.body if req.body is not None else {},
                    params=req.query or {},
                    headers=headers,
                )
        except httpx.RequestError as e:
            raise HTTPException(502, f'Webhook call failed: {e}')

    content_type = r.headers.get('content-type', '')
    body: object
    if 'application/json' in content_type:
        try:
            body = r.json()
        except Exception:
            body = r.text
    else:
        body = r.text

    return {
        'status': r.status_code,
        'headers': dict(r.headers),
        'body': body,
    }


# ---------- workflow controls + executions ----------

async def n8n_request(method: str, path: str, body=None) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {'X-N8N-API-KEY': N8N_API_KEY}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        r = await client.request(method, f'{N8N_BASE_URL}{path}', headers=headers, json=body)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, f'n8n: {r.text}')
        if r.status_code == 204 or not r.content:
            return None
        return r.json()


@app.post('/api/workflows/{workflow_id}/activate')
async def activate_workflow(workflow_id: str):
    await n8n_request('POST', f'/api/v1/workflows/{workflow_id}/activate')
    log_request({
        'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'kind': 'activate', 'workflow_id': workflow_id,
    })
    return {'ok': True, 'active': True}


@app.post('/api/workflows/{workflow_id}/deactivate')
async def deactivate_workflow(workflow_id: str):
    await n8n_request('POST', f'/api/v1/workflows/{workflow_id}/deactivate')
    log_request({
        'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'kind': 'deactivate', 'workflow_id': workflow_id,
    })
    return {'ok': True, 'active': False}


@app.delete('/api/workflows/{workflow_id}')
async def delete_workflow(workflow_id: str):
    await n8n_request('DELETE', f'/api/v1/workflows/{workflow_id}')
    log_request({
        'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'kind': 'delete', 'workflow_id': workflow_id,
    })
    return {'ok': True}


@app.get('/api/workflows/{workflow_id}/executions')
async def list_executions(workflow_id: str, limit: int = 20, include_data: bool = False):
    limit = max(1, min(limit, 100))
    qs = f'?workflowId={workflow_id}&limit={limit}'
    if include_data:
        qs += '&includeData=true'
    data = await n8n_get(f'/api/v1/executions{qs}')
    executions = data.get('data', []) if isinstance(data, dict) else []
    out = []
    for e in executions:
        err = None
        if include_data:
            rd = (e.get('data') or {}).get('resultData') or {}
            if isinstance(rd.get('error'), dict):
                err_obj = rd['error']
                err = {
                    'node': (err_obj.get('node') or {}).get('name'),
                    'message': err_obj.get('message'),
                    'type': err_obj.get('name'),
                }
        # derive status — newer n8n returns `status`, older ones `finished` + `stoppedAt`
        status = e.get('status')
        if not status:
            if not e.get('finished') and e.get('stoppedAt'):
                status = 'error'
            elif e.get('finished'):
                status = 'success'
            else:
                status = 'running'
        out.append({
            'id': e.get('id'),
            'mode': e.get('mode'),
            'started_at': e.get('startedAt'),
            'stopped_at': e.get('stoppedAt'),
            'status': status,
            'error': err,
        })
    return out


# ---------- past specs ----------

SPEC_DIRS = [
    ('workflows/test-data', '*-spec.json', 'spec'),
    ('workflows/live', '**/*.json', 'live'),
]


@app.get('/api/specs')
async def list_specs():
    """List JSON specs (rebuildable) and live workflow exports (read-only)."""
    out = []
    for rel_dir, pattern, kind in SPEC_DIRS:
        root = PROJECT_ROOT / rel_dir
        if not root.exists():
            continue
        for p in root.glob(pattern):
            if not p.is_file():
                continue
            name = None
            try:
                head = p.read_text(encoding='utf-8', errors='replace')[:2048]
                # PM-Agent specs use "workflow_name"; live n8n exports use "name"
                for key in ('"workflow_name"', '"name"'):
                    idx = head.find(key)
                    if idx == -1:
                        continue
                    colon = head.find(':', idx)
                    q1 = head.find('"', colon + 1)
                    q2 = head.find('"', q1 + 1) if q1 != -1 else -1
                    if q1 != -1 and q2 != -1:
                        name = head[q1 + 1:q2]
                        break
            except Exception:
                pass
            rel = str(p.relative_to(PROJECT_ROOT)).replace('\\', '/')
            try:
                st = p.stat()
            except OSError:
                continue
            out.append({
                'path': rel,
                'name': name or p.stem,
                'size': st.st_size,
                'mtime': st.st_mtime,
                'kind': kind,
            })
    out.sort(key=lambda s: s['mtime'], reverse=True)
    return out


@app.get('/api/specs/content')
async def spec_content(path: str):
    sp = _safe_project_path(path)
    if not sp.exists() or not sp.is_file():
        raise HTTPException(404, 'spec not found')
    try:
        return json.loads(sp.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        raise HTTPException(400, f'invalid JSON: {e}')


# ---------- SSE helpers ----------

def _sse(kind: str, data) -> bytes:
    payload = data if isinstance(data, str) else json.dumps(data)
    return f'event: {kind}\ndata: {payload}\n\n'.encode('utf-8')


async def _stream_subprocess(cmd: list[str]):
    """Yield SSE 'log' lines from a subprocess's merged stdout/stderr, ending
    with a synthetic ('_exit', returncode) tuple. On client disconnect the
    subprocess is terminated so it doesn't keep running — important for LLM
    calls that cost money.
    """
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUNBUFFERED'] = '1'

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode('utf-8', errors='replace').rstrip('\r\n')
            yield ('log', {'line': text})
        rc = await proc.wait()
        yield ('_exit', rc)
    finally:
        # Handles both normal completion (no-op) and client disconnect.
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await proc.wait()
                except Exception:
                    pass


def _safe_project_path(rel: str) -> pathlib.Path:
    """Resolve a user-supplied project-relative path, reject traversal."""
    candidate = (PROJECT_ROOT / rel).resolve()
    try:
        candidate.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        raise HTTPException(400, f'Path escapes project root: {rel}')
    return candidate


# ---------- PM Agent streaming ----------

class PlanRequest(BaseModel):
    brief: Optional[str] = None
    requirements_path: Optional[str] = None


@app.post('/api/plan')
async def plan(req: PlanRequest):
    """Run PM Agent on the provided brief OR pre-computed requirements JSON.

    Streams stdout lines as SSE events. Final `done` event carries the spec.
    """
    session_id = uuid.uuid4().hex[:12]
    spec_path = PROJECT_ROOT / 'workflows' / 'test-data' / f'web-{session_id}-spec.json'
    spec_path.parent.mkdir(parents=True, exist_ok=True)

    if req.requirements_path:
        rp = _safe_project_path(req.requirements_path)
        if not rp.exists():
            raise HTTPException(400, f'requirements file not found: {req.requirements_path}')
        cmd = [
            sys.executable, '-m', 'agents.pm_agent', 'plan',
            '--requirements', str(rp),
            '--output', str(spec_path),
        ]
        input_kind = 'requirements'
    elif req.brief and req.brief.strip():
        brief = req.brief.strip()
        brief_path = PROJECT_ROOT / 'build-logs' / f'web-brief-{session_id}.md'
        brief_path.parent.mkdir(parents=True, exist_ok=True)
        brief_path.write_text(brief, encoding='utf-8')
        cmd = [
            sys.executable, '-m', 'agents.pm_agent', 'plan',
            '--from-brief', str(brief_path),
            '--output', str(spec_path),
        ]
        input_kind = 'brief'
    else:
        raise HTTPException(400, 'brief or requirements_path is required')

    log_request({
        'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'kind': 'plan',
        'session_id': session_id,
        'input_kind': input_kind,
    })

    async def event_stream():
        yield _sse('session', {'session_id': session_id})
        rc = None
        async for kind, payload in _stream_subprocess(cmd):
            if kind == '_exit':
                rc = payload
            else:
                yield _sse(kind, payload)

        if rc == 0 and spec_path.exists():
            try:
                spec = json.loads(spec_path.read_text(encoding='utf-8'))
            except Exception as e:
                yield _sse('error', {'message': f'Failed to read spec: {e}'})
                return
            yield _sse('done', {
                'exit_code': rc,
                'spec_path': str(spec_path.relative_to(PROJECT_ROOT)).replace('\\', '/'),
                'spec': spec,
            })
        else:
            yield _sse('done', {'exit_code': rc, 'spec_path': None})

    return StreamingResponse(
        event_stream(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ---------- Build Agent streaming ----------

class BuildRequest(BaseModel):
    spec_path: str
    dry_run: bool = False


WORKFLOW_ID_RE = None  # compiled lazily


@app.post('/api/build')
async def build(req: BuildRequest):
    """Run Build Agent on a spec file. Streams stdout as SSE. Final event includes workflow_id."""
    import re
    global WORKFLOW_ID_RE
    if WORKFLOW_ID_RE is None:
        WORKFLOW_ID_RE = re.compile(r'Workflow deployed:\s*(\S+)')

    sp = _safe_project_path(req.spec_path)
    if not sp.exists():
        raise HTTPException(400, f'spec not found: {req.spec_path}')

    cmd = [sys.executable, '-m', 'agents.build_agent', 'build', str(sp)]
    if req.dry_run:
        cmd.append('--dry-run')

    log_request({
        'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'kind': 'build',
        'spec_path': req.spec_path,
        'dry_run': req.dry_run,
    })

    async def event_stream():
        workflow_id = None
        rc = None
        async for kind, payload in _stream_subprocess(cmd):
            if kind == '_exit':
                rc = payload
            else:
                yield _sse(kind, payload)
                if workflow_id is None:
                    m = WORKFLOW_ID_RE.search(payload.get('line', ''))
                    if m:
                        workflow_id = m.group(1)

        edit_url = f'{N8N_PUBLIC_URL}/workflow/{workflow_id}' if workflow_id else None
        yield _sse('done', {
            'exit_code': rc,
            'workflow_id': workflow_id,
            'edit_url': edit_url,
        })

    return StreamingResponse(
        event_stream(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ---------- Interactive interview ----------

INTERVIEW_MODEL = 'claude-haiku-4-5-20251001'


class InterviewStartRequest(BaseModel):
    description: str


class InterviewFinishRequest(BaseModel):
    description: str
    inferred: dict
    answers: dict  # {"q1": "...", "q2": "..."}


def _import_pm_llm():
    try:
        from llm import call_json, load_prompt  # type: ignore[import-not-found]
    except ImportError as e:
        raise HTTPException(500, f'pm_agent llm import failed: {e}')
    return call_json, load_prompt


@app.post('/api/interview/start')
async def interview_start(req: InterviewStartRequest):
    """First step: LLM infers what it can, returns remaining questions."""
    if not req.description.strip():
        raise HTTPException(400, 'description is required')
    call_json, load_prompt = _import_pm_llm()

    def _call():
        system = load_prompt('interview')
        return call_json(INTERVIEW_MODEL, system, req.description)

    try:
        result = await asyncio.to_thread(_call)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except FileNotFoundError as e:
        raise HTTPException(500, f'prompt template missing: {e}')

    log_request({
        'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'kind': 'interview_start',
        'description_len': len(req.description),
        'question_count': len(result.get('questions_to_ask', [])),
    })
    return {
        'inferred': result.get('inferred', {}),
        'questions': result.get('questions_to_ask', []),
    }


@app.post('/api/interview/finish')
async def interview_finish(req: InterviewFinishRequest):
    """Second step: consolidate inferred + answers into a requirements JSON file."""
    call_json, load_prompt = _import_pm_llm()

    def _call():
        system = load_prompt('interview')
        consolidate_prompt = (
            f'Original description: {req.description}\n\n'
            f'Inferred answers: {json.dumps(req.inferred)}\n\n'
            f'User answers to follow-up questions: {json.dumps(req.answers)}\n\n'
            'Produce the final structured requirements as JSON with these fields:\n'
            'outcome, trigger, stakes, success_criteria, systems, volume, budget, editors\n'
            "All fields should be filled in. Use inferred values where the user confirmed or didn't contradict."
        )
        return call_json(INTERVIEW_MODEL, system, consolidate_prompt)

    try:
        requirements = await asyncio.to_thread(_call)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except FileNotFoundError as e:
        raise HTTPException(500, f'prompt template missing: {e}')

    session_id = uuid.uuid4().hex[:12]
    req_path = PROJECT_ROOT / 'build-logs' / f'web-requirements-{session_id}.json'
    req_path.parent.mkdir(parents=True, exist_ok=True)
    req_path.write_text(json.dumps(requirements, indent=2), encoding='utf-8')

    log_request({
        'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'kind': 'interview_finish',
        'session_id': session_id,
        'requirements_path': str(req_path.relative_to(PROJECT_ROOT)).replace('\\', '/'),
    })
    return {
        'requirements': requirements,
        'requirements_path': str(req_path.relative_to(PROJECT_ROOT)).replace('\\', '/'),
        'session_id': session_id,
    }


# ---------- Whisper STT ----------

@app.post('/api/stt')
async def stt(audio: UploadFile = File(...)):
    if not OPENAI_API_KEY:
        raise HTTPException(503, 'OPENAI_API_KEY not set')
    contents = await audio.read()
    if len(contents) == 0:
        raise HTTPException(400, 'empty audio')

    suffix = pathlib.Path(audio.filename or '').suffix or '.webm'
    filename = f'audio{suffix}'

    async with httpx.AsyncClient(timeout=120) as client:
        files = {
            'file': (filename, contents, audio.content_type or 'application/octet-stream'),
            'model': (None, 'whisper-1'),
        }
        r = await client.post(
            'https://api.openai.com/v1/audio/transcriptions',
            headers={'Authorization': f'Bearer {OPENAI_API_KEY}'},
            files=files,
        )
    if r.status_code >= 400:
        raise HTTPException(r.status_code, f'whisper: {r.text}')
    data = r.json()
    transcript = data.get('text', '')
    log_request({
        'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'kind': 'stt',
        'bytes': len(contents),
        'transcript_len': len(transcript),
    })
    return {'text': transcript}


# ---------- static ----------

if STATIC_DIR.exists():
    app.mount('/static', StaticFiles(directory=str(STATIC_DIR)), name='static')


@app.get('/')
def index():
    index_path = STATIC_DIR / 'index.html'
    if not index_path.exists():
        return JSONResponse({'error': 'index.html not found'}, status_code=500)
    return FileResponse(str(index_path))


@app.get('/login')
def login_page():
    p = STATIC_DIR / 'login.html'
    if not p.exists():
        return JSONResponse({'error': 'login.html not found'}, status_code=500)
    return FileResponse(str(p))


@app.get('/signup')
def signup_page():
    p = STATIC_DIR / 'signup.html'
    if not p.exists():
        return JSONResponse({'error': 'signup.html not found'}, status_code=500)
    return FileResponse(str(p))

"""Two-user isolation tests for multi-user mode.

Skipped unless TEST_DATABASE_URL is set. Use a Neon branch DB for CI so the
test suite resets state cleanly between runs.

Run:
    TEST_DATABASE_URL=postgres://... python -m pytest web/tests/ -v
"""
import os
import pytest
import asyncio
import json
import uuid

pytest.importorskip('asyncpg')
pytest.importorskip('argon2')
import asyncpg

TEST_DSN = os.environ.get('TEST_DATABASE_URL', '')


def _run_async(coro):
    """Run a coroutine in a fresh event loop.

    Used by tests that need to do DB work outside the TestClient's loop.
    Each call connects fresh — never touches the asyncpg pool, which is
    pinned to whatever loop TestClient created it on.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _db_lookup_user_id(email: str) -> str:
    conn = await asyncpg.connect(TEST_DSN, statement_cache_size=0)
    try:
        row = await conn.fetchrow(
            'SELECT id FROM users WHERE email = $1', email.lower()
        )
        return str(row['id'])
    finally:
        await conn.close()


async def _db_insert_spec(user_id: str, spec: dict) -> str:
    conn = await asyncpg.connect(TEST_DSN, statement_cache_size=0)
    try:
        row = await conn.fetchrow(
            '''INSERT INTO specs (user_id, workflow_name, spec_json)
               VALUES ($1, $2, $3::jsonb) RETURNING id''',
            user_id, spec.get('workflow_name'), json.dumps(spec),
        )
        return str(row['id'])
    finally:
        await conn.close()


async def _db_claim_workflow(user_id: str, n8n_workflow_id: str):
    conn = await asyncpg.connect(TEST_DSN, statement_cache_size=0)
    try:
        await conn.execute(
            '''INSERT INTO workflow_owners (n8n_workflow_id, user_id)
               VALUES ($1, $2)
               ON CONFLICT (n8n_workflow_id) DO NOTHING''',
            n8n_workflow_id, user_id,
        )
    finally:
        await conn.close()


@pytest.fixture(scope='module')
def test_db():
    if not TEST_DSN:
        pytest.skip('TEST_DATABASE_URL not set')
    # Set DATABASE_URL so the app picks up multi-user mode
    os.environ['DATABASE_URL'] = TEST_DSN
    yield TEST_DSN


@pytest.fixture(scope='module')
def client(test_db):
    """A FastAPI TestClient with multi-user mode enabled."""
    from fastapi.testclient import TestClient
    # Import lazily so DATABASE_URL is set first
    from web.app import app
    from web import db

    # Drive startup/shutdown manually so the pool initializes
    with TestClient(app) as c:
        yield c


def _signup(client, email: str, password: str = 'testpass123'):
    r = client.post('/api/auth/signup', json={'email': email, 'password': password})
    assert r.status_code == 200, r.text
    # The TestClient persists cookies on the client object; capture the session
    # cookie and return it so callers can detach if they need a separate user.
    return r.cookies.get('vibe_session')


def _login(client, email: str, password: str = 'testpass123'):
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.text
    return r.cookies.get('vibe_session')


@pytest.mark.asyncio
async def test_signup_and_login_round_trip(client):
    email = f'rt_{uuid.uuid4().hex[:8]}@test.local'
    cookie = _signup(client, email)
    assert cookie

    # /api/me returns the user
    r = client.get('/api/me')
    assert r.status_code == 200
    data = r.json()
    assert data['user']['email'] == email
    assert data['multi_user'] is True

    # logout clears the session
    r = client.post('/api/auth/logout')
    assert r.status_code == 200
    r = client.get('/api/me')
    assert r.json()['user'] is None

    # log back in
    cookie2 = _login(client, email)
    assert cookie2 and cookie2 != cookie  # new session token


def test_bad_password_rejected(client):
    email = f'bad_{uuid.uuid4().hex[:8]}@test.local'
    _signup(client, email)
    client.post('/api/auth/logout')
    r = client.post('/api/auth/login', json={'email': email, 'password': 'wrongpassword'})
    assert r.status_code == 401


def test_signup_duplicate_email_rejected(client):
    email = f'dup_{uuid.uuid4().hex[:8]}@test.local'
    _signup(client, email)
    client.post('/api/auth/logout')
    r = client.post('/api/auth/signup', json={'email': email, 'password': 'testpass123'})
    assert r.status_code == 409


def test_unauth_api_returns_401(client):
    client.post('/api/auth/logout')
    r = client.get('/api/workflows')
    assert r.status_code == 401


def _signup_get_cookie(client, email: str) -> str:
    """Sign up and return the captured session cookie. Clears the client's
    persistent cookie jar after so the next signup doesn't auth as this user.

    Don't call /api/auth/logout — that *deletes* the session row, which would
    invalidate the cookie we want to reuse.
    """
    r = client.post('/api/auth/signup', json={'email': email, 'password': 'testpass123'})
    assert r.status_code == 200, r.text
    cookie = r.cookies.get('vibe_session')
    assert cookie
    client.cookies.clear()
    return cookie


def test_two_user_spec_isolation(client):
    """User A's specs are invisible to user B."""
    email_a = f'a_{uuid.uuid4().hex[:8]}@test.local'
    email_b = f'b_{uuid.uuid4().hex[:8]}@test.local'

    cookie_a = _signup_get_cookie(client, email_a)
    cookie_b = _signup_get_cookie(client, email_b)

    # User A inserts a spec via direct SQL (avoids running the PM Agent,
    # which costs API credits, and avoids the asyncpg pool — that pool is
    # pinned to TestClient's event loop).
    user_a = _run_async(_db_lookup_user_id(email_a))
    spec_id = _run_async(_db_insert_spec(user_a, {'workflow_name': 'A-only'}))

    # User A sees it
    client.cookies.clear()
    client.cookies.set('vibe_session', cookie_a)
    ra = client.get('/api/specs')
    assert ra.status_code == 200
    assert 'A-only' in [s['name'] for s in ra.json()]

    # User B does NOT
    client.cookies.clear()
    client.cookies.set('vibe_session', cookie_b)
    rb = client.get('/api/specs')
    assert rb.status_code == 200
    assert 'A-only' not in [s['name'] for s in rb.json()]

    # User B can't fetch the spec content by id either
    rb2 = client.get(f'/api/specs/content?id={spec_id}')
    assert rb2.status_code == 404


def test_workflow_ownership_filters_listing(client):
    """User A's claimed n8n workflow ID is hidden from user B's /api/workflows."""
    email_a = f'wfa_{uuid.uuid4().hex[:8]}@test.local'
    email_b = f'wfb_{uuid.uuid4().hex[:8]}@test.local'
    fake_wf = f'fake_wf_{uuid.uuid4().hex[:6]}'

    _signup_get_cookie(client, email_a)
    cookie_b = _signup_get_cookie(client, email_b)

    user_a = _run_async(_db_lookup_user_id(email_a))
    _run_async(_db_claim_workflow(user_a, fake_wf))

    client.cookies.clear()
    client.cookies.set('vibe_session', cookie_b)

    # /api/workflows hits the live n8n; even if the ID isn't there, the
    # filter should not raise and B should see no entry with this ID.
    rb = client.get('/api/workflows')
    assert rb.status_code in (200, 502)  # 502 if n8n unreachable in CI
    if rb.status_code == 200:
        ids = [w['id'] for w in rb.json()]
        assert fake_wf not in ids

    # Direct ownership check: B can't activate A's workflow
    r = client.post(f'/api/workflows/{fake_wf}/activate')
    assert r.status_code == 404

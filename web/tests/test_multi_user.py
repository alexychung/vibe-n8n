"""Two-user isolation tests for multi-user mode.

Skipped unless TEST_DATABASE_URL is set. Use a Neon branch DB for CI so the
test suite resets state cleanly between runs.

Run:
    TEST_DATABASE_URL=postgres://... python -m pytest web/tests/ -v
"""
import os
import pytest
import asyncio
import uuid

pytest.importorskip('asyncpg')
pytest.importorskip('argon2')

TEST_DSN = os.environ.get('TEST_DATABASE_URL', '')


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


def test_two_user_spec_isolation(client):
    """User A's specs are invisible to user B."""
    from fastapi.testclient import TestClient
    from web.app import app

    email_a = f'a_{uuid.uuid4().hex[:8]}@test.local'
    email_b = f'b_{uuid.uuid4().hex[:8]}@test.local'

    # Use independent client objects so cookies don't leak
    with TestClient(app) as ca, TestClient(app) as cb:
        ca.post('/api/auth/signup', json={'email': email_a, 'password': 'testpass123'})
        cb.post('/api/auth/signup', json={'email': email_b, 'password': 'testpass123'})

        # User A inserts a spec via the storage layer (avoids running the full
        # PM Agent, which costs API credits in tests).
        # We POST to /api/plan only if you want a true e2e test; here we
        # exercise the access-control path more cheaply via direct DB write.
        from web import db, storage
        loop = asyncio.new_event_loop()
        try:
            user_a = loop.run_until_complete(_lookup_user(email_a))
            spec_id = loop.run_until_complete(
                storage.save_spec(user_a, {'workflow_name': 'A-only'})
            )
        finally:
            loop.close()

        # User A sees it
        ra = ca.get('/api/specs')
        assert ra.status_code == 200
        names = [s['name'] for s in ra.json()]
        assert 'A-only' in names

        # User B does NOT
        rb = cb.get('/api/specs')
        assert rb.status_code == 200
        names_b = [s['name'] for s in rb.json()]
        assert 'A-only' not in names_b

        # User B can't fetch the spec content by id either
        rb2 = cb.get(f'/api/specs/content?id={spec_id}')
        assert rb2.status_code == 404


async def _lookup_user(email: str) -> str:
    from web import db
    pool = db.get_pool()
    row = await pool.fetchrow('SELECT id FROM users WHERE email = $1', email.lower())
    return str(row['id'])


def test_workflow_ownership_filters_listing(client):
    """User A's claimed n8n workflow ID is hidden from user B's /api/workflows."""
    from fastapi.testclient import TestClient
    from web.app import app
    from web import storage

    email_a = f'wfa_{uuid.uuid4().hex[:8]}@test.local'
    email_b = f'wfb_{uuid.uuid4().hex[:8]}@test.local'
    fake_wf = f'fake_wf_{uuid.uuid4().hex[:6]}'

    with TestClient(app) as ca, TestClient(app) as cb:
        ca.post('/api/auth/signup', json={'email': email_a, 'password': 'testpass123'})
        cb.post('/api/auth/signup', json={'email': email_b, 'password': 'testpass123'})

        loop = asyncio.new_event_loop()
        try:
            user_a = loop.run_until_complete(_lookup_user(email_a))
            loop.run_until_complete(storage.claim_workflow(user_a, fake_wf))
        finally:
            loop.close()

        # /api/workflows hits the live n8n; even if the ID isn't there, the
        # filter should not raise and B should see no entry with this ID.
        rb = cb.get('/api/workflows')
        assert rb.status_code in (200, 502)  # 502 if n8n unreachable in CI
        if rb.status_code == 200:
            ids = [w['id'] for w in rb.json()]
            assert fake_wf not in ids

        # Direct ownership check: B can't activate A's workflow
        r = cb.post(f'/api/workflows/{fake_wf}/activate')
        assert r.status_code == 404

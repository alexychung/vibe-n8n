"""Authentication: argon2 password hashing + cookie-based sessions.

Active only when DATABASE_URL is set (see web.db.is_enabled()). Otherwise the
app stays in its current single-user mode using BasicAuthMiddleware (or open).
"""
import asyncio
import datetime
import hashlib
import secrets
from typing import Optional

from fastapi import HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError
    _hasher = PasswordHasher()
except ImportError:
    _hasher = None  # type: ignore[assignment]
    VerifyMismatchError = Exception  # type: ignore[assignment,misc]

from . import db

SESSION_COOKIE = 'vibe_session'
SESSION_LIFETIME = datetime.timedelta(days=30)
SESSION_IDLE_LIMIT = datetime.timedelta(days=7)
PUBLIC_PATHS = {
    '/api/health',
    '/api/auth/signup',
    '/api/auth/login',
    '/api/auth/logout',
    '/api/me',  # returns 200 {user: null} when no session
    '/login',
    '/signup',
}


def hash_password(plain: str) -> str:
    if _hasher is None:
        raise RuntimeError('argon2-cffi not installed')
    return _hasher.hash(plain)


def verify_password(hashed: str, plain: str) -> bool:
    if _hasher is None:
        return False
    try:
        _hasher.verify(hashed, plain)
        return True
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def _hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode('utf-8')).digest()


async def create_user(email: str, password: str) -> dict:
    pool = db.get_pool()
    assert pool is not None, 'DATABASE_URL not configured'
    pw_hash = hash_password(password)
    try:
        row = await pool.fetchrow(
            'INSERT INTO users (email, pw_hash) VALUES ($1, $2) '
            'RETURNING id, email, created_at',
            email.strip().lower(), pw_hash,
        )
    except Exception as e:
        # asyncpg.UniqueViolationError → 409
        msg = str(e).lower()
        if 'unique' in msg or 'duplicate' in msg:
            raise HTTPException(409, 'email already registered')
        raise HTTPException(400, str(e))
    return {'id': str(row['id']), 'email': row['email'], 'created_at': row['created_at'].isoformat()}


async def authenticate(email: str, password: str) -> Optional[dict]:
    """Constant-ish-time. Returns user dict on success, None on bad creds."""
    pool = db.get_pool()
    assert pool is not None
    row = await pool.fetchrow(
        'SELECT id, email, pw_hash FROM users WHERE email = $1',
        email.strip().lower(),
    )
    # Always run a hash even if the user doesn't exist, to flatten timing.
    if row is None:
        if _hasher is not None:
            try:
                _hasher.hash(password)  # discarded
            except Exception:
                pass
        return None
    if not verify_password(row['pw_hash'], password):
        return None
    await pool.execute('UPDATE users SET last_login = now() WHERE id = $1', row['id'])
    return {'id': str(row['id']), 'email': row['email']}


async def create_session(user_id: str, user_agent: str = '') -> str:
    """Mint a new session token for the user. Returns the raw token (set as cookie)."""
    pool = db.get_pool()
    assert pool is not None
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    expires_at = datetime.datetime.now(datetime.timezone.utc) + SESSION_LIFETIME
    await pool.execute(
        'INSERT INTO sessions (token_hash, user_id, expires_at, user_agent) '
        'VALUES ($1, $2, $3, $4)',
        token_hash, user_id, expires_at, user_agent[:500] if user_agent else None,
    )
    return token


async def lookup_session(token: str) -> Optional[dict]:
    """Returns {user_id, email} if token is valid + not expired/idle. Updates last_used."""
    pool = db.get_pool()
    if pool is None:
        return None
    token_hash = _hash_token(token)
    row = await pool.fetchrow(
        '''SELECT s.user_id, s.expires_at, s.last_used, u.email
           FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = $1''',
        token_hash,
    )
    if row is None:
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    if row['expires_at'] <= now:
        await pool.execute('DELETE FROM sessions WHERE token_hash = $1', token_hash)
        return None
    if (now - row['last_used']) > SESSION_IDLE_LIMIT:
        await pool.execute('DELETE FROM sessions WHERE token_hash = $1', token_hash)
        return None
    # Fire-and-forget last_used update
    asyncio.create_task(_touch_session(token_hash))
    return {'user_id': str(row['user_id']), 'email': row['email']}


async def _touch_session(token_hash: bytes):
    pool = db.get_pool()
    if pool is None:
        return
    try:
        await pool.execute(
            'UPDATE sessions SET last_used = now() WHERE token_hash = $1',
            token_hash,
        )
    except Exception:
        pass


async def delete_session(token: str):
    pool = db.get_pool()
    if pool is None:
        return
    await pool.execute('DELETE FROM sessions WHERE token_hash = $1', _hash_token(token))


def set_session_cookie(response: Response, token: str, secure: bool):
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        secure=secure,
        samesite='lax',
        path='/',
    )


def clear_session_cookie(response: Response, secure: bool):
    response.set_cookie(
        SESSION_COOKIE, '', max_age=0,
        httponly=True, secure=secure, samesite='lax', path='/',
    )


class SessionMiddleware(BaseHTTPMiddleware):
    """Rejects requests without a valid session cookie. Public paths bypass.

    Sets request.state.user_id + user_email when authenticated.
    """

    def __init__(self, app, secure_cookies: bool = False):
        super().__init__(app)
        self.secure_cookies = secure_cookies

    async def dispatch(self, request: Request, call_next):
        if not db.is_enabled():
            return await call_next(request)

        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith('/static/'):
            return await call_next(request)

        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return _unauth(request)
        sess = await lookup_session(token)
        if sess is None:
            return _unauth(request)
        request.state.user_id = sess['user_id']
        request.state.user_email = sess['email']
        return await call_next(request)


def _unauth(request: Request) -> Response:
    """Redirect HTML requests to /login, return 401 JSON for API."""
    if request.url.path.startswith('/api/'):
        return Response(
            status_code=401,
            content='{"detail":"not authenticated"}',
            media_type='application/json',
        )
    return Response(
        status_code=302,
        headers={'Location': '/login'},
    )


def get_user(request: Request) -> dict:
    """Dependency: returns {user_id, email}. Requires SessionMiddleware to have run."""
    user_id = getattr(request.state, 'user_id', None)
    if user_id is None:
        raise HTTPException(401, 'not authenticated')
    return {'user_id': user_id, 'email': request.state.user_email}

"""Postgres connection pool + migration runner.

Multi-user mode is gated on DATABASE_URL. When unset, get_pool() returns None
and the rest of the app falls back to single-user behavior.
"""
import logging
import os
import pathlib
from typing import Optional

try:
    import asyncpg
except ImportError:
    asyncpg = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

_pool: Optional['asyncpg.Pool'] = None
MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent / 'migrations'


def is_enabled() -> bool:
    """True iff multi-user mode should be active."""
    return bool(os.environ.get('DATABASE_URL')) and asyncpg is not None


async def init_pool() -> Optional['asyncpg.Pool']:
    """Create the global pool and run migrations. Idempotent.

    Returns the pool, or None if DATABASE_URL is unset.
    """
    global _pool
    if _pool is not None:
        return _pool
    dsn = os.environ.get('DATABASE_URL')
    if not dsn:
        log.info('DATABASE_URL unset — running in single-user mode')
        return None
    if asyncpg is None:
        log.warning('asyncpg not installed — multi-user mode disabled')
        return None
    _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10, statement_cache_size=0)
    await _run_migrations(_pool)
    return _pool


async def close_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> Optional['asyncpg.Pool']:
    return _pool


async def _run_migrations(pool: 'asyncpg.Pool'):
    """Run any *.sql files in migrations/ that haven't been applied yet.

    Tracks applied migrations in a `schema_migrations` table — name only, no
    checksum, since we never edit historical migrations in place.
    """
    async with pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS schema_migrations (
              filename TEXT PRIMARY KEY,
              applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        ''')
        applied = {
            r['filename'] for r in
            await conn.fetch('SELECT filename FROM schema_migrations')
        }
        files = sorted(MIGRATIONS_DIR.glob('*.sql'))
        for f in files:
            if f.name in applied:
                continue
            log.info(f'Applying migration {f.name}')
            sql = f.read_text(encoding='utf-8')
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    'INSERT INTO schema_migrations (filename) VALUES ($1)',
                    f.name,
                )

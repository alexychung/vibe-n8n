"""DB-backed storage for specs, builds, and workflow ownership.

Only used when web.db.is_enabled() — in single-user mode the app falls back
to filesystem-based spec storage and skips ownership filtering.
"""
import datetime
import json
from typing import Optional

from . import db


# ---------- specs ----------

async def save_spec(
    user_id: str,
    spec: dict,
    brief_text: Optional[str] = None,
    requirements: Optional[dict] = None,
) -> str:
    """Insert a new spec for `user_id`, return the spec_id."""
    pool = db.get_pool()
    assert pool is not None
    row = await pool.fetchrow(
        '''INSERT INTO specs (user_id, workflow_name, spec_json, brief_text, requirements)
           VALUES ($1, $2, $3::jsonb, $4, $5::jsonb)
           RETURNING id''',
        user_id,
        spec.get('workflow_name'),
        json.dumps(spec),
        brief_text,
        json.dumps(requirements) if requirements is not None else None,
    )
    return str(row['id'])


async def get_spec(spec_id: str, user_id: str) -> Optional[dict]:
    """Return {id, workflow_name, spec_json, created_at} if owned by user, else None."""
    pool = db.get_pool()
    assert pool is not None
    try:
        row = await pool.fetchrow(
            '''SELECT id, workflow_name, spec_json, created_at
               FROM specs WHERE id = $1 AND user_id = $2''',
            spec_id, user_id,
        )
    except Exception:
        # malformed UUID etc.
        return None
    if row is None:
        return None
    spec_json = row['spec_json']
    if isinstance(spec_json, str):
        spec_json = json.loads(spec_json)
    return {
        'id': str(row['id']),
        'workflow_name': row['workflow_name'],
        'spec': spec_json,
        'created_at': row['created_at'].isoformat(),
    }


async def list_specs(user_id: str) -> list[dict]:
    pool = db.get_pool()
    assert pool is not None
    rows = await pool.fetch(
        '''SELECT id, workflow_name, created_at, octet_length(spec_json::text) AS size
           FROM specs WHERE user_id = $1
           ORDER BY created_at DESC''',
        user_id,
    )
    out = []
    for r in rows:
        out.append({
            'id': str(r['id']),
            'name': r['workflow_name'] or '(unnamed)',
            'mtime': r['created_at'].timestamp(),
            'size': int(r['size']),
            'kind': 'spec',
        })
    return out


# ---------- builds ----------

async def start_build(user_id: str, spec_id: Optional[str]) -> str:
    pool = db.get_pool()
    assert pool is not None
    row = await pool.fetchrow(
        '''INSERT INTO builds (user_id, spec_id, status)
           VALUES ($1, $2, 'running') RETURNING id''',
        user_id, spec_id,
    )
    return str(row['id'])


async def finish_build(
    build_id: str,
    *,
    status: str,
    exit_code: Optional[int],
    n8n_workflow_id: Optional[str],
    log: str,
):
    pool = db.get_pool()
    assert pool is not None
    await pool.execute(
        '''UPDATE builds
           SET status = $2, exit_code = $3, n8n_workflow_id = $4, log = $5,
               finished_at = $6
           WHERE id = $1''',
        build_id, status, exit_code, n8n_workflow_id, log,
        datetime.datetime.now(datetime.timezone.utc),
    )


# ---------- workflow ownership ----------

async def claim_workflow(user_id: str, n8n_workflow_id: str):
    pool = db.get_pool()
    assert pool is not None
    await pool.execute(
        '''INSERT INTO workflow_owners (n8n_workflow_id, user_id)
           VALUES ($1, $2)
           ON CONFLICT (n8n_workflow_id) DO NOTHING''',
        n8n_workflow_id, user_id,
    )


async def release_workflow(user_id: str, n8n_workflow_id: str):
    """Used when DELETE /api/workflows/{id} succeeds."""
    pool = db.get_pool()
    assert pool is not None
    await pool.execute(
        'DELETE FROM workflow_owners WHERE n8n_workflow_id = $1 AND user_id = $2',
        n8n_workflow_id, user_id,
    )


async def is_owner(user_id: str, n8n_workflow_id: str) -> bool:
    pool = db.get_pool()
    assert pool is not None
    row = await pool.fetchrow(
        'SELECT 1 FROM workflow_owners WHERE n8n_workflow_id = $1 AND user_id = $2',
        n8n_workflow_id, user_id,
    )
    return row is not None


async def owned_workflow_ids(user_id: str) -> set[str]:
    pool = db.get_pool()
    assert pool is not None
    rows = await pool.fetch(
        'SELECT n8n_workflow_id FROM workflow_owners WHERE user_id = $1',
        user_id,
    )
    return {r['n8n_workflow_id'] for r in rows}

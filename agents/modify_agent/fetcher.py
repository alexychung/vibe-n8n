"""Phase 1: FETCH — pull live workflow JSON, original spec, recent executions.

The fetched state is the consistent snapshot every later phase operates on.
Re-fetching during APPLY is how we detect concurrent UI edits.
"""
import datetime
import json
import os
from dataclasses import dataclass, field
from typing import Optional

from client import N8nClient, N8nApiError


@dataclass
class FetchedState:
    workflow_id: str
    workflow: dict  # full n8n workflow JSON
    workflow_name: str
    is_active: bool
    spec: Optional[dict] = None  # raw spec dict (None if no spec on disk)
    spec_path: str = ''
    recent_executions: list[dict] = field(default_factory=list)
    recent_failure_count: int = 0
    last_execution_age_seconds: Optional[float] = None


def fetch_state(client: N8nClient, workflow_id: str, spec_dir: str = '') -> FetchedState:
    """Pull live workflow + best-effort spec + recent executions.

    `spec_dir`: directory to search for a spec file. Defaults to project_root/specs.
    Spec file is matched by workflow name slug (see _find_spec_file).
    """
    try:
        wf = client.get_workflow(workflow_id)
    except N8nApiError as e:
        if e.status_code == 404:
            raise ModifyError(f'Workflow {workflow_id} not found in n8n') from e
        raise ModifyError(f'Failed to fetch workflow {workflow_id}: {e}') from e

    name = wf.get('name', '')
    is_active = bool(wf.get('active'))

    spec, spec_path = (None, '')
    if spec_dir:
        spec, spec_path = _find_spec_file(spec_dir, name, workflow_id)

    executions: list[dict] = []
    last_age = None
    failure_count = 0
    try:
        executions = client.list_executions(workflow_id=workflow_id)
        executions = executions[:10]
        for ex in executions:
            if ex.get('finished') and ex.get('status') == 'error':
                failure_count += 1
        # Most recent execution age — n8n returns 'startedAt' as ISO string.
        if executions:
            started = executions[0].get('startedAt')
            if started:
                try:
                    # n8n timestamps end with 'Z' or include offset; strip 'Z' for fromisoformat.
                    iso = started.rstrip('Z')
                    started_dt = datetime.datetime.fromisoformat(iso)
                    if started_dt.tzinfo is None:
                        started_dt = started_dt.replace(tzinfo=datetime.timezone.utc)
                    now = datetime.datetime.now(datetime.timezone.utc)
                    last_age = (now - started_dt).total_seconds()
                except ValueError:
                    pass
    except N8nApiError:
        # Non-fatal — execution history is informational
        pass

    return FetchedState(
        workflow_id=workflow_id,
        workflow=wf,
        workflow_name=name,
        is_active=is_active,
        spec=spec,
        spec_path=spec_path,
        recent_executions=executions,
        recent_failure_count=failure_count,
        last_execution_age_seconds=last_age,
    )


def _slugify(name: str) -> str:
    """Delegate to build_agent.export._slugify so spec lookup uses the same
    slug build agent wrote. Falls back to a local implementation if the
    build_agent isn't on the path (shouldn't happen in normal use).
    """
    try:
        from export import _slugify as _build_slugify  # type: ignore[import-not-found]
        return _build_slugify(name)
    except ImportError:
        import re
        s = name.lower().strip()
        s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
        return s or 'workflow'


def _find_spec_file(spec_dir: str, workflow_name: str, workflow_id: str) -> tuple[Optional[dict], str]:
    """Search spec_dir for a spec matching the workflow name or ID.

    Tries (in order):
      1. <spec_dir>/<workflow_id>.json
      2. <spec_dir>/<slug>.json
      3. <spec_dir>/<slug>-spec.json
      4. Any *.json in spec_dir whose top-level workflow_name matches

    Returns (raw_spec_dict, path) or (None, '') if nothing found.
    """
    if not os.path.isdir(spec_dir):
        return None, ''

    slug = _slugify(workflow_name)
    candidates = [
        os.path.join(spec_dir, f'{workflow_id}.json'),
        os.path.join(spec_dir, f'{slug}.json'),
        os.path.join(spec_dir, f'{slug}-spec.json'),
    ]
    for path in candidates:
        if os.path.exists(path):
            spec = _try_load_json(path)
            if spec is not None:
                return spec, path

    # Fallback: scan for matching workflow_name field
    try:
        for fname in os.listdir(spec_dir):
            if not fname.endswith('.json'):
                continue
            path = os.path.join(spec_dir, fname)
            spec = _try_load_json(path)
            if spec is not None and spec.get('workflow_name') == workflow_name:
                return spec, path
    except OSError:
        pass

    return None, ''


def _try_load_json(path: str) -> Optional[dict]:
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


class ModifyError(Exception):
    """Raised by Modify Agent phases when something can't be done safely."""

"""Change log writer + human_summary generation.

Each modify produces one JSON entry under build-logs/changes/.
Used by the `history` CLI subcommand to list past changes.
"""
import datetime
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Optional

from edits import Edit


@dataclass
class ChangeLogEntry:
    modify_id: str
    workflow_id: str
    workflow_name: str
    started_at: str
    completed_at: str
    user_request: str
    classification: str  # tactical | structural | manual_edits
    edits_applied: list[dict]
    snapshot_path: str
    test_results: dict
    audit_results: dict
    deploy_outcome: str
    human_summary: str = ''
    rollback_reason: str = ''  # set iff modify rolled back

    def to_dict(self) -> dict:
        return {
            'modify_id': self.modify_id,
            'workflow_id': self.workflow_id,
            'workflow_name': self.workflow_name,
            'started_at': self.started_at,
            'completed_at': self.completed_at,
            'user_request': self.user_request,
            'classification': self.classification,
            'edits_applied': self.edits_applied,
            'snapshot_path': self.snapshot_path,
            'test_results': self.test_results,
            'audit_results': self.audit_results,
            'deploy_outcome': self.deploy_outcome,
            'human_summary': self.human_summary,
            'rollback_reason': self.rollback_reason,
        }


def new_modify_id() -> str:
    return uuid.uuid4().hex[:12]


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def write_change_log(entry: ChangeLogEntry, log_dir: str) -> str:
    """Write a change log entry as JSON. Returns the path written."""
    os.makedirs(log_dir, exist_ok=True)
    ts = entry.started_at.replace(':', '').replace('-', '')[:15]
    path = os.path.join(log_dir, f'{entry.workflow_id}-{ts}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(entry.to_dict(), f, indent=2, ensure_ascii=False)
    return path


def list_changes(log_dir: str, workflow_id: str) -> list[dict]:
    """List change log entries for a workflow, newest first."""
    if not os.path.isdir(log_dir):
        return []
    prefix = f'{workflow_id}-'
    entries = []
    for fname in sorted(os.listdir(log_dir), reverse=True):
        if not fname.startswith(prefix) or not fname.endswith('.json'):
            continue
        path = os.path.join(log_dir, fname)
        try:
            with open(path, encoding='utf-8') as f:
                entries.append(json.load(f))
        except (OSError, json.JSONDecodeError):
            continue
    return entries


def generate_human_summary(
    workflow_name: str,
    user_request: str,
    edits: list[Edit],
) -> str:
    """LLM-generated plain-English summary of the change.

    Falls back to a deterministic summary if LLM is unavailable — the change
    log still gets written, just without the polish.
    """
    fallback = _fallback_summary(edits)

    call_json = _load_call_json_or_none()
    if call_json is None:
        return fallback

    template_path = os.path.join(os.path.dirname(__file__), 'prompts', 'summarize_change.md')
    try:
        with open(template_path, encoding='utf-8') as f:
            template = f.read()
    except OSError:
        return fallback

    edits_json = json.dumps([e.to_dict() for e in edits], indent=2)
    prompt = (
        template
        .replace('{workflow_name}', workflow_name)
        .replace('{user_request}', user_request)
        .replace('{edits_json}', edits_json)
    )

    try:
        # call_json is fine even though we want text — the summarize prompt
        # could ask for either. Use call() for plain text:
        from importlib import util as _util
        pm_llm_path = os.path.abspath(os.path.join(
            os.path.dirname(__file__), '..', 'pm_agent', 'llm.py'
        ))
        spec = _util.spec_from_file_location('pm_agent_llm', pm_llm_path)
        if spec is None or spec.loader is None:
            return fallback
        mod = _util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        text = mod.call(
            'claude-haiku-4-5-20251001',
            'You write short, clear summaries of workflow changes for non-technical operators.',
            prompt,
            max_tokens=512,
        )
        return text.strip() or fallback
    except Exception:
        return fallback


def _fallback_summary(edits: list[Edit]) -> str:
    """Deterministic summary used when LLM is unavailable."""
    if not edits:
        return 'No edits applied.'
    parts = []
    for e in edits:
        if e.type == 'set_node_parameter':
            parts.append(f'Set {e.path} on node {e.node_id}: {e.old_value!r} → {e.new_value!r}')
        elif e.type == 'rename_node':
            parts.append(f'Renamed node {e.node_id}: {e.old_name!r} → {e.new_name!r}')
        elif e.type == 'set_node_setting':
            parts.append(f'Set {e.path} on node {e.node_id} to {e.new_value!r}')
        elif e.type == 'update_credential_ref':
            parts.append(f'Swapped {e.credential_type} credential on node {e.node_id}')
        elif e.type == 'set_workflow_setting':
            parts.append(f'Set workflow setting {e.path} to {e.new_value!r}')
        elif e.type == 'rename_workflow':
            parts.append(f'Renamed workflow: {e.old_value!r} → {e.new_value!r}')
        else:
            parts.append(f'{e.type}')
    return '. '.join(parts) + '.'


def _load_call_json_or_none():
    """Same lazy-import dance as classifier.py — stays None when SDK is missing."""
    if not os.environ.get('ANTHROPIC_API_KEY'):
        return None
    import importlib.util
    pm_llm_path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), '..', 'pm_agent', 'llm.py'
    ))
    if not os.path.exists(pm_llm_path):
        return None
    spec = importlib.util.spec_from_file_location('pm_agent_llm', pm_llm_path)
    if spec is None or spec.loader is None:
        return None
    try:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except ImportError:
        return None
    return getattr(mod, 'call_json', None)

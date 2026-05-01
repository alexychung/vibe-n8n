"""Phase 3a: PLAN (tactical) — validate the edit list against the live workflow.

Every edit must reference a real node, with an `old_value` that matches the
current state. If validation fails, the modify aborts before SNAPSHOT, so
the live workflow is untouched.
"""
from dataclasses import dataclass

from edits import Edit
from fetcher import ModifyError


@dataclass
class PlanResult:
    edits: list[Edit]
    summary: str  # one-line description of the planned change


def validate_tactical_edits(edits: list[Edit], workflow: dict) -> PlanResult:
    """Validate each tactical edit against the live workflow.

    Raises ModifyError on the first invalid edit, with a message that names
    the offending edit and what's wrong (so the user can re-run with corrected
    inputs).
    """
    if not edits:
        raise ModifyError('Planner received an empty edit list — nothing to do')

    nodes_by_id = {n.get('id'): n for n in workflow.get('nodes', [])}
    settings = workflow.get('settings', {}) or {}

    for i, edit in enumerate(edits):
        if not edit.is_tactical():
            raise ModifyError(
                f'edits[{i}]: {edit.type!r} is not a tactical edit type — '
                f'use Phase 2 of the rollout for structural changes'
            )
        _validate_one(edit, i, nodes_by_id, workflow, settings)

    summary = _summarize_edits(edits)
    return PlanResult(edits=edits, summary=summary)


def _validate_one(edit: Edit, i: int, nodes_by_id: dict, workflow: dict, settings: dict):
    if edit.type == 'set_node_parameter':
        node = _require_node(edit, i, nodes_by_id)
        actual = _get_path(node, edit.path)
        _check_old_value(edit, i, actual)

    elif edit.type == 'rename_node':
        node = _require_node(edit, i, nodes_by_id)
        if not edit.new_name:
            raise ModifyError(f'edits[{i}]: rename_node requires new_name')
        if edit.old_name and node.get('name') != edit.old_name:
            raise ModifyError(
                f'edits[{i}]: old_name {edit.old_name!r} does not match '
                f'live node name {node.get("name")!r}'
            )
        # Reject collisions
        for other in workflow.get('nodes', []):
            if other.get('id') != edit.node_id and other.get('name') == edit.new_name:
                raise ModifyError(
                    f'edits[{i}]: rename target {edit.new_name!r} collides '
                    f'with existing node {other.get("id")}'
                )

    elif edit.type == 'set_node_setting':
        node = _require_node(edit, i, nodes_by_id)
        # Settings live at the top level of the node (e.g. node['retryOnFail'])
        # OR nested in parameters.options for HTTP — accept whichever path.
        actual = _get_path(node, edit.path)
        _check_old_value(edit, i, actual)

    elif edit.type == 'update_credential_ref':
        node = _require_node(edit, i, nodes_by_id)
        if not edit.credential_type:
            raise ModifyError(f'edits[{i}]: update_credential_ref requires credential_type')
        creds = node.get('credentials', {}) or {}
        cur = creds.get(edit.credential_type, {})
        actual = cur.get('id') if isinstance(cur, dict) else None
        _check_old_value(edit, i, actual)
        if not edit.new_value:
            raise ModifyError(f'edits[{i}]: update_credential_ref requires new_value (credential id)')

    elif edit.type == 'set_workflow_setting':
        actual = _get_path({'settings': settings}, f'settings.{edit.path}')
        _check_old_value(edit, i, actual)

    elif edit.type == 'rename_workflow':
        if not edit.new_value:
            raise ModifyError(f'edits[{i}]: rename_workflow requires new_value')
        if edit.old_value is not None and workflow.get('name') != edit.old_value:
            raise ModifyError(
                f'edits[{i}]: old_value {edit.old_value!r} does not match '
                f'live workflow name {workflow.get("name")!r}'
            )

    else:
        raise ModifyError(f'edits[{i}]: unknown tactical edit type {edit.type!r}')


def _require_node(edit: Edit, i: int, nodes_by_id: dict) -> dict:
    if not edit.node_id:
        raise ModifyError(f'edits[{i}]: {edit.type} requires node_id')
    node = nodes_by_id.get(edit.node_id)
    if node is None:
        raise ModifyError(
            f'edits[{i}]: node_id {edit.node_id!r} not found in live workflow '
            f'(available: {sorted(nodes_by_id)})'
        )
    return node


def _check_old_value(edit: Edit, i: int, actual):
    """Strict equality check between recorded old_value and live state.

    None on the recorded side means the planner is willing to apply blindly
    (rare — callers should always provide old_value). Mismatches mean the
    workflow has drifted (UI edit, prior modify) and we abort to avoid
    overwriting the user's other changes.
    """
    if edit.old_value is None:
        return
    if actual != edit.old_value:
        raise ModifyError(
            f'edits[{i}]: old_value drift on {edit.path or edit.type} — '
            f'expected {edit.old_value!r}, found {actual!r}. '
            f'Workflow may have been edited externally; re-run after refreshing.'
        )


def _get_path(obj, path: str):
    """Walk a dot-path with optional [index] segments. Returns None on miss.

    Supports `parameters.assignments.assignments[0].value` and similar.
    """
    if not path:
        return None
    cur = obj
    parts = _split_path(path)
    for p in parts:
        if isinstance(p, int):
            if not isinstance(cur, list) or p >= len(cur):
                return None
            cur = cur[p]
        else:
            if not isinstance(cur, dict):
                return None
            if p not in cur:
                return None
            cur = cur[p]
    return cur


def _split_path(path: str) -> list:
    """'a.b[0].c' → ['a', 'b', 0, 'c']."""
    parts: list = []
    for chunk in path.split('.'):
        # Split on bracket indices
        while '[' in chunk:
            head, _, rest = chunk.partition('[')
            if head:
                parts.append(head)
            idx_str, _, chunk = rest.partition(']')
            try:
                parts.append(int(idx_str))
            except ValueError:
                parts.append(idx_str)
        if chunk:
            parts.append(chunk)
    return parts


def _summarize_edits(edits: list[Edit]) -> str:
    by_type: dict[str, int] = {}
    for e in edits:
        by_type[e.type] = by_type.get(e.type, 0) + 1
    parts = [f'{n} {t}' for t, n in sorted(by_type.items())]
    return f'{len(edits)} edits: ' + ', '.join(parts)

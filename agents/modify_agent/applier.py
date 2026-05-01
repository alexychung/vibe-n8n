"""Phase 5: APPLY — execute the validated edit list against the live workflow.

Identity preservation is the central rule: node IDs, positions, credentials,
unaffected connections all stay exactly as they were unless an edit explicitly
changes them.

UI-drift guard: re-fetches the workflow before mutating; if the JSON diverges
from the snapshot taken in Phase 1, abort with a clear message.
"""
import copy
import json
from dataclasses import dataclass

from client import N8nClient
from edits import Edit
from fetcher import ModifyError
from planner import _split_path


@dataclass
class ApplyResult:
    workflow_id: str
    was_active: bool  # whether we deactivated for the modify
    edits_applied: list[Edit]
    final_workflow: dict


def apply_edits(
    client: N8nClient,
    workflow_id: str,
    edits: list[Edit],
    snapshot_workflow: dict,
    was_active: bool,
) -> ApplyResult:
    """Apply tactical edits to the live workflow.

    1. Deactivate if active (caller passes was_active from FETCH)
    2. Re-fetch and compare against snapshot — abort on UI drift
    3. Apply edits in order to an in-memory copy
    4. PUT back via update_workflow
    """
    if was_active:
        try:
            client.deactivate_workflow(workflow_id)
        except Exception as e:
            raise ModifyError(f'Failed to deactivate workflow before APPLY: {e}') from e

    # UI-drift guard + edit application both happen INSIDE the modifier so
    # they operate on the same GET that update_workflow's PUT will use. If
    # we did the drift check on a separate earlier GET, a UI edit between
    # the two GETs would silently get overwritten — exactly the corruption
    # the drift guard exists to prevent.
    def _modify(latest: dict) -> dict:
        if not _matches_snapshot(latest, snapshot_workflow):
            raise ModifyError(
                'Workflow was edited externally between FETCH and APPLY '
                '(nodes/connections/settings differ from snapshot). '
                'Re-run the modify.'
            )
        result = copy.deepcopy(latest)
        for edit in edits:
            _apply_one(result, edit)
        return result

    final = client.update_workflow(workflow_id, _modify)

    return ApplyResult(
        workflow_id=workflow_id,
        was_active=was_active,
        edits_applied=list(edits),
        final_workflow=final,
    )


def _matches_snapshot(live: dict, snap: dict) -> bool:
    """True iff structural fields are byte-identical between live and snapshot.

    Compares: name, nodes, connections, settings.
    Ignores: id, createdAt, updatedAt, versionId, active, tags, and other
    n8n-managed fields that change on every read.
    """
    fields = ('name', 'nodes', 'connections', 'settings')
    for f in fields:
        # JSON-canonicalize so dict key order doesn't trigger a false positive.
        l = json.dumps(live.get(f), sort_keys=True, default=str)
        s = json.dumps(snap.get(f), sort_keys=True, default=str)
        if l != s:
            return False
    return True


def _apply_one(wf: dict, edit: Edit):
    """Mutate wf in place to apply one edit. Identity-preserving."""
    if edit.type == 'set_node_parameter':
        node = _find_node_by_id(wf, edit.node_id)
        _set_path(node, edit.path, edit.new_value)

    elif edit.type == 'rename_node':
        node = _find_node_by_id(wf, edit.node_id)
        old_name = node['name']
        node['name'] = edit.new_name
        # Walk connections and rewrite both the keying and the targets.
        # n8n's connections object keys nodes by NAME (not id) — load-bearing.
        _rewrite_connection_node_name(wf, old_name, edit.new_name)

    elif edit.type == 'set_node_setting':
        node = _find_node_by_id(wf, edit.node_id)
        _set_path(node, edit.path, edit.new_value)

    elif edit.type == 'update_credential_ref':
        node = _find_node_by_id(wf, edit.node_id)
        creds = node.setdefault('credentials', {})
        cur = creds.get(edit.credential_type, {}) or {}
        # Preserve credential `name` if present; only the id is being swapped.
        new_entry = {'id': edit.new_value}
        if isinstance(cur, dict) and 'name' in cur:
            new_entry['name'] = cur['name']
        creds[edit.credential_type] = new_entry

    elif edit.type == 'set_workflow_setting':
        settings = wf.setdefault('settings', {})
        _set_path({'settings': settings}, f'settings.{edit.path}', edit.new_value)

    elif edit.type == 'rename_workflow':
        wf['name'] = edit.new_value

    else:
        raise ModifyError(f'Cannot apply edit type {edit.type!r} in tactical applier')


def _find_node_by_id(wf: dict, node_id: str) -> dict:
    for n in wf.get('nodes', []):
        if n.get('id') == node_id:
            return n
    raise ModifyError(f'Node id {node_id!r} disappeared during APPLY — cannot continue')


def _rewrite_connection_node_name(wf: dict, old_name: str, new_name: str):
    """Rewrite both keys and target references in the connections object.

    n8n connections shape:
      {"<source_name>": {"main": [[{"node": "<target_name>", ...}, ...], ...]}}
    """
    conns = wf.get('connections', {}) or {}
    new_conns: dict = {}
    for source, ports in conns.items():
        key = new_name if source == old_name else source
        # Deep-copy then rewrite targets
        new_ports: dict = {}
        for port_name, output_lists in (ports or {}).items():
            rewritten_lists = []
            for out_list in output_lists or []:
                rewritten = []
                for entry in out_list or []:
                    if isinstance(entry, dict) and entry.get('node') == old_name:
                        e = dict(entry)
                        e['node'] = new_name
                        rewritten.append(e)
                    else:
                        rewritten.append(entry)
                rewritten_lists.append(rewritten)
            new_ports[port_name] = rewritten_lists
        new_conns[key] = new_ports
    wf['connections'] = new_conns


def _set_path(obj, path: str, value):
    """Set a value at a dot-path, creating intermediate dicts as needed.

    Lists must already exist (we don't auto-create indices). Mirrors
    planner._get_path's parsing.
    """
    parts = _split_path(path)
    if not parts:
        raise ModifyError(f'Cannot set empty path on {type(obj).__name__}')

    cur = obj
    for p in parts[:-1]:
        if isinstance(p, int):
            if not isinstance(cur, list) or p >= len(cur):
                raise ModifyError(f'Path {path!r}: index {p} out of range')
            cur = cur[p]
        else:
            if not isinstance(cur, dict):
                raise ModifyError(f'Path {path!r}: expected dict at {p!r}, got {type(cur).__name__}')
            if p not in cur:
                cur[p] = {}  # auto-create missing intermediates
            elif not isinstance(cur[p], (dict, list)):
                # Don't silently destroy a scalar — caller must explicitly
                # set the parent if they want to convert it to a dict/list.
                raise ModifyError(
                    f'Path {path!r}: cannot traverse into non-container at {p!r} '
                    f'(found {type(cur[p]).__name__})'
                )
            cur = cur[p]

    last = parts[-1]
    if isinstance(last, int):
        if not isinstance(cur, list) or last >= len(cur):
            raise ModifyError(f'Path {path!r}: index {last} out of range')
        cur[last] = value
    else:
        if not isinstance(cur, dict):
            raise ModifyError(f'Path {path!r}: cannot set {last!r} on {type(cur).__name__}')
        cur[last] = value

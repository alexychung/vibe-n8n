"""ROLLBACK — restore a workflow from a snapshot.

Cross-cutting concern: any phase from APPLY onward can trigger this.
Snapshot file is never modified by rollback; manual rollback after the
fact is always possible by re-running with the snapshot path.
"""
import copy
import json
from dataclasses import dataclass

from client import N8nClient, N8nApiError
from fetcher import ModifyError
from snapshot import load_snapshot


@dataclass
class RollbackResult:
    workflow_id: str
    snapshot_path: str
    restored: bool
    reactivated: bool
    verification_passed: bool
    error: str = ''


def rollback(
    client: N8nClient,
    workflow_id: str,
    snapshot_path: str,
    snapshot_was_active: bool,
    reason: str = '',
) -> RollbackResult:
    """PUT the snapshot JSON back, then verify GET matches.

    `snapshot_was_active`: whether the workflow was active when the snapshot
    was taken — if so, we re-activate after restore.
    `reason`: surfaced in the result for the change log.
    """
    try:
        snap = load_snapshot(snapshot_path)
    except (OSError, json.JSONDecodeError) as e:
        return RollbackResult(
            workflow_id=workflow_id,
            snapshot_path=snapshot_path,
            restored=False,
            reactivated=False,
            verification_passed=False,
            error=f'Cannot load snapshot {snapshot_path}: {e}',
        )
    # Sanity check: a tampered/truncated snapshot would PUT garbage.
    # Require the minimum fields we'll PUT back.
    for field in ('name', 'nodes', 'connections'):
        if field not in snap:
            return RollbackResult(
                workflow_id=workflow_id,
                snapshot_path=snapshot_path,
                restored=False,
                reactivated=False,
                verification_passed=False,
                error=f'Snapshot at {snapshot_path} is missing required field {field!r} — cannot restore safely',
            )

    # Always deactivate before PUT — n8n rejects updates to active workflows
    # in some configs, and deactivate is idempotent.
    try:
        client.deactivate_workflow(workflow_id)
    except N8nApiError:
        pass  # may already be inactive

    try:
        def _restore(_wf):
            return copy.deepcopy(snap)
        client.update_workflow(workflow_id, _restore)
    except N8nApiError as e:
        return RollbackResult(
            workflow_id=workflow_id,
            snapshot_path=snapshot_path,
            restored=False,
            reactivated=False,
            verification_passed=False,
            error=f'Rollback PUT failed: {e}. Snapshot at {snapshot_path}',
        )

    reactivated = False
    if snapshot_was_active:
        # n8n's /activate sometimes returns 200 with active=true but the
        # workflow doesn't actually activate (race after a deactivate-PUT-
        # activate sequence). Activate, then GET to verify, retry once.
        import time
        activate_error: str = ''
        for attempt in range(2):
            try:
                client.activate_workflow(workflow_id)
            except N8nApiError as e:
                activate_error = str(e)
                if attempt == 0:
                    time.sleep(1)
                    continue
                return RollbackResult(
                    workflow_id=workflow_id,
                    snapshot_path=snapshot_path,
                    restored=True,
                    reactivated=False,
                    verification_passed=False,
                    error=f'Restored content but failed to re-activate: {activate_error}',
                )
            try:
                live_check = client.get_workflow(workflow_id)
            except N8nApiError as e:
                activate_error = str(e)
                if attempt == 0:
                    time.sleep(1)
                    continue
            else:
                if live_check.get('active'):
                    reactivated = True
                    break
                activate_error = 'activate API returned 200 but workflow is still inactive'
                if attempt == 0:
                    time.sleep(1)
                    continue
        if not reactivated:
            return RollbackResult(
                workflow_id=workflow_id,
                snapshot_path=snapshot_path,
                restored=True,
                reactivated=False,
                verification_passed=False,
                error=f'Restored content but workflow did not become active: {activate_error}',
            )

    # Verify: GET and compare structural fields against snapshot
    try:
        live = client.get_workflow(workflow_id)
    except N8nApiError as e:
        return RollbackResult(
            workflow_id=workflow_id,
            snapshot_path=snapshot_path,
            restored=True,
            reactivated=reactivated,
            verification_passed=False,
            error=f'Verification GET failed: {e}',
        )

    verified = _verify_structural_match(live, snap)

    return RollbackResult(
        workflow_id=workflow_id,
        snapshot_path=snapshot_path,
        restored=True,
        reactivated=reactivated,
        verification_passed=verified,
        error='' if verified else 'Post-rollback verification mismatch — investigate manually',
    )


def _verify_structural_match(live: dict, snap: dict) -> bool:
    """Compare nodes/connections/settings/name. Same logic as applier's drift guard."""
    fields = ('name', 'nodes', 'connections', 'settings')
    for f in fields:
        l = json.dumps(live.get(f), sort_keys=True, default=str)
        s = json.dumps(snap.get(f), sort_keys=True, default=str)
        if l != s:
            return False
    return True

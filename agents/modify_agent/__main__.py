"""CLI entry point for the Modify Agent.

Usage:
    python -m agents.modify_agent change <workflow_id> "rename the webhook to qualify-v2"
    python -m agents.modify_agent change <workflow_id> --edits edits.json
    python -m agents.modify_agent change <workflow_id> "..." --dry-run
    python -m agents.modify_agent rollback <workflow_id> --snapshot path/to/snap.json
    python -m agents.modify_agent history <workflow_id>
"""
import io
import json
import os
import sys

# Force UTF-8 on Windows console — workflow names and LLM-generated summaries
# can contain Unicode that crashes the cp1252 default codec.
if sys.platform == 'win32' and __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Wire up paths: we need both the build_agent dir (for client/auditor/harden/
# test_runner/models) and our own dir on sys.path. This mirrors build_agent's
# __main__.py pattern.
_AGENT_DIR = os.path.dirname(__file__)
_BUILD_AGENT_DIR = os.path.abspath(os.path.join(_AGENT_DIR, '..', 'build_agent'))
sys.path.insert(0, _AGENT_DIR)
sys.path.insert(0, _BUILD_AGENT_DIR)

from client import N8nClient, N8nApiError  # noqa: E402
from auditor import render_findings  # noqa: E402
from harden import harden  # noqa: E402
from test_runner import run_tests, render_results  # noqa: E402
from models import parse_spec, ValidationError  # noqa: E402

from edits import Edit  # noqa: E402
from fetcher import fetch_state, ModifyError  # noqa: E402
from classifier import classify  # noqa: E402
from planner import validate_tactical_edits  # noqa: E402
from snapshot import save_snapshot, cleanup_snapshots  # noqa: E402
from applier import apply_edits  # noqa: E402
from audit_diff import audit_delta  # noqa: E402
from rollback import rollback as do_rollback  # noqa: E402
from change_log import (  # noqa: E402
    ChangeLogEntry, write_change_log, list_changes,
    new_modify_id, now_iso, generate_human_summary,
)
from modify_status import ModifyStatus  # noqa: E402


def _project_root() -> str:
    d = os.path.dirname(__file__)
    for _ in range(5):
        if os.path.exists(os.path.join(d, '.env')) or os.path.exists(os.path.join(d, 'CLAUDE.md')):
            return d
        d = os.path.dirname(d)
    return os.getcwd()


def load_env():
    """Load .env from project root if env vars not set."""
    if os.environ.get('N8N_API_KEY'):
        return
    env_path = os.path.join(_project_root(), '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, val = line.split('=', 1)
                    os.environ[key.strip()] = val.strip()


def _snapshot_dir() -> str:
    return os.path.join(_project_root(), 'build-logs', 'snapshots')


def _changes_dir() -> str:
    return os.path.join(_project_root(), 'build-logs', 'changes')


def _read_webhook_auth(workflow_name: str) -> dict:
    """Look for build-logs/{slug}-auth.env and read the first webhook auth pair.

    Returns {} if no auth file found. Used to thread auth through TEST and
    DEPLOY-smoke webhook calls when the live workflow has been hardened.
    Only the first credential's header/token is used (the spec's first
    webhook trigger is what we'll be testing).
    """
    import re
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', workflow_name).strip('-').lower() or 'workflow'
    path = os.path.join(_project_root(), 'build-logs', f'{slug}-auth.env')
    if not os.path.exists(path):
        return {}
    header_name = ''
    token = ''
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k = k.strip()
                v = v.strip()
                # First trigger header/token wins — pattern is
                # WEBHOOK_AUTH_HEADER_1 / WEBHOOK_AUTH_TOKEN_1.
                if k.startswith('WEBHOOK_AUTH_HEADER') and not header_name:
                    header_name = v
                elif k.startswith('WEBHOOK_AUTH_TOKEN') and not token:
                    token = v
                if header_name and token:
                    break
    except OSError:
        return {}
    return {header_name: token} if header_name and token else {}


def _spec_dir() -> str:
    """Where to look for build specs. Returns the first directory that exists.

    Project convention: build specs live under workflows/test-data/.
    Fall back to specs/ for forward compatibility.
    """
    root = _project_root()
    for candidate in ('workflows/test-data', 'specs'):
        path = os.path.join(root, candidate)
        if os.path.isdir(path):
            return path
    return os.path.join(root, 'specs')


def _print_status(status: ModifyStatus):
    print(status.render())
    print()


def cmd_change(
    workflow_id: str,
    change_description: str,
    explicit_edits_path: str = '',
    dry_run: bool = False,
) -> int:
    """Run the full modify pipeline."""
    if not os.environ.get('N8N_API_KEY'):
        print('Error: N8N_API_KEY not set. Add it to .env or set the environment variable.')
        return 1

    client = N8nClient()
    started_at = now_iso()
    modify_id = new_modify_id()

    # ---- Phase 1: FETCH ----
    try:
        state = fetch_state(client, workflow_id, spec_dir=_spec_dir())
    except ModifyError as e:
        print(f'FETCH failed: {e}')
        return 1

    status = ModifyStatus(state.workflow_name or workflow_id, workflow_id)
    notes = f'{len(state.workflow.get("nodes", []))} nodes'
    notes += ', active' if state.is_active else ', inactive'
    if state.recent_failure_count:
        notes += f', {state.recent_failure_count} recent failures'
    if state.last_execution_age_seconds is not None and state.last_execution_age_seconds < 60:
        print(f'Warning: workflow executed {state.last_execution_age_seconds:.0f}s ago — may still be running')
    if state.spec_path:
        notes += f', spec={os.path.basename(state.spec_path)}'
    status.done('FETCH', notes)
    _print_status(status)

    # ---- Phase 2: CLASSIFY ----
    if explicit_edits_path:
        try:
            with open(explicit_edits_path, encoding='utf-8') as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            status.fail('CLASSIFY', f'failed to load --edits file: {e}')
            _print_status(status)
            return 1
        edits_raw = payload.get('edits', payload) if isinstance(payload, dict) else payload
        if not isinstance(edits_raw, list):
            status.fail('CLASSIFY', 'edits file must contain a list of edits')
            _print_status(status)
            return 1
        edits = [Edit.from_dict(e) for e in edits_raw]
        classification = 'manual_edits'
        status.done('CLASSIFY', f'manual: {len(edits)} edits from {explicit_edits_path}')
    else:
        if not change_description:
            status.fail('CLASSIFY', 'no change description and no --edits file')
            _print_status(status)
            return 1
        try:
            cls = classify(change_description, state.workflow, workflow_id)
        except ModifyError as e:
            status.fail('CLASSIFY', str(e))
            _print_status(status)
            return 1
        classification = cls.classification
        if classification == 'structural':
            status.fail('CLASSIFY', f'structural: {cls.structural_summary or cls.reason}')
            _print_status(status)
            print('\nStructural changes are not supported in Phase 1 of the Modify Agent.')
            print('Re-plan with the PM Agent and rebuild via the Build Agent.')
            return 2  # distinct exit code so scripts can detect "needs PM Agent"
        edits = cls.edits
        status.done('CLASSIFY', f'tactical: {len(edits)} edits — {cls.reason}')
    _print_status(status)

    # ---- Phase 3a: PLAN ----
    try:
        plan = validate_tactical_edits(edits, state.workflow)
    except ModifyError as e:
        status.fail('PLAN', str(e))
        _print_status(status)
        return 1
    status.done('PLAN', plan.summary)
    _print_status(status)

    if dry_run:
        for skip_phase in ('SNAPSHOT', 'APPLY', 'TEST', 'AUDIT', 'HARDEN', 'DEPLOY'):
            status.skip(skip_phase, '--dry-run')
        _print_status(status)
        print('Dry run — would apply these edits:')
        for e in plan.edits:
            print(f'  - {json.dumps(e.to_dict(), ensure_ascii=False)}')
        return 0

    # ---- Phase 4: SNAPSHOT ----
    snapshot_dir = _snapshot_dir()
    cleanup_snapshots(snapshot_dir, workflow_id)
    snap = save_snapshot(workflow_id, state.workflow, snapshot_dir)
    status.snapshot_path = snap.path
    status.done('SNAPSHOT', f'saved to {os.path.relpath(snap.path, _project_root())}')
    _print_status(status)

    # ---- Phase 5: APPLY ----
    try:
        apply_result = apply_edits(
            client=client,
            workflow_id=workflow_id,
            edits=plan.edits,
            snapshot_workflow=state.workflow,
            was_active=state.is_active,
        )
    except ModifyError as e:
        status.fail('APPLY', str(e))
        _print_status(status)
        # Even though APPLY failed, the workflow may have been deactivated.
        # Restore active state via rollback (snapshot == original).
        _do_rollback(client, status, workflow_id, snap.path, state.is_active, str(e))
        _write_log(modify_id, started_at, state, change_description, classification,
                   plan.edits, snap.path, {'passed': 0, 'failed': 0},
                   {'new_critical': 0, 'new_warning': 0, 'new_info': 0},
                   'rolled_back_in_apply', rollback_reason=str(e))
        return 1
    status.done('APPLY', f'{len(plan.edits)} edits applied, PUT returned 200')
    _print_status(status)

    # ---- Phase 6: TEST ----
    test_results = {'passed': 0, 'failed': 0, 'skipped': False}
    spec = _load_spec_for_tests(state)
    webhook_headers = _read_webhook_auth(state.workflow_name)
    if spec is None:
        status.skip('TEST', 'no spec/test_cases available — modify deploys without re-test')
        test_results['skipped'] = True
    else:
        try:
            results = run_tests(spec, client, workflow_id, extra_headers=webhook_headers or None)
        except Exception as e:
            status.fail('TEST', f'test runner crashed: {e}')
            _print_status(status)
            _do_rollback(client, status, workflow_id, snap.path, state.is_active, str(e))
            _write_log(modify_id, started_at, state, change_description, classification,
                       plan.edits, snap.path, test_results,
                       {'new_critical': 0, 'new_warning': 0, 'new_info': 0},
                       'rolled_back_in_test', rollback_reason=str(e))
            return 1
        passed = sum(1 for r in results if r.passed)
        total = len(results)
        test_results = {'passed': passed, 'failed': total - passed, 'skipped': False}
        if passed != total:
            status.fail('TEST', f'{passed}/{total} pass — rolling back')
            _print_status(status)
            print(render_results(results))
            _do_rollback(client, status, workflow_id, snap.path, state.is_active,
                         f'{total - passed} test(s) failed after modify')
            _write_log(modify_id, started_at, state, change_description, classification,
                       plan.edits, snap.path, test_results,
                       {'new_critical': 0, 'new_warning': 0, 'new_info': 0},
                       'rolled_back_in_test',
                       rollback_reason=f'{total - passed} test(s) failed')
            return 1
        status.done('TEST', f'{passed}/{total} pass')
    _print_status(status)

    # ---- Phase 7: AUDIT (delta) ----
    modified_wf = client.get_workflow(workflow_id)
    delta = audit_delta(state.workflow, modified_wf)
    audit_results = {
        'new_critical': delta.new_critical,
        'new_warning': delta.new_warning,
        'new_info': delta.new_info,
    }
    status.done('AUDIT', f'{delta.new_critical} new critical, {delta.new_warning} new warning, '
                         f'{delta.new_info} new info ({len(delta.suppressed)} pre-existing suppressed)')
    _print_status(status)

    # ---- Phase 8: HARDEN (only if new findings) ----
    if delta.new_critical or delta.new_warning:
        os.environ['MODIFY_MODE'] = '1'
        try:
            harden_result = harden(
                client, workflow_id,
                workflow_name=state.workflow_name,
                disable_credential_creation=True,
            )
        finally:
            os.environ.pop('MODIFY_MODE', None)
        # Re-audit-delta after harden
        modified_wf = client.get_workflow(workflow_id)
        post = audit_delta(state.workflow, modified_wf)
        if post.new_critical or post.new_warning:
            status.fail('HARDEN', f'{post.new_critical} critical / {post.new_warning} warning remain')
            _print_status(status)
            print(render_findings(post.new_findings))
            _do_rollback(client, status, workflow_id, snap.path, state.is_active,
                         'unfixable new audit findings')
            _write_log(modify_id, started_at, state, change_description, classification,
                       plan.edits, snap.path, test_results, audit_results,
                       'rolled_back_in_harden',
                       rollback_reason='unfixable new audit findings')
            return 1
        status.done('HARDEN', f'fixed all {delta.new_warning + delta.new_critical} new findings')
    else:
        status.skip('HARDEN', 'no new findings')
    _print_status(status)

    # ---- Phase 9: DEPLOY ----
    deploy_outcome = 'inactive — was inactive before modify'
    if state.is_active:
        try:
            client.activate_workflow(workflow_id)
        except N8nApiError as e:
            status.fail('DEPLOY', f'activation failed: {e}')
            _print_status(status)
            _do_rollback(client, status, workflow_id, snap.path, state.is_active, str(e))
            _write_log(modify_id, started_at, state, change_description, classification,
                       plan.edits, snap.path, test_results, audit_results,
                       'rolled_back_in_deploy', rollback_reason=str(e))
            return 1
        # Smoke test if there's a webhook
        smoke_outcome = _smoke_test(client, spec, webhook_headers)
        if smoke_outcome == 'failed':
            status.fail('DEPLOY', 'smoke test failed — rolling back')
            _print_status(status)
            _do_rollback(client, status, workflow_id, snap.path, state.is_active,
                         'production smoke test failed')
            _write_log(modify_id, started_at, state, change_description, classification,
                       plan.edits, snap.path, test_results, audit_results,
                       'rolled_back_in_deploy', rollback_reason='smoke test failed')
            return 1
        deploy_outcome = f'active, smoke test {smoke_outcome}'
        status.done('DEPLOY', deploy_outcome)
    else:
        status.done('DEPLOY', deploy_outcome)
    _print_status(status)

    # ---- Change log ----
    summary = generate_human_summary(state.workflow_name, change_description, plan.edits)
    _write_log(
        modify_id, started_at, state, change_description, classification,
        plan.edits, snap.path, test_results, audit_results,
        deploy_outcome, human_summary=summary,
    )
    print(f'\n{summary}')
    return 0


def _smoke_test(client: N8nClient, spec, webhook_headers: dict | None = None) -> str:
    """Send the first happy-path test through the production webhook.

    Returns 'passed' | 'failed' | 'skipped'.
    """
    if spec is None or not spec.test_cases or not spec.trigger.path:
        return 'skipped (no spec or no webhook)'
    tc = spec.test_cases[0]
    method = (spec.trigger.method or 'POST').upper()
    try:
        if method == 'GET':
            wrapped = tc.input.get('query') if isinstance(tc.input, dict) else None
            query = wrapped if isinstance(wrapped, dict) else (tc.input if isinstance(tc.input, dict) else {})
            actual = client.send_webhook(spec.trigger.path, method='GET', query=query, headers=webhook_headers or None)
        else:
            actual = client.send_webhook(spec.trigger.path, tc.input, headers=webhook_headers or None)
    except Exception:
        return 'failed'
    # We don't strictly compare — production webhook returning a dict response
    # at all is a success signal that routing works end-to-end.
    if isinstance(actual, dict) and actual.get('http_status', 0) < 500:
        return 'passed'
    return 'failed'


def _load_spec_for_tests(state):
    """Build a parsed WorkflowSpec from state.spec, or None if unavailable."""
    if not state.spec:
        return None
    try:
        return parse_spec(state.spec)
    except ValidationError as e:
        print(f'Note: spec on disk failed to parse ({e}); skipping automated tests')
        return None


def _do_rollback(client, status: ModifyStatus, workflow_id: str, snapshot_path: str,
                 was_active: bool, reason: str):
    rb = do_rollback(client, workflow_id, snapshot_path, was_active, reason=reason)
    if rb.restored and rb.verification_passed:
        status.done('ROLLBACK', f'restored to {os.path.basename(snapshot_path)}')
    else:
        status.fail('ROLLBACK', rb.error or 'verification failed')
    _print_status(status)


def _write_log(
    modify_id: str, started_at: str, state, user_request: str,
    classification: str, edits: list, snapshot_path: str,
    test_results: dict, audit_results: dict, deploy_outcome: str,
    human_summary: str = '', rollback_reason: str = '',
) -> str:
    entry = ChangeLogEntry(
        modify_id=modify_id,
        workflow_id=state.workflow_id,
        workflow_name=state.workflow_name,
        started_at=started_at,
        completed_at=now_iso(),
        user_request=user_request,
        classification=classification,
        edits_applied=[e.to_dict() for e in edits],
        snapshot_path=snapshot_path,
        test_results=test_results,
        audit_results=audit_results,
        deploy_outcome=deploy_outcome,
        human_summary=human_summary,
        rollback_reason=rollback_reason,
    )
    return write_change_log(entry, _changes_dir())


def cmd_rollback(workflow_id: str, snapshot_path: str) -> int:
    """Manual rollback to a known snapshot."""
    if not os.environ.get('N8N_API_KEY'):
        print('Error: N8N_API_KEY not set.')
        return 1
    client = N8nClient()
    # Determine the live workflow's active state to decide whether to re-activate
    try:
        live = client.get_workflow(workflow_id)
    except N8nApiError as e:
        print(f'Cannot fetch workflow {workflow_id}: {e}')
        return 1
    rb = do_rollback(client, workflow_id, snapshot_path, bool(live.get('active')),
                     reason='manual rollback')
    if rb.restored and rb.verification_passed:
        print(f'Restored {workflow_id} from {snapshot_path}')
        return 0
    print(f'Rollback issue: {rb.error}')
    return 1


def cmd_history(workflow_id: str) -> int:
    """List change log entries for a workflow."""
    entries = list_changes(_changes_dir(), workflow_id)
    if not entries:
        print(f'No changes recorded for {workflow_id}')
        return 0
    print(f'{len(entries)} change(s) for {workflow_id}:\n')
    for e in entries:
        print(f'  {e.get("started_at", "?")} — {e.get("classification", "?")} — '
              f'{e.get("deploy_outcome", "?")}')
        if e.get('human_summary'):
            print(f'    {e["human_summary"]}')
        if e.get('rollback_reason'):
            print(f'    ROLLED BACK: {e["rollback_reason"]}')
    return 0


def _extract_flag_value(flags: list, name: str, default: str) -> str:
    for i, f in enumerate(flags):
        if f == f'--{name}' and i + 1 < len(flags):
            return flags[i + 1]
        if f.startswith(f'--{name}='):
            return f.split('=', 1)[1]
    return default


def main():
    args = sys.argv[1:]
    if not args:
        print('Usage: python -m agents.modify_agent <change|rollback|history> <workflow_id> [...]')
        return 1
    command = args[0]
    load_env()

    if command == 'change':
        if len(args) < 2:
            print('Usage: python -m agents.modify_agent change <workflow_id> "<description>" [--edits file] [--dry-run]')
            return 1
        workflow_id = args[1]
        flags = args[2:]
        dry_run = '--dry-run' in flags
        edits_path = _extract_flag_value(flags, 'edits', '')
        # Description is any positional arg that isn't a flag or flag value
        description = ''
        skip_next = False
        for i, a in enumerate(flags):
            if skip_next:
                skip_next = False
                continue
            if a == '--dry-run':
                continue
            if a.startswith('--'):
                if a == '--edits':
                    skip_next = True
                continue
            description = a
            break
        return cmd_change(workflow_id, description, explicit_edits_path=edits_path, dry_run=dry_run)

    if command == 'rollback':
        if len(args) < 2:
            print('Usage: python -m agents.modify_agent rollback <workflow_id> --snapshot <path>')
            return 1
        workflow_id = args[1]
        snapshot_path = _extract_flag_value(args[2:], 'snapshot', '')
        if not snapshot_path:
            print('--snapshot <path> required')
            return 1
        return cmd_rollback(workflow_id, snapshot_path)

    if command == 'history':
        if len(args) < 2:
            print('Usage: python -m agents.modify_agent history <workflow_id>')
            return 1
        return cmd_history(args[1])

    print(f'Unknown command: {command}')
    return 1


if __name__ == '__main__':
    sys.exit(main())

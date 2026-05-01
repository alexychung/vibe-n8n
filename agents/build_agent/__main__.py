"""CLI entry point for the build agent.

Usage:
    python -m build_agent build spec.json             # Full pipeline (+ auto-export)
    python -m build_agent build spec.json --dry-run   # Validate only
    python -m build_agent build spec.json --no-export # Skip EXPORT phase
    python -m build_agent scaffold spec.json          # Single phase
    python -m build_agent validate spec.json          # Parse + validate spec only
    python -m build_agent list                        # List deployed workflows
    python -m build_agent export <wf-id> <spec.json>  # Re-export a deployed workflow
"""
import datetime
import io
import json
import os
import sys
import uuid

# Fix Windows console encoding — topology uses box-drawing glyphs (●│├) and
# generated auth messages can include Unicode from workflow names. Default
# cp1252 codec crashes on these; force UTF-8 to match the PM Agent.
# Only patch when run as the script entry (not when test code imports us via
# importlib — that stomps on pytest's capture wrapper).
if sys.platform == 'win32' and __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Ensure the package directory is on the path
sys.path.insert(0, os.path.dirname(__file__))

from client import N8nClient, N8nApiError
from models import parse_spec, ValidationError
from scaffold import scaffold
from wire import wire
from test_runner import run_tests, render_results
from auditor import audit_workflow, render_findings
from harden import harden
from deploy import deploy
from export import export_workflow, _slugify
from status import BuildStatus


def load_env():
    """Load .env from project root if env vars not set.

    Walks up to find .env. Strips surrounding quotes from values
    (`KEY="value"` → `value`). Tolerates `export KEY=value` and UTF-8 BOM.
    Existing env vars take precedence (uses setdefault).
    """
    if os.environ.get('N8N_API_KEY'):
        return
    d = os.path.dirname(__file__)
    for _ in range(5):
        env_path = os.path.join(d, '.env')
        if os.path.exists(env_path):
            with open(env_path, encoding='utf-8-sig') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    if line.startswith('export '):
                        line = line[7:].lstrip()
                    key, val = line.split('=', 1)
                    val = val.strip()
                    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                        val = val[1:-1]
                    os.environ.setdefault(key.strip(), val)
            return
        d = os.path.dirname(d)


def _project_root():
    d = os.path.dirname(__file__)
    for _ in range(5):
        if os.path.exists(os.path.join(d, '.env')) or os.path.exists(os.path.join(d, 'CLAUDE.md')):
            return d
        d = os.path.dirname(d)
    return os.getcwd()


def log_event(event: dict):
    """Append one JSONL line to build-logs/build-inputs.jsonl. Never raises."""
    try:
        log_path = os.path.join(_project_root(), 'build-logs', 'build-inputs.jsonl')
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False) + '\n')
    except Exception:
        pass


def load_spec(spec_path: str):
    """Load and parse a spec file."""
    try:
        with open(spec_path) as f:
            raw = json.load(f)
    except FileNotFoundError:
        raise SystemExit(f'Spec file not found: {spec_path}')
    except json.JSONDecodeError as e:
        raise SystemExit(f'Invalid JSON in {spec_path}: {e}')
    return parse_spec(raw)


def cmd_build(
    spec_path: str,
    dry_run: bool = False,
    export: bool = True,
    export_dir: str = 'workflows/live',
):
    """Full build pipeline: SCAFFOLD → WIRE → TEST → AUDIT → HARDEN → CODIFY → DEPLOY → EXPORT."""
    session_id = uuid.uuid4().hex[:12]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    spec = load_spec(spec_path)
    status = BuildStatus(spec.workflow_name)

    log_event({
        'ts': now,
        'session_id': session_id,
        'kind': 'input',
        'spec_path': spec_path,
        'workflow_name': spec.workflow_name,
        'step_count': len(spec.steps),
        'gate_count': len(spec.gates),
        'test_case_count': len(spec.test_cases),
        'dry_run': dry_run,
        'export': export,
    })

    def _outcome(status_str: str, **extra):
        log_event({
            'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'session_id': session_id,
            'kind': 'outcome',
            'status': status_str,
            'workflow_name': spec.workflow_name,
            **extra,
        })

    if dry_run:
        print(f'Spec: {spec.workflow_name}')
        print(f'Steps: {len(spec.steps)}, Gates: {len(spec.gates)}, Test cases: {len(spec.test_cases)}')
        print()
        print(_render_topology(spec))
        print('\nDry run complete — no workflow created.')
        _outcome('dry_run')
        return 0

    if not os.environ.get('N8N_API_KEY'):
        print('Error: N8N_API_KEY not set. Add it to .env or set the environment variable.')
        _outcome('missing_api_key')
        return 1

    client = N8nClient()
    workflow_id = None

    try:
        # SCAFFOLD
        workflow_id = scaffold(spec, client)
        status.workflow_id = workflow_id
        status.done('SCAFFOLD', f'{len(spec.steps) + 1} nodes created')
        print(status.render())
        print()

        # WIRE
        wire(spec, client, workflow_id)
        status.done('WIRE', f'{len(spec.steps)} steps configured and connected')
        print(status.render())
        print()

        # TEST
        if spec.trigger.type in ('webhook', 'event') and spec.trigger.path:
            results = run_tests(spec, client, workflow_id)
            passed = sum(1 for r in results if r.passed)
            total = len(results)
            if passed == total:
                status.done('TEST', f'{passed}/{total} test cases pass')
            else:
                status.fail('TEST', f'{passed}/{total} test cases pass')
                print(status.render())
                print()
                print(render_results(results))
                _outcome('test_failed', workflow_id=workflow_id, tests_passed=passed, tests_total=total)
                return 1
        else:
            status.done('TEST', f'skipped (no webhook trigger — verify manually in n8n)')
        print(status.render())
        print()

        # AUDIT
        wf = client.get_workflow(workflow_id)
        findings = audit_workflow(wf)
        critical = sum(1 for f in findings if f.severity == 'CRITICAL')
        warning = sum(1 for f in findings if f.severity == 'WARNING')
        info = sum(1 for f in findings if f.severity == 'INFO')
        status.done('AUDIT', f'{critical} critical, {warning} warning, {info} info')
        print(status.render())
        print()

        # HARDEN
        unfixed_warnings: list = []
        generated_auth: list = []
        if critical > 0 or warning > 0:
            harden_result = harden(client, workflow_id, workflow_name=spec.workflow_name)
            final_findings = harden_result.findings
            generated_auth = harden_result.generated_auth
            remaining_critical = sum(1 for f in final_findings if f.severity == 'CRITICAL')
            remaining_warning = sum(1 for f in final_findings if f.severity == 'WARNING')
            fixed = warning - remaining_warning
            if remaining_critical > 0:
                status.fail('HARDEN', f'{remaining_critical} critical remain')
                print(status.render())
                print()
                print(render_findings(final_findings))
                _outcome('harden_failed', workflow_id=workflow_id, critical_remaining=remaining_critical)
                return 1
            else:
                msg = f'Fixed {fixed}/{warning} warnings'
                if generated_auth:
                    msg += f' (incl. {len(generated_auth)} webhook auth credential{"s" if len(generated_auth) > 1 else ""})'
                if remaining_warning > 0:
                    msg += f'; {remaining_warning} need human review'
                status.done('HARDEN', msg)
                unfixed_warnings = [f for f in final_findings if f.severity == 'WARNING']
        else:
            status.done('HARDEN', 'No findings to fix')
        print(status.render())
        print()

        if generated_auth:
            auth_log_path = _write_auth_log(spec.workflow_name, generated_auth)
            print(f'Webhook auth credentials created for {len(generated_auth)} node(s).')
            for a in generated_auth:
                print(f'  - node "{a.node_name}": header {a.header_name}')
            print(f'Tokens written to {auth_log_path} (chmod 600). Inspect the file to retrieve them — they are not recoverable.')
            print()

        if unfixed_warnings:
            print('Remaining warnings (not auto-fixable — require human action):')
            print(render_findings(unfixed_warnings))
            print()

        # CODIFY
        status.skip('CODIFY', 'deferred')
        print(status.render())
        print()

        # DEPLOY
        smoke_headers = {generated_auth[0].header_name: generated_auth[0].token} if generated_auth else None
        deploy_result = deploy(spec, client, workflow_id, webhook_headers=smoke_headers)
        if deploy_result['smoke_test_passed']:
            status.done('DEPLOY', f'Workflow {workflow_id} active, smoke test passed')
        else:
            status.done('DEPLOY', f'Workflow {workflow_id} active, smoke test skipped or failed')
        print(status.render())
        print()

        # EXPORT
        if export:
            try:
                result = export_workflow(
                    spec,
                    client,
                    workflow_id,
                    output_dir=export_dir,
                    generated_auth=generated_auth,
                )
                status.done(
                    'EXPORT',
                    f"{result['json_path']} + README ({result['node_count']} nodes)",
                )
            except Exception as e:
                status.fail('EXPORT', f'{type(e).__name__}: {e}')
        else:
            status.skip('EXPORT', '--no-export')
        print(status.render())
        print()

        print(f'Workflow deployed: {workflow_id}')
        _outcome(
            'success',
            workflow_id=workflow_id,
            smoke_test_passed=bool(deploy_result.get('smoke_test_passed')),
            unfixed_warnings=len(unfixed_warnings),
            generated_auth_count=len(generated_auth),
        )
        return 0

    except Exception as e:
        _outcome(
            'error',
            workflow_id=workflow_id,
            error_type=type(e).__name__,
            error=str(e),
        )
        if workflow_id:
            print(f'\nBuild failed at workflow {workflow_id}.')
            try:
                client.deactivate_workflow(workflow_id)
            except Exception:
                pass
            try:
                client.delete_workflow(workflow_id)
                print(f'Cleaned up failed workflow {workflow_id}.')
            except Exception:
                print(f'Could not clean up workflow {workflow_id} — delete it manually.')
        raise


def cmd_single_phase(phase: str, spec_path: str):
    """Run a single phase for debugging."""
    spec = load_spec(spec_path)
    client = N8nClient()

    if phase == 'scaffold':
        wf_id = scaffold(spec, client)
        print(f'Scaffolded: {wf_id}')
    elif phase == 'validate':
        print(f'Spec valid: {spec.workflow_name}')
        print(f'  {len(spec.steps)} steps, {len(spec.gates)} gates, {len(spec.test_cases)} tests')
    else:
        print(f'Unknown phase: {phase}')
        return 1
    return 0


def cmd_list(as_json: bool = False):
    """List all deployed workflows with IDs, names, active state, and slugs."""
    if not os.environ.get('N8N_API_KEY'):
        print('Error: N8N_API_KEY not set. Add it to .env or set the environment variable.')
        return 1

    client = N8nClient()
    workflows = client.list_workflows()

    if as_json:
        print(json.dumps([
            {
                'id': w.get('id', ''),
                'name': w.get('name', ''),
                'active': bool(w.get('active')),
                'slug': _slugify(w.get('name', '')),
            }
            for w in workflows
        ], indent=2))
        return 0

    if not workflows:
        print('No workflows deployed.')
        return 0

    # Column widths computed from data, capped for sanity
    id_w = max(len('ID'), max(len(w.get('id', '')) for w in workflows))
    name_w = min(50, max(len('Name'), max(len(w.get('name', '')) for w in workflows)))
    slugs = [_slugify(w.get('name', '')) for w in workflows]
    slug_w = min(40, max(len('Slug'), max(len(s) for s in slugs)))

    header = f'{"ID":<{id_w}}  {"Active":<6}  {"Name":<{name_w}}  {"Slug":<{slug_w}}'
    print(header)
    print('-' * len(header))
    for w, slug in zip(workflows, slugs):
        name = (w.get('name', '') or '')[:name_w]
        slug_out = slug[:slug_w]
        active = 'yes' if w.get('active') else 'no'
        print(f'{w.get("id", ""):<{id_w}}  {active:<6}  {name:<{name_w}}  {slug_out:<{slug_w}}')

    print(f'\n{len(workflows)} workflow(s)')
    return 0


def _write_auth_log(workflow_name: str, generated_auth: list, log_dir: str = 'build-logs') -> str:
    """Persist generated webhook-auth tokens to a gitignored log file.

    Writes a dotenv-style file that's easy to source or read. Path:
    `{log_dir}/{slug}-auth.env`. Overwrites an existing file — on re-builds
    the old tokens would no longer be attached to the workflow's nodes anyway.
    """
    os.makedirs(log_dir, exist_ok=True)
    slug = _slugify(workflow_name)
    path = os.path.join(log_dir, f'{slug}-auth.env')
    lines = [
        f'# Webhook auth tokens for: {workflow_name}',
        '# Generated during HARDEN — save these; n8n masks credential values after creation.',
        '',
    ]
    for i, a in enumerate(generated_auth):
        suffix = '' if len(generated_auth) == 1 else f'_{i + 1}'
        lines.append(f'# node: {a.node_name}')
        lines.append(f'WEBHOOK_AUTH_HEADER{suffix}={a.header_name}')
        lines.append(f'WEBHOOK_AUTH_TOKEN{suffix}={a.token}')
        lines.append('')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    # Tighten permissions so the file isn't world-readable on multi-user
    # systems. No-op on Windows (file ACLs ignore the chmod bits) but real
    # protection on Linux/macOS where these tokens grant webhook access.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _render_topology(spec) -> str:
    """Render a simple ASCII topology of the workflow spec for --dry-run."""
    lines = ['Topology:']
    trigger_info = spec.trigger.type
    details = []
    if spec.trigger.path:
        details.append(f'/{spec.trigger.path}')
    if spec.trigger.method:
        details.append(spec.trigger.method)
    if spec.trigger.schedule:
        details.append(f'cron={spec.trigger.schedule}')
    if details:
        trigger_info += ' (' + ' '.join(details) + ')'
    lines.append(f'  ● {trigger_info}')

    by_id = {s.id: s for s in spec.steps}
    gates_by_step = {g.after_step: g for g in spec.gates}

    def _label(step_id: str) -> str:
        s = by_id.get(step_id)
        return f'{s.name} ({s.node_type})' if s else f'?{step_id}'

    for s in spec.steps:
        lines.append(f'    │')
        lines.append(f'  ├─ [{s.id}] {s.name} ({s.node_type})')
        g = gates_by_step.get(s.id)
        if g:
            if g.pass_to:
                lines.append(f'  │    ├─ pass → {_label(g.pass_to)}')
            if g.fail_to:
                lines.append(f'  │    └─ fail → {_label(g.fail_to)}')

    return '\n'.join(lines)


def cmd_export(workflow_id: str, spec_path: str, export_dir: str = 'workflows/live'):
    """Re-export an already-deployed workflow by ID, using a spec for README context."""
    if not os.environ.get('N8N_API_KEY'):
        print('Error: N8N_API_KEY not set. Add it to .env or set the environment variable.')
        return 1

    spec = load_spec(spec_path)
    client = N8nClient()

    try:
        result = export_workflow(spec, client, workflow_id, output_dir=export_dir)
    except N8nApiError as e:
        print(f'n8n API error: {e}')
        return 1

    print(f'Exported workflow {workflow_id} ({result["node_count"]} nodes):')
    print(f'  {result["json_path"]}')
    print(f'  {result["readme_path"]}')
    return 0


def _extract_flag_value(flags: list, name: str, default: str) -> str:
    """Extract a CLI flag value supporting both --flag value and --flag=value forms."""
    for i, f in enumerate(flags):
        if f == f'--{name}' and i + 1 < len(flags):
            return flags[i + 1]
        if f.startswith(f'--{name}='):
            return f.split('=', 1)[1]
    return default


def main():
    args = sys.argv[1:]
    if not args:
        print('Usage: python -m build_agent <command> [args...]')
        print('Commands: build, scaffold, validate, list, export')
        return 1

    command = args[0]
    load_env()

    try:
        if command == 'list':
            return cmd_list(as_json='--json' in args[1:])

        if command == 'export':
            if len(args) < 3:
                print('Usage: python -m build_agent export <workflow-id> <spec.json> [--export-dir=DIR]')
                return 1
            workflow_id = args[1]
            spec_path = args[2]
            export_dir = _extract_flag_value(args[3:], 'export-dir', 'workflows/live')
            return cmd_export(workflow_id, spec_path, export_dir=export_dir)

        # All remaining commands take a spec path
        if len(args) < 2:
            print(f'Missing spec path for command: {command}')
            return 1
        spec_path = args[1]
        flags = args[2:]

        if command == 'build':
            dry_run = '--dry-run' in flags
            export = '--no-export' not in flags
            export_dir = _extract_flag_value(flags, 'export-dir', 'workflows/live')
            return cmd_build(spec_path, dry_run=dry_run, export=export, export_dir=export_dir)
        elif command in ('scaffold', 'validate'):
            return cmd_single_phase(command, spec_path)
        else:
            print(f'Unknown command: {command}')
            return 1
    except ValidationError as e:
        print(f'Spec validation error: {e}')
        return 1
    except N8nApiError as e:
        print(f'n8n API error: {e}')
        return 1


if __name__ == '__main__':
    sys.exit(main())

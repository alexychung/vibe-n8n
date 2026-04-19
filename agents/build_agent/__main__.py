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
import json
import os
import sys

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
    """Load .env from project root if env vars not set."""
    if os.environ.get('N8N_API_KEY'):
        return
    # Walk up to find .env
    d = os.path.dirname(__file__)
    for _ in range(5):
        env_path = os.path.join(d, '.env')
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, val = line.split('=', 1)
                        os.environ[key.strip()] = val.strip()
            return
        d = os.path.dirname(d)


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
    spec = load_spec(spec_path)
    status = BuildStatus(spec.workflow_name)

    if dry_run:
        print(f'Spec: {spec.workflow_name}')
        print(f'Steps: {len(spec.steps)}, Gates: {len(spec.gates)}, Test cases: {len(spec.test_cases)}')
        print()
        print(_render_topology(spec))
        print('\nDry run complete — no workflow created.')
        return 0

    if not os.environ.get('N8N_API_KEY'):
        print('Error: N8N_API_KEY not set. Add it to .env or set the environment variable.')
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
        if critical > 0 or warning > 0:
            final_findings = harden(client, workflow_id)
            remaining_critical = sum(1 for f in final_findings if f.severity == 'CRITICAL')
            remaining_warning = sum(1 for f in final_findings if f.severity == 'WARNING')
            fixed = warning - remaining_warning
            if remaining_critical > 0:
                status.fail('HARDEN', f'{remaining_critical} critical remain')
                print(status.render())
                print()
                print(render_findings(final_findings))
                return 1
            else:
                msg = f'Fixed {fixed}/{warning} warnings'
                if remaining_warning > 0:
                    msg += f'; {remaining_warning} need human review'
                status.done('HARDEN', msg)
                unfixed_warnings = [f for f in final_findings if f.severity == 'WARNING']
        else:
            status.done('HARDEN', 'No findings to fix')
        print(status.render())
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
        deploy_result = deploy(spec, client, workflow_id)
        if deploy_result['smoke_test_passed']:
            status.done('DEPLOY', f'Workflow {workflow_id} active, smoke test passed')
        else:
            status.done('DEPLOY', f'Workflow {workflow_id} active, smoke test skipped or failed')
        print(status.render())
        print()

        # EXPORT
        if export:
            try:
                result = export_workflow(spec, client, workflow_id, output_dir=export_dir)
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
        return 0

    except Exception as e:
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

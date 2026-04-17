"""CLI entry point for the build agent.

Usage:
    python -m build_agent build spec.json          # Full pipeline
    python -m build_agent scaffold spec.json       # Single phase
    python -m build_agent build spec.json --dry-run  # Validate only
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


def cmd_build(spec_path: str, dry_run: bool = False):
    """Full build pipeline: SCAFFOLD → WIRE → TEST → AUDIT → HARDEN → CODIFY → DEPLOY."""
    spec = load_spec(spec_path)
    status = BuildStatus(spec.workflow_name)

    if dry_run:
        print(f'Spec: {spec.workflow_name}')
        print(f'Trigger: {spec.trigger.type} ({spec.trigger.path})')
        print(f'Steps: {len(spec.steps)}')
        for s in spec.steps:
            print(f'  - {s.name} ({s.node_type})')
        print(f'Gates: {len(spec.gates)}')
        print(f'Test cases: {len(spec.test_cases)}')
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
        if critical > 0 or warning > 0:
            final_findings = harden(client, workflow_id)
            remaining_critical = sum(1 for f in final_findings if f.severity == 'CRITICAL')
            remaining_warning = sum(1 for f in final_findings if f.severity == 'WARNING')
            if remaining_critical > 0:
                status.fail('HARDEN', f'{remaining_critical} critical remain')
                print(status.render())
                print()
                print(render_findings(final_findings))
                return 1
            else:
                status.done('HARDEN', f'Fixed {warning - remaining_warning} warnings')
        else:
            status.done('HARDEN', 'No findings to fix')
        print(status.render())
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


def main():
    args = sys.argv[1:]
    if not args:
        print('Usage: python -m build_agent <command> <spec.json> [--dry-run]')
        print('Commands: build, scaffold, validate')
        return 1

    command = args[0]
    if len(args) < 2:
        print(f'Missing spec path for command: {command}')
        return 1

    spec_path = args[1]
    flags = args[2:]

    load_env()

    try:
        if command == 'build':
            dry_run = '--dry-run' in flags
            return cmd_build(spec_path, dry_run=dry_run)
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

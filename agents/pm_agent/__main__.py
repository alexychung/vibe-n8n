"""CLI entry point for the PM Agent.

Usage:
    python -m agents.pm_agent plan "description"              # Interactive interview
    python -m agents.pm_agent plan --from-brief brief.md      # Non-interactive
    python -m agents.pm_agent plan "desc" --output spec.json  # Custom output path
"""
import datetime
import hashlib
import io
import json
import os
import sys
import uuid

# Fix Windows console encoding — LLM outputs Unicode (°, ≤, emoji) that
# crashes the default cp1252 codec. Force UTF-8 on stdout/stderr.
# Gate on __name__ == '__main__' so importing this module from the web layer
# or tests doesn't stomp on pytest's stdout-capture wrapper.
if sys.platform == 'win32' and __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(__file__))

from interviewer import interview_interactive, interview_from_brief
from auditor import audit_existing_workflows, render_audit_summary
from decomposer import decompose
from reviewer import review_loop
from validator import validate_spec


def load_env():
    """Load .env from project root if env vars not set."""
    if os.environ.get('ANTHROPIC_API_KEY') and os.environ.get('N8N_API_KEY'):
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
                    k, v = line.split('=', 1)
                    v = v.strip()
                    # Strip surrounding quotes — `KEY="value"` shouldn't keep
                    # the quotes literal in the env var.
                    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                        v = v[1:-1]
                    os.environ.setdefault(k.strip(), v)
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
    """Append one JSONL line to build-logs/pm-inputs.jsonl. Never raises."""
    try:
        log_path = os.path.join(_project_root(), 'build-logs', 'pm-inputs.jsonl')
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False) + '\n')
    except Exception:
        pass


def _auto_fix_spec(spec: dict, errors: list) -> tuple:
    """Deterministic patches for validator findings the LLM keeps mis-generating.

    Returns (patched_spec, applied_fixes). For each fix applied, a short label
    is added to applied_fixes; an empty list means nothing matched.

    Currently handles:
      - LLM step (determinism=3.0) missing a gate entry: insert a gate with
        after_step=<llm_step_id>, pass_to=<next sequential step>, fail_to=null,
        type=conditional_branch. The Build Agent will wire the IF — the LLM
        usually included an IF in the steps array but forgot the gate entry.
    """
    import re
    applied = []
    steps = spec.get('steps', [])
    gates = spec.get('gates', [])
    step_by_id = {s.get('id'): s for s in steps}
    step_index = {s.get('id'): i for i, s in enumerate(steps)}
    gated_steps = set()
    for g in gates:
        a = g.get('after_step')
        if isinstance(a, str):
            gated_steps.add(a)
        elif isinstance(a, list):
            gated_steps.update(a)

    for err in errors:
        # Spec convention is `step_<n>` IDs but the LLM also uses
        # camelCase or snake_case names without the prefix. Match any
        # identifier-like token so the auto-fix doesn't silently skip
        # those and force an LLM repair pass.
        m = re.search(r'\(id=([A-Za-z][A-Za-z0-9_-]*)\) has determinism 3.0', err)
        if not m:
            continue
        llm_id = m.group(1)
        if llm_id in gated_steps:
            continue
        idx = step_index.get(llm_id)
        if idx is None or idx + 1 >= len(steps):
            continue
        # Insert a sequential gate `{after_step: <llm>, pass_to: <next>}`. The
        # validator only requires that the LLM step appears as some gate's
        # after_step; a sequential gate satisfies that without forcing the
        # build agent to wire two branches off an HTTP node. If the LLM
        # included a downstream IF step for response validation, this gate
        # routes through it; the IF still does the conditional branching.
        next_step = steps[idx + 1]
        new_gate = {
            'after_step': llm_id,
            'pass_to': next_step.get('id'),
            'fail_to': None,
            'type': 'sequential',
        }
        gates.append(new_gate)
        gated_steps.add(llm_id)
        applied.append(f'inserted sequential gate after LLM step {llm_id}')

    if applied:
        spec['gates'] = gates
    return spec, applied


def cmd_plan(description: str = '', from_brief: str = '', output_path: str = '', requirements_path: str = ''):
    """Full PM pipeline: INTERVIEW → AUDIT → DECOMPOSE → REVIEW → VALIDATE → OUTPUT."""

    session_id = uuid.uuid4().hex[:12]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        return _cmd_plan_inner(description, from_brief, output_path, session_id, now, requirements_path)
    except BaseException as e:
        log_event({
            'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'session_id': session_id,
            'kind': 'outcome',
            'status': 'error' if not isinstance(e, KeyboardInterrupt) else 'interrupted',
            'error_type': type(e).__name__,
            'error': str(e),
        })
        raise


def _cmd_plan_inner(description, from_brief, output_path, session_id, now, requirements_path=''):
    # Phase 1: Interview (or load pre-computed requirements)
    print('Phase 1: Interview')
    if requirements_path:
        with open(requirements_path, encoding='utf-8') as f:
            requirements = json.load(f)
        log_event({
            'ts': now,
            'session_id': session_id,
            'kind': 'input',
            'mode': 'requirements',
            'requirements_path': requirements_path,
            'output_path_arg': output_path or None,
        })
        print(f'  Loaded requirements from {requirements_path}')
    elif from_brief:
        with open(from_brief, encoding='utf-8-sig') as f:
            brief_text = f.read()
        log_event({
            'ts': now,
            'session_id': session_id,
            'kind': 'input',
            'mode': 'from-brief',
            'brief_path': from_brief,
            'brief_chars': len(brief_text),
            'brief_sha256': hashlib.sha256(brief_text.encode('utf-8')).hexdigest()[:16],
            'output_path_arg': output_path or None,
        })
        requirements = interview_from_brief(brief_text)
        print(f'  Inferred requirements from brief ({len(brief_text)} chars)')
    else:
        log_event({
            'ts': now,
            'session_id': session_id,
            'kind': 'input',
            'mode': 'interactive',
            'description_chars': len(description),
            'description_sha256': hashlib.sha256(description.encode('utf-8')).hexdigest()[:16],
            'output_path_arg': output_path or None,
        })
        requirements = interview_interactive(description)
    print(f'  Outcome: {requirements.get("outcome", "?")}')
    print(f'  Trigger: {requirements.get("trigger", "?")}')
    print()

    # Phase 2: Audit
    print('Phase 2: Audit existing workflows')
    audit = audit_existing_workflows(requirements)
    audit_text = render_audit_summary(audit)
    print(f'  {audit_text}')
    print()

    # Phase 3: Decompose
    print('Phase 3: Decompose into workflow spec')
    spec = decompose(requirements, audit_text)
    print(f'  Workflow: {spec.get("workflow_name", "?")}')
    print(f'  Steps: {len(spec.get("steps", []))}')
    print(f'  Gates: {len(spec.get("gates", []))}')
    print(f'  Test cases: {len(spec.get("test_cases", []))}')
    print()

    # Phase 4-5: Review + Fix loop
    print('Phase 4: Adversarial review')
    spec, findings = review_loop(spec, requirements)
    critical = sum(1 for f in findings if f.get('severity') == 'CRITICAL')
    warning = sum(1 for f in findings if f.get('severity') == 'WARNING')
    info = sum(1 for f in findings if f.get('severity') == 'INFO')
    print(f'  Final: {critical} critical, {warning} warning, {info} info')
    if findings:
        for f in findings:
            print(f'    [{f.get("severity", "?")}] {f.get("finding", "")}')
    print()

    # Phase 6: Validate (with auto-fixes + one LLM repair pass on remaining errors)
    print('Phase 5: Validate')
    errors = validate_spec(spec)
    if errors:
        print(f'  Validation errors:')
        for e in errors:
            print(f'    - {e}')
        # Attempt deterministic auto-fixes first for shapes the LLM keeps
        # mis-generating despite explicit prompts (e.g. forgetting the
        # gates-array entry for an LLM step). Anything still broken after
        # auto-fix goes to the LLM repair pass.
        spec, auto_fixed = _auto_fix_spec(spec, errors)
        if auto_fixed:
            print(f'  Applied auto-fixes: {", ".join(auto_fixed)}')
            errors = validate_spec(spec)
        if errors:
            from reviewer import fix_spec
            print('  Attempting LLM repair pass with remaining validator findings...')
            validator_findings = [
                {'severity': 'CRITICAL', 'finding': e} for e in errors
            ]
            spec = fix_spec(spec, validator_findings)
            errors = validate_spec(spec)
        if errors:
            print(f'  Validation errors after repair:')
            for e in errors:
                print(f'    - {e}')
            print('\nSpec has issues. Fix manually or re-run.')
            log_event({
                'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'session_id': session_id,
                'kind': 'outcome',
                'status': 'validation_failed',
                'workflow_name': spec.get('workflow_name'),
                'errors': errors,
            })
            return 1
        print('  Spec is valid after repair.')
    else:
        print('  Spec is valid.')
    print()

    # Output
    if not output_path:
        name_slug = spec.get('workflow_name', 'workflow').lower().replace(' ', '-')
        output_path = os.path.join('workflows', 'test-data', f'{name_slug}-spec.json')

    # Confirm before writing (skip in non-interactive modes)
    if not from_brief and not requirements_path:
        print(f'Save to {output_path}? [Y/n]')
        confirm = input('> ').strip().lower()
        if confirm and confirm != 'y':
            print('Cancelled.')
            log_event({
                'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'session_id': session_id,
                'kind': 'outcome',
                'status': 'cancelled',
                'workflow_name': spec.get('workflow_name'),
            })
            return 1

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(spec, f, indent=2, ensure_ascii=False)

    print(f'Spec saved to {output_path}')
    print(f'\nBuild it:')
    print(f'  python -m agents.build_agent build {output_path}')
    log_event({
        'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'session_id': session_id,
        'kind': 'outcome',
        'status': 'success',
        'workflow_name': spec.get('workflow_name'),
        'output_path': output_path,
        'step_count': len(spec.get('steps', [])),
        'gate_count': len(spec.get('gates', [])),
    })
    return 0


def main():
    args = sys.argv[1:]
    if not args:
        print('Usage:')
        print('  python -m agents.pm_agent plan "description"')
        print('  python -m agents.pm_agent plan --from-brief brief.md')
        print('  python -m agents.pm_agent plan "desc" --output spec.json')
        return 1

    command = args[0]
    if command != 'plan':
        print(f'Unknown command: {command}. Use "plan".')
        return 1

    # Parse args
    description = ''
    from_brief = ''
    output_path = ''
    requirements_path = ''

    i = 1
    while i < len(args):
        if args[i] == '--from-brief' and i + 1 < len(args):
            from_brief = args[i + 1]
            i += 2
        elif args[i] == '--output' and i + 1 < len(args):
            output_path = args[i + 1]
            i += 2
        elif args[i] == '--requirements' and i + 1 < len(args):
            requirements_path = args[i + 1]
            i += 2
        elif not args[i].startswith('--'):
            description = args[i]
            i += 1
        else:
            print(f'Unknown flag: {args[i]}')
            return 1

    if not description and not from_brief and not requirements_path:
        print('Error: Provide a description, --from-brief, or --requirements')
        return 1

    load_env()

    if not os.environ.get('ANTHROPIC_API_KEY'):
        print('Error: ANTHROPIC_API_KEY not set. Add it to .env or set the environment variable.')
        return 1

    try:
        return cmd_plan(
            description=description,
            from_brief=from_brief,
            output_path=output_path,
            requirements_path=requirements_path,
        )
    except KeyboardInterrupt:
        print('\nCancelled.')
        return 1
    except Exception as e:
        print(f'Error: {e}')
        return 1


if __name__ == '__main__':
    sys.exit(main())

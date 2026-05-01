"""TEST phase — run test data through the workflow and verify results.

Activates the workflow, sends each test case via webhook, compares actual
response against expected output, then deactivates.
"""
from dataclasses import dataclass
from typing import Optional

from models import WorkflowSpec, TestCase
from client import N8nClient, N8nApiError


# Counter for spec-contract normalizer fires within a single run_tests() call.
# Each entry maps a fix name (e.g. 'httpStatus_to_http_status') to the number
# of times it papered over a PM Agent shape drift. Cleared at the start of
# run_tests; printed at the end if non-empty. Lets us measure whether the PM
# prompts have stopped emitting drift shapes — if a fix never fires across
# many builds, the corresponding normalizer is a candidate for removal.
NORMALIZER_FIRES: dict[str, int] = {}


def _record_fix(name: str) -> None:
    NORMALIZER_FIRES[name] = NORMALIZER_FIRES.get(name, 0) + 1


@dataclass
class RunResult:
    test_name: str
    passed: bool
    expected: dict
    actual: dict
    error: str = ''


def _normalize_expected(expected: dict) -> dict:
    """Flatten common PM Agent test-case shape drifts.

    Build Agent response shape (see client._webhook_request): flat dict with
    `http_status` (snake_case). PM-generated specs sometimes emit:
      - `httpStatus` (camelCase) instead of `http_status`
      - `body: {...}` nested wrapper around the actual response fields

    Normalize so _match_expected compares against the real shape without each
    caller re-hand-fixing the spec.
    """
    if not isinstance(expected, dict):
        return expected
    exp = dict(expected)
    camel = exp.pop('httpStatus', None)
    if camel is not None and 'http_status' not in exp:
        exp['http_status'] = camel
        _record_fix('httpStatus_to_http_status')
    if isinstance(exp.get('body'), dict):
        body = exp.pop('body')
        for k, v in body.items():
            exp.setdefault(k, v)
        _record_fix('expected_body_flatten')
    return exp


def _match_expected(actual, expected: dict) -> tuple[bool, str]:
    """Check if actual output matches expected.

    Special values in expected:
    - "any non-empty string": matches any non-empty string
    - Exact values must match exactly
    - Only keys present in expected are checked (extra keys in actual are OK)
    """
    if not isinstance(actual, dict):
        return False, f'Expected dict response, got {type(actual).__name__}'
    expected = _normalize_expected(expected)
    for key, exp_val in expected.items():
        if key not in actual:
            return False, f'Missing key: {key}'

        act_val = actual[key]

        if exp_val == 'any non-empty string':
            if not isinstance(act_val, str) or len(act_val) == 0:
                return False, f'{key}: expected non-empty string, got {act_val!r}'
        elif act_val != exp_val:
            return False, f'{key}: expected {exp_val!r}, got {act_val!r}'

    return True, ''


def run_tests(
    spec: WorkflowSpec,
    client: N8nClient,
    workflow_id: str,
    extra_headers: Optional[dict] = None,
) -> list[RunResult]:
    """Run all test cases against the workflow. Returns results.

    Activates the workflow, sends each test case, collects results,
    then deactivates.

    `extra_headers`: passed to every webhook call. Required by the Modify
    Agent when testing a workflow that has webhook auth attached (Build
    Agent's TEST runs before HARDEN adds auth, so it doesn't need this).
    """
    if not spec.test_cases:
        raise ValueError('Cannot run tests: spec has no test cases')

    NORMALIZER_FIRES.clear()

    # Determine webhook path from trigger
    webhook_path = spec.trigger.path
    if not webhook_path:
        trigger_type = spec.trigger.type or 'unknown'
        raise ValueError(
            f'Cannot run tests: {trigger_type} trigger has no webhook path. '
            f'Automated testing requires a webhook trigger. '
            f'For cron/manual triggers, skip automated tests and verify manually in n8n.'
        )

    # Activate
    client.activate_workflow(workflow_id)

    method = (spec.trigger.method or 'POST').upper()

    results = []
    try:
        for tc in spec.test_cases:
            result = _run_single_test(client, webhook_path, tc, method, extra_headers)
            results.append(result)
    finally:
        # Always deactivate after testing
        try:
            client.deactivate_workflow(workflow_id)
        except Exception:
            pass

    if NORMALIZER_FIRES:
        fires = ', '.join(f'{k}={v}' for k, v in sorted(NORMALIZER_FIRES.items()))
        print(f'  PM-spec contract normalizers fired during tests — {fires}')

    return results


def _run_single_test(
    client: N8nClient,
    webhook_path: str,
    tc: TestCase,
    method: str = 'POST',
    extra_headers: Optional[dict] = None,
) -> RunResult:
    """Run a single test case and return the result."""
    try:
        if method == 'GET':
            # For GET triggers, the PM Agent commonly writes inputs either
            # wrapped as {query: {...}} or as a flat object. Accept both.
            wrapped = tc.input.get('query') if isinstance(tc.input, dict) else None
            if isinstance(wrapped, dict):
                query = wrapped
                _record_fix('get_input_query_unwrap')
            else:
                query = tc.input if isinstance(tc.input, dict) else {}
            actual = client.send_webhook(webhook_path, method='GET', query=query, headers=extra_headers)
        else:
            actual = client.send_webhook(webhook_path, tc.input, headers=extra_headers)

        passed, reason = _match_expected(actual, tc.expected)

        return RunResult(
            test_name=tc.name,
            passed=passed,
            expected=tc.expected,
            actual=actual,
            error=reason,
        )
    except N8nApiError as e:
        return RunResult(
            test_name=tc.name,
            passed=False,
            expected=tc.expected,
            actual={},
            error=f'API error {e.status_code}: {e.message}',
        )
    except Exception as e:
        return RunResult(
            test_name=tc.name,
            passed=False,
            expected=tc.expected,
            actual={},
            error=str(e),
        )


def render_results(results: list[RunResult]) -> str:
    """Render test results as a markdown table."""
    lines = ['| Test | Result | Details |', '|------|--------|---------|']
    for r in results:
        status = 'PASS' if r.passed else 'FAIL'
        detail = r.error if r.error else 'OK'
        lines.append(f'| {r.test_name} | {status} | {detail} |')

    passed = sum(1 for r in results if r.passed)
    lines.append(f'\n{passed}/{len(results)} passed')
    return '\n'.join(lines)

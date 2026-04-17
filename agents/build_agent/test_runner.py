"""TEST phase — run test data through the workflow and verify results.

Activates the workflow, sends each test case via webhook, compares actual
response against expected output, then deactivates.
"""
from dataclasses import dataclass

from models import WorkflowSpec, TestCase
from client import N8nClient, N8nApiError


@dataclass
class RunResult:
    test_name: str
    passed: bool
    expected: dict
    actual: dict
    error: str = ''


def _match_expected(actual, expected: dict) -> tuple[bool, str]:
    """Check if actual output matches expected.

    Special values in expected:
    - "any non-empty string": matches any non-empty string
    - Exact values must match exactly
    - Only keys present in expected are checked (extra keys in actual are OK)
    """
    if not isinstance(actual, dict):
        return False, f'Expected dict response, got {type(actual).__name__}'
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
) -> list[RunResult]:
    """Run all test cases against the workflow. Returns results.

    Activates the workflow, sends each test case, collects results,
    then deactivates.
    """
    if not spec.test_cases:
        raise ValueError('Cannot run tests: spec has no test cases')

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

    results = []
    try:
        for tc in spec.test_cases:
            result = _run_single_test(client, webhook_path, tc)
            results.append(result)
    finally:
        # Always deactivate after testing
        try:
            client.deactivate_workflow(workflow_id)
        except Exception:
            pass

    return results


def _run_single_test(client: N8nClient, webhook_path: str, tc: TestCase) -> RunResult:
    """Run a single test case and return the result."""
    try:
        actual = client.send_webhook(webhook_path, tc.input)

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

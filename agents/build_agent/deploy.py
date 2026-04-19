"""DEPLOY phase — activate workflow and run smoke test."""
from typing import Optional

from client import N8nClient, N8nApiError
from models import WorkflowSpec
from test_runner import _match_expected


def deploy(
    spec: WorkflowSpec,
    client: N8nClient,
    workflow_id: str,
    webhook_headers: Optional[dict] = None,
) -> dict:
    """Activate the workflow and run a smoke test.

    `webhook_headers`: extra headers for the smoke-test webhook call. Required
    when HARDEN has added auth to the webhook node, otherwise the smoke test
    will 401/403 on an otherwise-healthy deployment.

    Returns dict with deployment details.
    """
    # Activate
    active = False
    activate_error = ''
    try:
        result = client.activate_workflow(workflow_id)
        active = result.get('active', False)
    except N8nApiError as e:
        activate_error = str(e)

    # Smoke test: only if workflow is active and we have test cases + webhook path
    smoke_result = None
    smoke_passed = False
    smoke_error = ''
    if not active:
        smoke_error = activate_error or 'Workflow failed to activate'
    elif spec.test_cases and spec.trigger.path:
        first_tc = spec.test_cases[0]
        try:
            smoke_result = client.send_webhook(
                spec.trigger.path,
                first_tc.input,
                headers=webhook_headers,
            )
            smoke_passed, smoke_error = _match_expected(smoke_result, first_tc.expected)
        except N8nApiError as e:
            smoke_error = str(e)

    return {
        'workflow_id': workflow_id,
        'active': active,
        'smoke_test_passed': smoke_passed,
        'smoke_test_result': smoke_result,
        'smoke_test_error': smoke_error,
    }

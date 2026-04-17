"""HARDEN phase — fix audit findings and re-audit.

Loops: audit → fix → re-audit until no CRITICALs/WARNINGs remain (max 3 iterations).
"""
from client import N8nClient
from auditor import audit_workflow, Finding


def harden(client: N8nClient, workflow_id: str, max_iterations: int = 3) -> list[Finding]:
    """Fix audit findings in a loop. Returns final findings list.

    Each iteration: audit → apply fixes → re-audit.
    Stops when no CRITICAL or WARNING findings remain, or max iterations reached.
    """
    for iteration in range(max_iterations):
        wf = client.get_workflow(workflow_id)
        findings = audit_workflow(wf)

        actionable = [f for f in findings if f.severity in ('CRITICAL', 'WARNING')]
        if not actionable:
            return findings  # Clean — only INFO remaining

        # Apply automated fixes (bind actionable via default arg to avoid
        # late-binding closure capturing the wrong iteration's list)
        def apply_fixes(wf: dict, _findings=actionable) -> dict:
            for finding in _findings:
                _apply_fix(wf, finding)
            return wf

        client.update_workflow(workflow_id, apply_fixes)

    # Final audit after all iterations
    wf = client.get_workflow(workflow_id)
    return audit_workflow(wf)


def _apply_fix(wf: dict, finding: Finding):
    """Apply an automated fix for a finding. Modifies wf in place."""
    if finding.check == 'no_timeout':
        wf.setdefault('settings', {})['executionTimeout'] = 300

    elif finding.check == 'no_error_save':
        wf.setdefault('settings', {})['saveDataErrorExecution'] = 'all'

    elif finding.check == 'missing_retry':
        # Add retry to HTTP request nodes
        for node in wf.get('nodes', []):
            if node.get('type') == 'n8n-nodes-base.httpRequest':
                node.setdefault('parameters', {}).setdefault('options', {})
                node['parameters']['options']['retry'] = {
                    'retryOnFail': True,
                    'maxTries': 3,
                    'waitBetweenTries': 1000,
                }

    # Findings that can't be auto-fixed (hardcoded creds, missing webhook auth,
    # default names) are left for human review. They'll still appear in the
    # final findings list.

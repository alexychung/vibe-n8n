"""HARDEN phase — fix audit findings and re-audit.

Loops: audit → fix → re-audit until no CRITICALs/WARNINGs remain (max 3 iterations).

Returns a HardenResult with the final findings plus any auth credentials created
during the run (so callers can thread the token into smoke tests and export docs).
"""
import os
import secrets
from dataclasses import dataclass, field

from client import N8nClient
from auditor import audit_workflow, Finding


WEBHOOK_AUTH_HEADER = 'X-Webhook-Auth'


@dataclass
class GeneratedAuth:
    """Auth credential created during harden to secure a webhook node."""
    node_name: str
    header_name: str
    token: str
    credential_id: str
    credential_name: str


@dataclass
class HardenResult:
    findings: list[Finding]
    generated_auth: list[GeneratedAuth] = field(default_factory=list)


def harden(
    client: N8nClient,
    workflow_id: str,
    max_iterations: int = 3,
    workflow_name: str = '',
    disable_credential_creation: bool = False,
) -> HardenResult:
    """Fix audit findings in a loop. Returns HardenResult.

    Each iteration: audit → (create credentials for webhook auth) → apply fixes → re-audit.
    Stops when no CRITICAL or WARNING findings remain, or max iterations reached.

    `disable_credential_creation`: when True (or env MODIFY_MODE=1), skip the
    webhook-auth credential-creation path — leave `missing_webhook_auth`
    findings in place rather than rotating credentials. The Modify Agent sets
    this so it can't silently rotate tokens out from under live callers.
    """
    if not disable_credential_creation:
        disable_credential_creation = os.environ.get('MODIFY_MODE') == '1'

    generated_auth: list[GeneratedAuth] = []

    for iteration in range(max_iterations):
        wf = client.get_workflow(workflow_id)
        findings = audit_workflow(wf)

        actionable = [f for f in findings if f.severity in ('CRITICAL', 'WARNING')]
        if not actionable:
            return HardenResult(findings=findings, generated_auth=generated_auth)

        # Pre-fix side effects: create header-auth credentials for every
        # webhook node flagged with missing_webhook_auth. Done before the
        # in-place patch so the credential IDs can be wired into node params.
        # Skipped in modify mode to preserve existing webhook auth contracts.
        if disable_credential_creation:
            auth_assignments: list[GeneratedAuth] = []
        else:
            name_hint = workflow_name or wf.get('name', 'workflow')
            auth_assignments = _create_webhook_auth_credentials(client, wf, actionable, name_hint)
        generated_auth.extend(auth_assignments)

        auth_by_node = {a.node_name: a for a in auth_assignments}

        # In modify mode, drop missing_webhook_auth from the fix list so we
        # don't half-attach credentials. The finding stays in the final result.
        if disable_credential_creation:
            fixable = [f for f in actionable if f.check != 'missing_webhook_auth']
        else:
            fixable = actionable

        if not fixable:
            # Nothing left to fix this iteration; re-audit would loop forever.
            return HardenResult(findings=findings, generated_auth=generated_auth)

        def apply_fixes(wf: dict, _findings=fixable, _auth=auth_by_node) -> dict:
            for finding in _findings:
                _apply_fix(wf, finding, _auth)
            return wf

        client.update_workflow(workflow_id, apply_fixes)

    # Final audit after all iterations
    wf = client.get_workflow(workflow_id)
    return HardenResult(findings=audit_workflow(wf), generated_auth=generated_auth)


def _create_webhook_auth_credentials(
    client: N8nClient,
    wf: dict,
    findings: list[Finding],
    workflow_name: str,
) -> list[GeneratedAuth]:
    """Create an httpHeaderAuth credential per webhook node missing auth.

    Returns one GeneratedAuth per credential created. Node mutation (setting
    `authentication='headerAuth'` and `credentials.httpHeaderAuth`) happens
    later in _apply_fix; this function only creates credentials via the API.
    """
    has_webhook_auth_finding = any(
        f.check == 'missing_webhook_auth' for f in findings
    )
    if not has_webhook_auth_finding:
        return []

    results: list[GeneratedAuth] = []
    for node in wf.get('nodes', []):
        if 'webhook' not in node.get('type', '').lower():
            continue
        params = node.get('parameters', {})
        auth = params.get('authentication', '')
        if auth and auth != 'none':
            continue

        token = secrets.token_urlsafe(32)
        cred_name = f'{workflow_name} — {node["name"]} auth'
        created = client.create_credential(
            name=cred_name,
            type='httpHeaderAuth',
            data={'name': WEBHOOK_AUTH_HEADER, 'value': token},
        )
        results.append(GeneratedAuth(
            node_name=node['name'],
            header_name=WEBHOOK_AUTH_HEADER,
            token=token,
            credential_id=created.get('id', ''),
            credential_name=created.get('name', cred_name),
        ))
    return results


def _apply_fix(wf: dict, finding: Finding, auth_by_node: dict | None = None):
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

    elif finding.check == 'missing_webhook_auth' and auth_by_node:
        # Attach the pre-created httpHeaderAuth credential to every webhook
        # node that has an assignment. Safe to run once per finding — the
        # per-node loop is idempotent (same credential re-attached).
        for node in wf.get('nodes', []):
            if 'webhook' not in node.get('type', '').lower():
                continue
            assignment = auth_by_node.get(node['name'])
            if not assignment:
                continue
            node.setdefault('parameters', {})['authentication'] = 'headerAuth'
            node.setdefault('credentials', {})['httpHeaderAuth'] = {
                'id': assignment.credential_id,
                'name': assignment.credential_name,
            }

    # Findings that still can't be auto-fixed (hardcoded creds, default names)
    # are left for human review. They'll still appear in the final findings list.

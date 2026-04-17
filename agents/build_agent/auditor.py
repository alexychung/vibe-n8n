"""AUDIT phase — deterministic checks on workflow JSON.

Three audit categories: Security, Best Practices, Resilience.
Each check returns findings categorized as CRITICAL, WARNING, or INFO.
"""
import json
import re
from dataclasses import dataclass


@dataclass
class Finding:
    category: str  # security, best_practices, resilience
    severity: str  # CRITICAL, WARNING, INFO
    check: str
    message: str


def audit_workflow(workflow: dict) -> list[Finding]:
    """Run all three audit categories on a workflow. Returns findings."""
    findings = []
    findings.extend(_audit_security(workflow))
    findings.extend(_audit_best_practices(workflow))
    findings.extend(_audit_resilience(workflow))
    return findings


def _params_to_str(params: dict) -> str:
    """Serialize parameters to a string for scanning.

    Uses json.dumps instead of str() to preserve structure and avoid
    missing values inside nested dicts.
    """
    try:
        return json.dumps(params)
    except (TypeError, ValueError):
        return str(params)


def _audit_security(wf: dict) -> list[Finding]:
    """Check for credential exposure, missing auth, data leakage."""
    findings = []
    nodes = wf.get('nodes', [])

    # Check for hardcoded credentials in parameters.
    # Patterns are intentionally conservative — prefer flagging something
    # suspicious over missing a real credential.
    secret_patterns = [
        (r'sk-[a-zA-Z0-9]{20,}', 'OpenAI/API key'),
        (r'xox[bpsa]-[a-zA-Z0-9]{10,}', 'Slack token'),
        (r'ghp_[a-zA-Z0-9]{30,}', 'GitHub PAT'),
        (r'eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}', 'JWT'),
        (r'AKIA[A-Z0-9]{16}', 'AWS access key'),
        (r'AIza[a-zA-Z0-9_-]{35}', 'Google API key'),
    ]

    for node in nodes:
        params_str = _params_to_str(node.get('parameters', {}))
        for pattern, label in secret_patterns:
            if re.search(pattern, params_str):
                findings.append(Finding(
                    category='security',
                    severity='CRITICAL',
                    check='hardcoded_credentials',
                    message=f'Node "{node["name"]}": possible {label} detected in parameters',
                ))
                break

    # Check webhook nodes for authentication
    for node in nodes:
        if 'webhook' in node.get('type', '').lower():
            auth = node.get('parameters', {}).get('authentication', '')
            if not auth or auth == 'none':
                findings.append(Finding(
                    category='security',
                    severity='WARNING',
                    check='missing_webhook_auth',
                    message=f'Node "{node["name"]}": webhook has no authentication configured',
                ))

    # Check for password values (not field names) in parameters.
    # Only flag when a key like "password" or "secret" has a literal string
    # value that isn't an n8n expression.
    _sensitive_keys = re.compile(r'\b(password|secret|api_?key|token|auth)\b', re.IGNORECASE)
    for node in nodes:
        _scan_for_sensitive_values(node.get('parameters', {}), node['name'], findings)

    return findings


def _scan_for_sensitive_values(obj, node_name: str, findings: list[Finding], path: str = ''):
    """Recursively scan parameters for sensitive key/value pairs."""
    _sensitive_keys = re.compile(r'^(password|secret|api_?key|private_?key)$', re.IGNORECASE)
    if isinstance(obj, dict):
        for key, val in obj.items():
            current_path = f'{path}.{key}' if path else key
            if _sensitive_keys.match(key) and isinstance(val, str) and val and not val.startswith('={{'):
                findings.append(Finding(
                    category='security',
                    severity='WARNING',
                    check='credential_in_expression',
                    message=f'Node "{node_name}": sensitive key "{current_path}" has a literal value — use n8n credentials or an expression',
                ))
            else:
                _scan_for_sensitive_values(val, node_name, findings, current_path)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _scan_for_sensitive_values(item, node_name, findings, f'{path}[{i}]')


def _audit_best_practices(wf: dict) -> list[Finding]:
    """Check naming, error handling, timeouts, etc."""
    findings = []
    nodes = wf.get('nodes', [])
    settings = wf.get('settings', {})

    # Check for default node names
    default_names = {'HTTP Request', 'IF', 'Code', 'Set', 'Switch', 'Function'}
    for node in nodes:
        if node['name'] in default_names:
            findings.append(Finding(
                category='best_practices',
                severity='WARNING',
                check='default_node_name',
                message=f'Node "{node["name"]}": uses default name — rename to be descriptive',
            ))

    # Check workflow timeout
    timeout = settings.get('executionTimeout', 0)
    if timeout == 0 or timeout is None:
        findings.append(Finding(
            category='best_practices',
            severity='WARNING',
            check='no_timeout',
            message='Workflow has no execution timeout set — could run forever',
        ))

    # Check node count
    if len(nodes) > 30:
        findings.append(Finding(
            category='best_practices',
            severity='INFO',
            check='too_many_nodes',
            message=f'Workflow has {len(nodes)} nodes — consider splitting',
        ))

    # Check that HTTP nodes have retry
    for node in nodes:
        if node.get('type', '') == 'n8n-nodes-base.httpRequest':
            retry = node.get('parameters', {}).get('options', {}).get('retry', {})
            if not retry:
                findings.append(Finding(
                    category='best_practices',
                    severity='WARNING',
                    check='missing_retry',
                    message=f'Node "{node["name"]}": HTTP request has no retry configured',
                ))

    return findings


def _audit_resilience(wf: dict) -> list[Finding]:
    """Check error handling, idempotency, alerting."""
    findings = []
    nodes = wf.get('nodes', [])
    connections = wf.get('connections', {})

    # Check if error paths exist (any node with multiple outputs)
    has_error_path = False
    for node_name, conn in connections.items():
        outputs = conn.get('main', [])
        if len(outputs) > 1:
            has_error_path = True
            break

    if not has_error_path and len(nodes) > 2:
        findings.append(Finding(
            category='resilience',
            severity='WARNING',
            check='no_error_paths',
            message='Workflow has no branching/error paths — all failures will be unhandled',
        ))

    # Check for save settings
    settings = wf.get('settings', {})
    if not settings.get('saveDataErrorExecution'):
        findings.append(Finding(
            category='resilience',
            severity='INFO',
            check='no_error_save',
            message='Error execution data is not being saved — harder to debug failures',
        ))

    return findings


def render_findings(findings: list[Finding]) -> str:
    """Render audit findings as a markdown table."""
    if not findings:
        return 'No findings.'

    lines = ['| # | Category | Severity | Check | Message |',
             '|---|----------|----------|-------|---------|']
    for i, f in enumerate(findings, 1):
        lines.append(f'| {i} | {f.category} | {f.severity} | {f.check} | {f.message} |')

    critical = sum(1 for f in findings if f.severity == 'CRITICAL')
    warning = sum(1 for f in findings if f.severity == 'WARNING')
    info = sum(1 for f in findings if f.severity == 'INFO')
    lines.append(f'\nSummary: {critical} critical, {warning} warning, {info} info')

    return '\n'.join(lines)

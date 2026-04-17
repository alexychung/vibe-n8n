"""AUDIT phase — check existing n8n workflows for conflicts and reuse.

Read-only. Uses the build agent's N8nClient.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'build_agent'))

from client import N8nClient, N8nApiError


def audit_existing_workflows(requirements: dict) -> dict:
    """Audit existing n8n workflows for conflicts with the planned workflow.

    Returns a summary dict with workflow count, potential conflicts, and reuse.
    """
    try:
        client = N8nClient()
        workflows = client.list_workflows()
    except (N8nApiError, Exception) as e:
        return {
            'workflow_count': 0,
            'workflows': [],
            'conflicts': [],
            'reuse_opportunities': [],
            'error': str(e),
        }

    summaries = []
    conflicts = []
    reuse = []

    planned_trigger = requirements.get('trigger', '')
    planned_systems = requirements.get('systems', [])

    for wf in workflows:
        nodes = wf.get('nodes', [])
        trigger_types = [n['type'] for n in nodes if 'trigger' in n.get('type', '').lower() or 'webhook' in n.get('type', '').lower()]

        summary = {
            'name': wf.get('name', ''),
            'id': wf.get('id', ''),
            'active': wf.get('active', False),
            'node_count': len(nodes),
            'trigger_types': trigger_types,
        }
        summaries.append(summary)

        # Check for trigger conflicts (same webhook path)
        for node in nodes:
            if 'webhook' in node.get('type', '').lower():
                existing_path = node.get('parameters', {}).get('path', '')
                if existing_path and planned_trigger == 'webhook':
                    conflicts.append(f'Workflow "{wf["name"]}" uses webhook path "{existing_path}"')

    return {
        'workflow_count': len(workflows),
        'workflows': summaries,
        'conflicts': conflicts,
        'reuse_opportunities': reuse,
    }


def render_audit_summary(audit: dict) -> str:
    """Render audit results as a string for prompt injection."""
    lines = [f'Existing workflows: {audit["workflow_count"]}']
    if audit.get('error'):
        lines.append(f'Warning: Could not connect to n8n: {audit["error"]}')
    for wf in audit.get('workflows', []):
        status = 'active' if wf['active'] else 'inactive'
        lines.append(f'  - {wf["name"]} ({status}, {wf["node_count"]} nodes)')
    if audit['conflicts']:
        lines.append('Potential conflicts:')
        for c in audit['conflicts']:
            lines.append(f'  - {c}')
    else:
        lines.append('No conflicts detected.')
    return '\n'.join(lines)

"""EXPORT phase — write deployed workflow as portable JSON + README.

Produces two files per build:
  {output_dir}/{slug}.json         — importable workflow JSON (no credentials, no IDs)
  {output_dir}/{slug}.README.md    — trigger summary, required credentials, import steps

Callers can ship these two files to anyone with their own n8n instance.
"""
import json
import os
import re
from typing import Optional

from client import N8nClient
from models import WorkflowSpec


PORTABLE_FIELDS = ('name', 'nodes', 'connections', 'settings')


def _slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return s or 'workflow'


def _portable_workflow(wf: dict) -> dict:
    return {k: wf[k] for k in PORTABLE_FIELDS if k in wf}


def _format_credential(c) -> str:
    if isinstance(c, str):
        return f'- `{c}`'
    if isinstance(c, dict):
        name = c.get('name') or c.get('service') or c.get('type') or 'unnamed'
        desc = c.get('description') or c.get('purpose') or ''
        return f'- `{name}` — {desc}' if desc else f'- `{name}`'
    return f'- `{c}`'


def _render_auth_block(generated_auth: Optional[list]) -> str:
    """Render the 'Calling the Webhook' section when auth was auto-generated.

    The token itself is NOT included in the README — READMEs live in version
    control alongside the portable JSON. Tokens are stored in build-logs/.
    """
    if not generated_auth:
        return ''
    header_names = sorted({a.header_name for a in generated_auth})
    headers_desc = ', '.join(f'`{h}`' for h in header_names)
    return (
        '## Calling the Webhook\n\n'
        f'This workflow requires header-based auth on its webhook: {headers_desc}.\n\n'
        'Tokens were generated during HARDEN and stored in '
        '`build-logs/{slug}-auth.env` — they are not included here because\n'
        'this README is meant to be checked in. When importing into another\n'
        'n8n instance, create a new `Header Auth` credential (Settings →\n'
        'Credentials → New → Header Auth) and point the webhook node at it.\n\n'
        'Example request:\n\n'
        '```bash\n'
        f'curl -X POST "$N8N_BASE_URL/webhook/{{path}}" \\\n'
        f'  -H "{header_names[0]}: $WEBHOOK_AUTH_TOKEN" \\\n'
        '  -H "Content-Type: application/json" \\\n'
        '  -d \'{"your": "payload"}\'\n'
        '```\n\n'
    )


def _render_readme(
    spec: WorkflowSpec,
    portable: dict,
    filename: str,
    generated_auth: Optional[list] = None,
) -> str:
    credentials = []
    if isinstance(spec.security, dict):
        credentials = spec.security.get('credentials_needed') or []

    if credentials:
        creds_block = (
            '## Required Credentials\n\n'
            'Create these in n8n (Settings → Credentials) before activating:\n\n'
            + '\n'.join(_format_credential(c) for c in credentials)
            + '\n\n'
        )
    else:
        creds_block = '## Required Credentials\n\nNone.\n\n'

    if generated_auth:
        # The JSON references credential IDs that only exist in the source
        # n8n instance. Tell importers they need to re-create them.
        creds_block += (
            '### Auto-generated webhook auth\n\n'
            'HARDEN auto-created an `httpHeaderAuth` credential per webhook '
            'node to replace the default unauthenticated setup. When importing '
            'this JSON into another n8n instance, re-create a **Header Auth** '
            'credential and re-point the webhook node(s) at it.\n\n'
        )

    trigger = spec.trigger
    trigger_desc = trigger.type or 'unknown'
    if trigger.path:
        trigger_desc += f' at `/{trigger.path}`'
    if trigger.method:
        trigger_desc += f' ({trigger.method})'
    if trigger.schedule:
        trigger_desc += f' on schedule `{trigger.schedule}`'

    description = spec.description.strip() if spec.description else '_(no description)_'
    node_count = len(portable.get('nodes', []))

    return (
        f'# {spec.workflow_name}\n\n'
        f'{description}\n\n'
        f'- **Trigger:** {trigger_desc}\n'
        f'- **Nodes:** {node_count}\n\n'
        f'{creds_block}'
        f'{_render_auth_block(generated_auth)}'
        '## Import\n\n'
        '### Via n8n UI\n'
        '1. Open your n8n instance → Workflows → **Import from File**\n'
        f'2. Select `{filename}` — opens on the canvas\n'
        '3. Configure the credentials listed above (if any)\n'
        '4. Toggle **Active**\n\n'
        '### Via API\n'
        '```bash\n'
        'curl -X POST "$N8N_BASE_URL/api/v1/workflows" \\\n'
        '  -H "X-N8N-API-KEY: $N8N_API_KEY" \\\n'
        '  -H "Content-Type: application/json" \\\n'
        f'  --data-binary @{filename}\n'
        '```\n'
    )


def export_workflow(
    spec: WorkflowSpec,
    client: N8nClient,
    workflow_id: str,
    output_dir: str = 'workflows/live',
    generated_auth: Optional[list] = None,
) -> dict:
    """Fetch a deployed workflow and write portable JSON + README.

    `generated_auth`: optional list of GeneratedAuth records from HARDEN; if
    provided, the README documents the auth requirement (but not the tokens).

    Returns {'json_path', 'readme_path', 'slug', 'node_count'}.
    """
    wf = client.get_workflow(workflow_id)
    portable = _portable_workflow(wf)

    slug = _slugify(spec.workflow_name)
    os.makedirs(output_dir, exist_ok=True)

    json_path = os.path.join(output_dir, f'{slug}.json')
    readme_path = os.path.join(output_dir, f'{slug}.README.md')

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(portable, f, indent=2)
        f.write('\n')

    with open(readme_path, 'w', encoding='utf-8') as f:
        f.write(_render_readme(spec, portable, f'{slug}.json', generated_auth=generated_auth))

    return {
        'json_path': json_path,
        'readme_path': readme_path,
        'slug': slug,
        'node_count': len(portable.get('nodes', [])),
    }

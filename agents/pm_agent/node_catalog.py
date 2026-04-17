"""n8n node type catalog — single source of truth.

Used by:
1. render_catalog() → injected into LLM prompts so the decomposer knows what nodes exist
2. translate_params() → converts pseudocode params to exact n8n JSON format

Built from what we learned building the Build Agent. Every format quirk
(IF v2 combinator, Set v3.4 nesting) is captured here.

Translation functions delegate to wire.py in the build agent — one source
of truth for n8n parameter format quirks.
"""
import json
import os
import sys

# Import translation functions from build agent's wire.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'build_agent'))
from wire import _translate_set_params, _translate_if_params

NODE_CATALOG = {
    'n8n-nodes-base.webhook': {
        'typeVersion': 2,
        'when_to_use': 'Receive HTTP requests. Use for webhook-triggered workflows or as a test entry point.',
        'param_example': {
            'path': 'my-webhook',
            'httpMethod': 'POST',
            'responseMode': 'lastNode',
        },
    },
    'n8n-nodes-base.scheduleTrigger': {
        'typeVersion': 1.2,
        'when_to_use': 'Run on a schedule (cron). Use for polling or periodic workflows.',
        'param_example': {
            'rule': {
                'interval': [{'field': 'cronExpression', 'expression': '0 7 * * 1-5'}]
            },
        },
    },
    'n8n-nodes-base.manualTrigger': {
        'typeVersion': 1,
        'when_to_use': 'Manually triggered. Use for testing or one-off runs.',
        'param_example': {},
    },
    'n8n-nodes-base.httpRequest': {
        'typeVersion': 4.2,
        'when_to_use': 'Make HTTP requests to external APIs. Use for any REST API call.',
        'param_example': {
            'url': 'https://api.example.com/data',
            'method': 'GET',
            'options': {
                'retry': {
                    'retryOnFail': True,
                    'maxTries': 3,
                    'waitBetweenTries': 1000,
                },
            },
        },
    },
    'n8n-nodes-base.set': {
        'typeVersion': 3.4,
        'when_to_use': 'Set or transform fields. Use to build response objects, rename fields, add computed values.',
        'param_example': {
            'assignments': {
                'assignments': [
                    {'id': 'a1', 'name': 'status', 'value': 'ok', 'type': 'string'},
                    {'id': 'a2', 'name': 'data', 'value': '={{ $json.body.field }}', 'type': 'string'},
                ]
            }
        },
    },
    'n8n-nodes-base.if': {
        'typeVersion': 2,
        'when_to_use': 'Conditional branching. Use for validation gates, routing, filtering. Output 0 = true, Output 1 = false.',
        'param_example': {
            'conditions': {
                'options': {'caseSensitive': True, 'leftValue': ''},
                'conditions': [
                    {
                        'id': 'cond_0',
                        'leftValue': '={{ $json.body.name }}',
                        'rightValue': '',
                        'operator': {'type': 'string', 'operation': 'notEmpty'},
                    },
                ],
                'combinator': 'and',
            }
        },
    },
    'n8n-nodes-base.code': {
        'typeVersion': 2,
        'when_to_use': 'Run custom JavaScript. Use for complex transformations, parsing, or logic that no built-in node handles.',
        'param_example': {
            'jsCode': 'const items = $input.all();\nreturn items.map(item => ({ json: { result: item.json.value * 2 } }));',
        },
    },
}


def render_catalog() -> str:
    """Render the node catalog as markdown for prompt injection."""
    lines = ['# n8n Node Types Reference\n']
    for node_type, entry in NODE_CATALOG.items():
        lines.append(f'## `{node_type}` (v{entry["typeVersion"]})')
        lines.append(f'**When to use:** {entry["when_to_use"]}')
        lines.append(f'**Parameter format:**')
        lines.append('```json')
        lines.append(json.dumps(entry['param_example'], indent=2))
        lines.append('```')
        lines.append('')
    return '\n'.join(lines)


def translate_params(node_type: str, pseudocode_params: dict) -> dict:
    """Convert pseudocode parameters to exact n8n JSON format.

    Delegates to build agent's wire.py for Set and IF nodes.
    For unknown types, passes through unchanged.
    """
    if node_type == 'n8n-nodes-base.set':
        return _translate_set_params(pseudocode_params)
    elif node_type == 'n8n-nodes-base.if':
        return _translate_if_params(pseudocode_params)
    else:
        return pseudocode_params

"""DECOMPOSE phase — turn requirements into a workflow spec.

Two-layer process:
1. LLM produces structural spec (steps, gates, data flow) with pseudocode params
2. Python translates pseudocode params to exact n8n JSON via node_catalog
"""
import json

from llm import call_json, load_prompt
from node_catalog import render_catalog, translate_params
from validator import validate_spec

DECOMPOSE_MODEL = 'claude-sonnet-4-6'


def decompose(requirements: dict, audit_summary: str) -> dict:
    """Turn requirements + audit into a validated workflow spec.

    Returns the spec dict. Retries once on validation failure.
    """
    catalog_md = render_catalog()
    system_prompt = load_prompt(
        'decompose',
        requirements=json.dumps(requirements, indent=2),
        audit_summary=audit_summary,
        node_catalog=catalog_md,
    )

    # First attempt
    spec = call_json(DECOMPOSE_MODEL, system_prompt, 'Produce the workflow spec JSON now.', max_tokens=32768)
    spec = _unwrap_spec(spec)

    # Translate pseudocode params to n8n format
    spec = _translate_spec_params(spec)

    # Validate
    errors = validate_spec(spec)
    if not errors:
        return spec

    # Retry with errors appended
    retry_msg = (
        f'The spec you produced has validation errors:\n'
        + '\n'.join(f'- {e}' for e in errors)
        + '\n\nFix these errors and return the corrected spec JSON.'
    )
    spec = call_json(DECOMPOSE_MODEL, system_prompt, retry_msg, max_tokens=32768)
    spec = _unwrap_spec(spec)
    spec = _translate_spec_params(spec)

    # Validate again — if still fails, return anyway (caller decides)
    errors = validate_spec(spec)
    if errors:
        print(f'Warning: Spec still has {len(errors)} validation issue(s) after retry:')
        for e in errors:
            print(f'  - {e}')

    return spec


def _unwrap_spec(spec) -> dict:
    """Ensure spec is a dict — LLM sometimes wraps it in a list."""
    if isinstance(spec, dict):
        return spec
    if isinstance(spec, list):
        for item in spec:
            if isinstance(item, dict) and 'workflow_name' in item:
                return item
        if len(spec) == 1 and isinstance(spec[0], dict):
            return spec[0]
        raise ValueError(f'Decompose returned a list of {len(spec)} items, none with workflow_name')
    raise ValueError(f'Decompose returned unexpected type: {type(spec).__name__}')


def _translate_spec_params(spec: dict) -> dict:
    """Translate all step parameters from pseudocode to n8n format."""
    # Strip connections field — build agent infers wiring from step order + gates
    spec.pop('connections', None)

    for step in spec.get('steps', []):
        node_type = step.get('node_type', '')
        params = step.get('parameters', {})
        if params and node_type:
            step['parameters'] = translate_params(node_type, params)
    return spec

"""Phase 2: CLASSIFY — tactical vs structural, with edit list extraction.

Uses an LLM via the PM Agent's llm wrapper. Refuses to classify (and tells
the caller to use --edits) when the SDK or API key is missing.
"""
import json
import os
from dataclasses import dataclass, field

from edits import Edit
from fetcher import ModifyError


CLASSIFY_MODEL = 'claude-sonnet-4-6'


@dataclass
class Classification:
    classification: str  # 'tactical' or 'structural'
    edits: list[Edit] = field(default_factory=list)
    reason: str = ''
    structural_summary: str = ''


def classify(
    change_description: str,
    workflow: dict,
    workflow_id: str,
) -> Classification:
    """Classify the change and extract edits if tactical.

    Raises ModifyError if the LLM is unavailable. Caller can fall back to
    --edits with a hand-written edit list to skip classification.
    """
    call_json = _load_call_json_or_none()
    if call_json is None:
        raise ModifyError(
            'Classifier requires the anthropic SDK and ANTHROPIC_API_KEY. '
            'Install anthropic and set the key, or pass --edits with an '
            'explicit edit list to skip classification.'
        )

    prompt = _render_prompt(change_description, workflow, workflow_id)
    try:
        raw = call_json(
            CLASSIFY_MODEL,
            'You are a precise routing classifier. Output ONLY JSON.',
            prompt,
            max_tokens=4096,
        )
    except Exception as e:
        raise ModifyError(f'Classifier LLM call failed: {e}') from e
    return _parse_response(raw)


def _load_call_json_or_none():
    """Lazy-import the PM Agent's call_json. Returns None if unavailable."""
    if not os.environ.get('ANTHROPIC_API_KEY'):
        return None
    # PM Agent's llm.py lives at agents/pm_agent/llm.py — load it via spec
    # since pm_agent isn't on the path by default.
    import importlib.util
    pm_llm_path = os.path.join(os.path.dirname(__file__), '..', 'pm_agent', 'llm.py')
    pm_llm_path = os.path.abspath(pm_llm_path)
    if not os.path.exists(pm_llm_path):
        return None
    spec = importlib.util.spec_from_file_location('pm_agent_llm', pm_llm_path)
    if spec is None or spec.loader is None:
        return None
    try:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except ImportError:
        # anthropic SDK not installed
        return None
    return getattr(mod, 'call_json', None)


def _render_prompt(change_description: str, workflow: dict, workflow_id: str) -> str:
    template_path = os.path.join(os.path.dirname(__file__), 'prompts', 'classify.md')
    with open(template_path, encoding='utf-8') as f:
        template = f.read()

    nodes_summary = _summarize_nodes(workflow.get('nodes', []))
    settings_summary = json.dumps(workflow.get('settings', {}), indent=2)

    return (
        template
        .replace('{workflow_name}', workflow.get('name', ''))
        .replace('{workflow_id}', workflow_id)
        .replace('{nodes_summary}', nodes_summary)
        .replace('{settings_summary}', settings_summary)
        .replace('{change_description}', change_description)
    )


def _summarize_nodes(nodes: list[dict]) -> str:
    """Render nodes as JSON the LLM can reason about — full parameters included.

    The classifier needs to see actual current values to populate `old_value`
    correctly. Truncating parameters here would make tactical-vs-structural
    decisions worse and force a re-fetch.
    """
    summary = []
    for n in nodes:
        summary.append({
            'id': n.get('id', ''),
            'name': n.get('name', ''),
            'type': n.get('type', ''),
            'parameters': n.get('parameters', {}),
            'credentials': n.get('credentials', {}),
        })
    return json.dumps(summary, indent=2)


def _parse_response(raw: dict) -> Classification:
    """Parse the LLM's JSON into a Classification.

    Robust to a few common shapes the LLM emits:
      - Wrapped in {result: ...} or [...]
      - edits as a list of dicts (expected) or accidentally a dict
    """
    if isinstance(raw, list) and raw:
        raw = raw[0]
    if not isinstance(raw, dict):
        raise ModifyError(f'Classifier returned non-dict response: {type(raw).__name__}')

    if 'classification' not in raw and 'result' in raw and isinstance(raw['result'], dict):
        raw = raw['result']

    cls = raw.get('classification', '').lower()
    if cls not in ('tactical', 'structural'):
        raise ModifyError(f'Classifier returned invalid classification: {cls!r}')

    edits_raw = raw.get('edits', []) or []
    if isinstance(edits_raw, dict):
        edits_raw = [edits_raw]

    edits = [Edit.from_dict(e) for e in edits_raw if isinstance(e, dict)]

    return Classification(
        classification=cls,
        edits=edits,
        reason=raw.get('reason', ''),
        structural_summary=raw.get('structural_summary', ''),
    )

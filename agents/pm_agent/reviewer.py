"""REVIEW phase — adversarial review + fix loop.

1. Review: LLM finds problems in the spec
2. Fix: LLM applies fixes
3. Loop: max 2 iterations until clean
"""
import json

from llm import call_json, load_prompt
from node_catalog import render_catalog, translate_params
from validator import validate_spec

REVIEW_MODEL = 'claude-sonnet-4-6'
FIX_MODEL = 'claude-sonnet-4-6'


def review(spec: dict, requirements: dict) -> list[dict]:
    """Run adversarial review on a spec. Returns list of findings."""
    system_prompt = load_prompt(
        'review',
        requirements=json.dumps(requirements, indent=2),
        spec=json.dumps(spec, indent=2),
    )

    findings = call_json(REVIEW_MODEL, system_prompt, 'Review this spec and return findings as a JSON array.', max_tokens=32768)

    # The LLM should return a list, but handle the case where it wraps in an object
    if isinstance(findings, dict):
        findings = findings.get('findings', [])

    # Normalize: ensure every item is a dict (LLM sometimes returns strings)
    normalized = []
    for f in findings:
        if isinstance(f, dict):
            normalized.append(f)
        elif isinstance(f, str):
            normalized.append({'severity': 'INFO', 'finding': f})
    return normalized


MAX_FINDINGS_PER_FIX = 8  # Limit findings to prevent overwhelming the LLM


def fix_spec(spec: dict, findings: list[dict]) -> dict:
    """Apply review findings to produce an updated spec."""
    actionable = [f for f in findings if f.get('severity') in ('CRITICAL', 'WARNING')]
    if not actionable:
        return spec

    # Prioritize: CRITICALs first, then WARNINGs, capped to prevent LLM overload
    criticals = [f for f in actionable if f.get('severity') == 'CRITICAL']
    warnings = [f for f in actionable if f.get('severity') == 'WARNING']
    prioritized = (criticals + warnings)[:MAX_FINDINGS_PER_FIX]
    if len(actionable) > MAX_FINDINGS_PER_FIX:
        print(f'  (Fixing top {MAX_FINDINGS_PER_FIX} of {len(actionable)} issues this iteration)')

    system_prompt = load_prompt(
        'fix',
        spec=json.dumps(spec, indent=2),
        findings=json.dumps(prioritized, indent=2),
    )

    updated = call_json(FIX_MODEL, system_prompt,
                         'Apply the fixes and return the complete updated spec as a single JSON object. '
                         'The response must be one JSON object with workflow_name, steps, gates, test_cases, etc.',
                         max_tokens=32768)

    # Handle LLM returning a list instead of a dict
    if isinstance(updated, list):
        # Try to find the spec object in the list
        for item in updated:
            if isinstance(item, dict) and 'workflow_name' in item and 'steps' in item:
                updated = item
                break
        else:
            # Single-element list wrap
            if len(updated) == 1 and isinstance(updated[0], dict):
                updated = updated[0]
            else:
                # Last resort: return the original spec unchanged
                print(f'  Warning: fix_spec got a list of {len(updated)} items instead of a spec dict, keeping original spec')
                return spec

    # Sanity check the LLM didn't drop required top-level fields. The list-
    # unwrap branch above already checks `workflow_name` + `steps`; the dict
    # path doesn't, so a response like `{"workflow_name": "..."}` would be
    # accepted as a "fix" that actually destroys the spec.
    if not isinstance(updated, dict) or 'steps' not in updated or not updated.get('steps'):
        print('  Warning: fix_spec response missing or empty steps array, keeping original spec')
        return spec

    # Strip connections field — build agent infers wiring from step order + gates
    updated.pop('connections', None)

    # Re-translate params after LLM modifications
    for step in updated.get('steps', []):
        node_type = step.get('node_type', '')
        params = step.get('parameters', {})
        if params and node_type:
            step['parameters'] = translate_params(node_type, params)

    return updated


def review_loop(spec: dict, requirements: dict, max_iterations: int = 2) -> tuple[dict, list[dict]]:
    """Review → Fix loop. Returns (final_spec, final_findings).

    Loops until no CRITICAL/WARNING findings or max iterations reached.
    """
    current_spec = spec

    last_findings = []
    for iteration in range(max_iterations):
        findings = review(current_spec, requirements)

        actionable = [f for f in findings if f.get('severity') in ('CRITICAL', 'WARNING')]
        if not actionable:
            return current_spec, findings

        print(f'  Review iteration {iteration + 1}: {len(actionable)} issues to fix')
        for f in actionable:
            print(f'    [{f.get("severity")}] {f.get("finding", "")}')

        current_spec = fix_spec(current_spec, findings)
        last_findings = findings

    # Final review after fixes to report remaining issues (if any fixes were applied)
    if last_findings:
        final_findings = review(current_spec, requirements)
        remaining = [f for f in final_findings if f.get('severity') in ('CRITICAL', 'WARNING')]
        if remaining:
            print(f'  After {max_iterations} fix iterations, {len(remaining)} issues remain:')
            for f in remaining:
                print(f'    [{f.get("severity")}] {f.get("finding", "")}')
        return current_spec, final_findings

    return current_spec, last_findings

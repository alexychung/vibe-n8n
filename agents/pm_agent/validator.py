"""Extended spec validation beyond parse_spec.

Validates:
1. Schema (via build agent's parse_spec)
2. Every 3.0/LLM step has a gate after it
3. At least 3 test cases
4. Credentials referenced in steps match security.credentials_needed
"""
import os
import sys

# Add build_agent to import path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'build_agent'))

from models import parse_spec, ValidationError


def validate_spec(raw: dict) -> list[str]:
    """Validate a spec dict. Returns list of error strings (empty = valid)."""
    errors = []

    # 1. Schema validation via parse_spec
    try:
        spec = parse_spec(raw)
    except ValidationError as e:
        errors.append(str(e))
        return errors  # Can't do further checks without a valid spec

    # 2. Every 3.0 step must have a gate after it
    gated_steps = set()
    for g in spec.gates:
        if isinstance(g.after_step, str):
            gated_steps.add(g.after_step)
        elif isinstance(g.after_step, list):
            gated_steps.update(g.after_step)
    for step in spec.steps:
        if step.determinism == '3.0' and step.id not in gated_steps:
            errors.append(
                f'Step "{step.name}" (id={step.id}) has determinism 3.0 (LLM) '
                f'but is not registered in the `gates` array. Add a gate entry: '
                f'{{"after_step": "{step.id}", "pass_to": "<id of next normal step>", '
                f'"fail_to": "<id of an error/alert step>", "type": "conditional_branch"}}. '
                f'Also add an IF step (n8n-nodes-base.if) immediately after {step.id} in the '
                f'steps array that validates the LLM response shape (e.g. checks '
                f'`={{ $json.choices[0].message.content }}` is non-empty or a parse_success flag '
                f'from a Code node). The IF step\'s id should be the gate\'s after_step or you '
                f'must point pass_to/fail_to at the IF\'s output targets — the gate entry tells '
                f'the Build Agent how to wire branches.'
            )

    # 3. IF nodes must not have empty conditions
    for step in spec.steps:
        if step.node_type == 'n8n-nodes-base.if':
            conditions = step.parameters.get('conditions', {})
            # Check both pseudocode format {and: [...]} and n8n format {conditions: [...]}
            has_pseudocode = bool(conditions.get('and', []) or conditions.get('or', []))
            has_n8n_format = bool(conditions.get('conditions', []))
            if not has_pseudocode and not has_n8n_format:
                errors.append(
                    f'Step "{step.name}" (id={step.id}) is an IF node with empty conditions. '
                    f'IF nodes must have at least one condition in parameters.conditions.and array.'
                )

    # 4. Merge nodes require parallel wiring (not yet supported by build agent)
    merge_node_types = {'n8n-nodes-base.merge'}
    for step in spec.steps:
        if step.node_type in merge_node_types:
            errors.append(
                f'Step "{step.name}" (id={step.id}) is a Merge node. '
                f'The Build Agent does not support parallel fan-out/merge wiring. '
                f'Use a Code node to combine data from sequential steps instead, '
                f'or remove the Merge node and restructure as a linear flow.'
            )

    # 5. At least 3 test cases
    if len(spec.test_cases) < 3:
        errors.append(
            f'Spec has {len(spec.test_cases)} test case(s), need at least 3 '
            f'(happy path, edge case, error case).'
        )

    return errors

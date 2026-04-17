"""Workflow spec models and parser.

Parses PM Agent JSON specs into typed dataclasses.
Validates required fields and rejects malformed input.
"""
from dataclasses import dataclass, field
from typing import Any


class ValidationError(Exception):
    """Raised when a spec fails validation."""
    pass


@dataclass
class Trigger:
    type: str
    description: str = ''
    path: str = ''
    method: str = ''
    schedule: str = ''


@dataclass
class Step:
    id: str
    name: str
    node_type: str
    parameters: dict = field(default_factory=dict)
    determinism: str = ''
    description: str = ''
    input_shape: Any = None
    output_shape: Any = None
    error_handling: Any = field(default_factory=dict)


@dataclass
class Gate:
    after_step: str
    type: str = ''
    description: str = ''
    pass_to: str = ''
    fail_to: str = ''
    validation: dict = field(default_factory=dict)
    on_fail: str = ''


@dataclass
class TestCase:
    __test__ = False  # prevent pytest from collecting this dataclass

    name: str
    input: dict
    expected: dict


@dataclass
class WorkflowSpec:
    workflow_name: str
    trigger: Trigger
    steps: list[Step]
    test_cases: list[TestCase]
    description: str = ''
    gates: list[Gate] = field(default_factory=list)
    error_handling: dict = field(default_factory=dict)
    output: dict = field(default_factory=dict)
    security: dict = field(default_factory=dict)
    cost_estimate: dict = field(default_factory=dict)
    components_used: list[str] = field(default_factory=list)
    components_needed: list[str] = field(default_factory=list)


def _require(data: dict, key: str, context: str = 'spec'):
    """Raise ValidationError if key is missing or empty."""
    if key not in data:
        raise ValidationError(f'Missing required field: {key} in {context}')
    val = data[key]
    if isinstance(val, str) and not val.strip():
        raise ValidationError(f'Empty required field: {key} in {context}')
    return val


def parse_spec(raw: dict) -> WorkflowSpec:
    """Parse a raw JSON dict into a validated WorkflowSpec."""
    workflow_name = _require(raw, 'workflow_name')
    trigger_raw = _require(raw, 'trigger')
    steps_raw = _require(raw, 'steps')
    test_cases_raw = _require(raw, 'test_cases')

    if not isinstance(steps_raw, list) or len(steps_raw) == 0:
        raise ValidationError('steps must be a non-empty list')

    if not isinstance(test_cases_raw, list):
        raise ValidationError(f'test_cases must be a list, got {type(test_cases_raw).__name__}')
    if len(test_cases_raw) == 0:
        raise ValidationError('test_cases must be a non-empty list')

    trigger_type = trigger_raw.get('type', '')
    if not trigger_type:
        raise ValidationError('trigger.type is required')

    trigger = Trigger(
        type=trigger_type,
        description=trigger_raw.get('description', ''),
        path=trigger_raw.get('path', ''),
        method=trigger_raw.get('method', ''),
        schedule=trigger_raw.get('schedule', ''),
    )

    steps = []
    for i, s in enumerate(steps_raw):
        step_id = _require(s, 'id', f'steps[{i}]')
        steps.append(Step(
            id=step_id,
            name=s.get('name', ''),
            node_type=s.get('node_type', ''),
            parameters=s.get('parameters', {}),
            determinism=s.get('determinism', ''),
            description=s.get('description', ''),
            input_shape=s.get('input_shape'),
            output_shape=s.get('output_shape'),
            error_handling=s.get('error_handling', {}),
        ))

    # Check for duplicate step IDs
    seen_ids = set()
    for step in steps:
        if step.id in seen_ids:
            raise ValidationError(f'Duplicate step id: {step.id}')
        seen_ids.add(step.id)

    gates = []
    for g in raw.get('gates', []):
        gate_type = g.get('type', '')
        after_step = g.get('after_step', '')
        # PM-generated specs sometimes include invented gate kinds
        # ("sequential", "error_branch", ...) or gates missing after_step
        # (error handlers keyed on from_step_error). Only conditional_branch
        # gates drive wiring; silently drop the rest so the wire phase isn't
        # fed bogus connection requests.
        if not after_step:
            continue
        if gate_type and gate_type != 'conditional_branch':
            continue
        gates.append(Gate(
            after_step=after_step,
            type=gate_type,
            description=g.get('description', ''),
            pass_to=g.get('pass_to', ''),
            fail_to=g.get('fail_to', ''),
            validation=g.get('validation', {}),
            on_fail=g.get('on_fail', ''),
        ))

    # Validate gate references
    for i, gate in enumerate(gates):
        if gate.after_step and gate.after_step not in seen_ids:
            raise ValidationError(f'gates[{i}].after_step references unknown step: {gate.after_step}')
        if gate.pass_to and gate.pass_to not in seen_ids:
            raise ValidationError(f'gates[{i}].pass_to references unknown step: {gate.pass_to}')
        if gate.fail_to and gate.fail_to not in seen_ids:
            raise ValidationError(f'gates[{i}].fail_to references unknown step: {gate.fail_to}')

    # Validate webhook triggers have a path
    if trigger_type in ('webhook', 'event') and not trigger.path:
        raise ValidationError('Webhook trigger requires a non-empty path')

    test_cases = []
    for i, tc in enumerate(test_cases_raw):
        tc_input = tc.get('input', {})
        tc_expected = tc.get('expected', {})
        if not isinstance(tc_expected, dict):
            raise ValidationError(f'test_cases[{i}].expected must be a dict, got {type(tc_expected).__name__}')
        test_cases.append(TestCase(
            name=tc.get('name', ''),
            input=tc_input,
            expected=tc_expected,
        ))

    return WorkflowSpec(
        workflow_name=workflow_name,
        description=raw.get('description', ''),
        trigger=trigger,
        steps=steps,
        gates=gates,
        test_cases=test_cases,
        error_handling=raw.get('error_handling', {}),
        output=raw.get('output', {}),
        security=raw.get('security', {}),
        cost_estimate=raw.get('cost_estimate', {}),
        components_used=raw.get('components_used', []),
        components_needed=raw.get('components_needed', []),
    )

"""WIRE phase — configure and connect nodes in a scaffolded workflow.

Processes nodes one at a time: set parameters, then add connections.
Uses the GET-modify-PUT pattern via client.update_workflow().
"""
import re

from models import WorkflowSpec, Step, Gate
from client import N8nClient


def _find_node_by_name(nodes: list[dict], name: str) -> dict:
    """Find a node in the workflow by its name."""
    for n in nodes:
        if n['name'] == name:
            return n
    raise ValueError(f'Node not found: {name}')


def _find_node_by_id(nodes: list[dict], step_id: str) -> dict:
    """Find a node in the workflow by its spec step ID."""
    for n in nodes:
        if n['id'] == step_id:
            return n
    raise ValueError(f'Node with id not found: {step_id}')


def _find_gate_for_step(spec: WorkflowSpec, step_id: str):
    """Find a gate that branches after this step."""
    for gate in spec.gates:
        if gate.after_step == step_id:
            return gate
    return None


def _translate_set_params(spec_params: dict) -> dict:
    """Translate spec Set node parameters to n8n format."""
    assignments = spec_params.get('assignments', [])
    if isinstance(assignments, dict):
        # Already in n8n format
        return spec_params

    # Convert from spec format [{name, value, type}, ...] to n8n format
    n8n_assignments = []
    for i, a in enumerate(assignments):
        n8n_assignments.append({
            'id': f'a{i}',
            'name': a['name'],
            'value': a['value'],
            'type': a.get('type', 'string'),
        })

    return {
        'assignments': {
            'assignments': n8n_assignments,
        }
    }


def _is_boolean_value(value) -> bool:
    """Check if a value represents a boolean (string 'true'/'false'/'True'/'False' or Python bool)."""
    if isinstance(value, bool):
        return True
    if isinstance(value, str) and value.strip().lower() in ('true', 'false'):
        return True
    return False


_STRING_WRAP = re.compile(r'^=\{\{\s*String\(\s*(.+?)\s*\)\s*\}\}$')


def _unwrap_string_cast(expr: str) -> str:
    """Strip a surrounding String(...) cast from an n8n expression.

    LLMs sometimes emit `={{ String($json.valid) }}` for boolean operators
    "for compatibility," but the String() cast turns both true and false
    into truthy non-empty strings — so every input routes to the true
    branch. Unwrap to the raw expression when the operator is boolean.
    """
    if not isinstance(expr, str):
        return expr
    m = _STRING_WRAP.match(expr.strip())
    if not m:
        return expr
    return '={{ ' + m.group(1) + ' }}'


def _fix_boolean_conditions_n8n(spec_params: dict) -> dict:
    """Fix boolean comparisons in already-translated n8n format conditions.

    When the LLM outputs native n8n format, boolean checks like
    equals "true" with type "number" need to be converted to
    type "boolean" with operation "true"/"false". Also strips
    String(...) coercion on leftValue when the operator is boolean.
    """
    conditions = spec_params.get('conditions', {})
    cond_list = conditions.get('conditions', [])

    for cond in cond_list:
        op = cond.get('operator', {})
        right = cond.get('rightValue', '')
        if op.get('operation') == 'equals' and _is_boolean_value(right):
            bool_val = str(right).strip().lower() in ('true', '1')
            cond['operator'] = {
                'type': 'boolean',
                'operation': 'true' if bool_val else 'false',
            }
            cond.pop('rightValue', None)

        if cond.get('operator', {}).get('type') == 'boolean':
            cond['leftValue'] = _unwrap_string_cast(cond.get('leftValue', ''))

    return spec_params


def _translate_if_params(spec_params: dict) -> dict:
    """Translate spec IF node parameters to n8n v2 format."""
    conditions = spec_params.get('conditions', {})

    # Already in n8n format — just fix boolean conditions
    if 'combinator' in conditions:
        return _fix_boolean_conditions_n8n(spec_params)

    # Support both AND and OR pseudocode conditions
    and_conditions = conditions.get('and', [])
    or_conditions = conditions.get('or', [])
    raw_conditions = and_conditions or or_conditions
    combinator = 'or' if or_conditions and not and_conditions else 'and'

    # Map spec operations to n8n IF v2 operations
    op_map = {
        'isNotEmpty': 'notEmpty',
        'isEmpty': 'empty',
        'gte': 'gte',
        'lte': 'lte',
        'gt': 'gt',
        'lt': 'lt',
        'equals': 'equals',
    }

    n8n_cond_list = []
    for i, cond in enumerate(raw_conditions):
        field_expr = cond.get('field', '')
        op = cond.get('operation', '')
        value = cond.get('value', '')

        n8n_op = op_map.get(op, op)
        is_string_op = n8n_op in ('notEmpty', 'empty', 'contains', 'startsWith', 'endsWith')

        # Detect boolean comparisons: equals "true"/"false" or Python bool
        if n8n_op == 'equals' and _is_boolean_value(value):
            bool_val = str(value).lower().strip() in ('true', '1')
            n8n_cond_list.append({
                'id': f'cond_{i}',
                'leftValue': field_expr,
                'operator': {
                    'type': 'boolean',
                    'operation': 'true' if bool_val else 'false',
                },
            })
        else:
            # For equals with string values (non-boolean), use string type
            if n8n_op == 'equals' and isinstance(value, str) and value != '':
                op_type = 'string'
            elif is_string_op:
                op_type = 'string'
            else:
                op_type = 'number'

            n8n_cond_list.append({
                'id': f'cond_{i}',
                'leftValue': field_expr,
                'rightValue': str(value) if value != '' else '',
                'operator': {
                    'type': op_type,
                    'operation': n8n_op,
                },
            })

    return {
        'conditions': {
            'options': {'caseSensitive': True, 'leftValue': ''},
            'conditions': n8n_cond_list,
            'combinator': combinator,
        }
    }


def _configure_node(node: dict, step: Step) -> dict:
    """Apply spec parameters to a node."""
    if step.node_type == 'n8n-nodes-base.set':
        node['parameters'] = _translate_set_params(step.parameters)
    elif step.node_type == 'n8n-nodes-base.if':
        node['parameters'] = _translate_if_params(step.parameters)
    else:
        # For other node types, pass parameters through
        node['parameters'] = step.parameters
    return node


def _build_connections(spec: WorkflowSpec, nodes: list[dict]) -> dict:
    """Build the full connections map from the spec.

    Connects: trigger → first step, then step-to-step.
    Gates create branching connections (true/false outputs).
    """
    connections = {}

    # Find trigger node name
    trigger_node = next(n for n in nodes if n['id'] == 'trigger')
    trigger_name = trigger_node['name']

    # Build step lookup: step_id → node_name
    step_name = {}
    for step in spec.steps:
        node = _find_node_by_id(nodes, step.id)
        step_name[step.id] = node['name']

    # Connect trigger → first step
    first_step = spec.steps[0]
    connections[trigger_name] = {
        'main': [[{'node': step_name[first_step.id], 'type': 'main', 'index': 0}]]
    }

    # Figure out which steps are gate branch targets (they get connected by the gate, not sequentially)
    gate_targets = set()
    for gate in spec.gates:
        for target_id in (gate.pass_to, gate.fail_to):
            if target_id:
                if target_id not in step_name:
                    raise ValueError(
                        f'Gate after "{gate.after_step}" references unknown step "{target_id}"'
                    )
                gate_targets.add(target_id)

    # Build the step order index for quick lookups
    step_index = {s.id: i for i, s in enumerate(spec.steps)}

    # For each gate, figure out the "continuation" step — the next main-path
    # step after the gate.  Branch targets that aren't terminal wire into this.
    gate_continuation = {}  # gate.after_step → next main-path step id (or None)
    for gate in spec.gates:
        after_idx = step_index.get(gate.after_step, -1)
        continuation = None
        for s in spec.steps[after_idx + 1:]:
            if s.id not in gate_targets:
                continuation = s.id
                break
        gate_continuation[gate.after_step] = continuation

    # Connect steps, handling gates and sequential connections
    main_steps = [s for s in spec.steps if s.id not in gate_targets]
    for i, step in enumerate(main_steps):
        gate = _find_gate_for_step(spec, step.id)
        if gate:
            # This step is a gate — connect true/false branches
            true_name = step_name.get(gate.pass_to, '')
            false_name = step_name.get(gate.fail_to, '')
            outputs = []
            outputs.append([{'node': true_name, 'type': 'main', 'index': 0}] if true_name else [])
            outputs.append([{'node': false_name, 'type': 'main', 'index': 0}] if false_name else [])
            connections[step_name[step.id]] = {'main': outputs}
        elif i + 1 < len(main_steps):
            # Sequential step — connect to next main step
            next_step = main_steps[i + 1]
            connections[step_name[step.id]] = {
                'main': [[{'node': step_name[next_step.id], 'type': 'main', 'index': 0}]]
            }

    # Wire gate branch targets to continuation steps (if they're not terminal
    # and don't themselves have gates).
    for gate in spec.gates:
        cont_id = gate_continuation.get(gate.after_step)
        if not cont_id:
            continue  # no continuation — branch targets are terminal
        cont_name = step_name[cont_id]
        for target_id in (gate.pass_to, gate.fail_to):
            if not target_id:
                continue
            target_step_name = step_name.get(target_id, '')
            if not target_step_name:
                continue
            # Don't overwrite if the target already has connections (e.g., it's a gate itself)
            if target_step_name in connections:
                continue
            connections[target_step_name] = {
                'main': [[{'node': cont_name, 'type': 'main', 'index': 0}]]
            }

    return connections


def wire(spec: WorkflowSpec, client: N8nClient, workflow_id: str) -> dict:
    """Configure all nodes and add all connections.

    Returns the fully wired workflow.
    """
    def do_wire(wf: dict) -> dict:
        # 1. Configure each step node's parameters
        for step in spec.steps:
            for node in wf['nodes']:
                if node['id'] == step.id:
                    _configure_node(node, step)
                    break

        # 2. Build and set connections
        wf['connections'] = _build_connections(spec, wf['nodes'])

        return wf

    return client.update_workflow(workflow_id, do_wire)

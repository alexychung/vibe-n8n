"""SCAFFOLD phase — create workflow skeleton in n8n.

Creates the workflow with all nodes positioned but unconfigured and unconnected.
Configuration and connections happen in the WIRE phase.
"""
from models import WorkflowSpec
from client import N8nClient

# Mapping from spec trigger types to n8n node types
TRIGGER_TYPE_MAP = {
    'manual': 'n8n-nodes-base.manualTrigger',
    'cron': 'n8n-nodes-base.scheduleTrigger',
    'webhook': 'n8n-nodes-base.webhook',
    'polling': 'n8n-nodes-base.scheduleTrigger',
    'event': 'n8n-nodes-base.webhook',
    'chained': 'n8n-nodes-base.executeWorkflowTrigger',
}

# Layout constants
START_X = 250
START_Y = 300
X_SPACING = 250
Y_BRANCH_OFFSET = 150


_TRIGGER_NAME_MAX = 60


def _trigger_name(spec: WorkflowSpec) -> str:
    """Pick a concise trigger node name.

    Prefer spec.trigger.description only when it's short enough to read on
    the canvas. LLM-authored specs often put multi-sentence prose in
    description, which corrupts connection keys (n8n uses node name as the
    connection map key).
    """
    desc = (spec.trigger.description or '').strip()
    if desc and len(desc) <= _TRIGGER_NAME_MAX and '\n' not in desc:
        return desc
    return f'{spec.trigger.type.title()} Trigger'


def _needs_response_node_mode(spec: WorkflowSpec) -> bool:
    """True if any step is a respondToWebhook — trigger must wait for it."""
    return any(s.node_type == 'n8n-nodes-base.respondToWebhook' for s in spec.steps)


def _build_trigger_node(spec: WorkflowSpec) -> dict:
    """Build the trigger node from spec."""
    trigger_type = TRIGGER_TYPE_MAP.get(spec.trigger.type, 'n8n-nodes-base.webhook')
    node = {
        'id': 'trigger',
        'name': _trigger_name(spec),
        'type': trigger_type,
        'typeVersion': 2,
        'position': [START_X, START_Y],
        'parameters': {},
    }
    # Webhook triggers need path and response mode for scaffold
    if spec.trigger.type in ('webhook', 'event'):
        response_mode = 'responseNode' if _needs_response_node_mode(spec) else 'lastNode'
        node['parameters'] = {
            'path': spec.trigger.path or spec.workflow_name.lower().replace(' ', '-'),
            'httpMethod': spec.trigger.method or 'POST',
            'responseMode': response_mode,
        }
        node['webhookId'] = f'{spec.trigger.path or "hook"}-id'
    return node


def _find_gate_for_step(spec: WorkflowSpec, step_id: str):
    """Find a gate that branches after this step, if any."""
    for gate in spec.gates:
        if gate.after_step == step_id:
            return gate
    return None


def _build_step_nodes(spec: WorkflowSpec) -> list[dict]:
    """Build placeholder nodes for each step, positioned left-to-right.

    Steps that are gate fail targets are positioned below the main path.
    """
    # Figure out which steps are on error/fail branches
    fail_targets = set()
    for gate in spec.gates:
        if gate.fail_to:
            fail_targets.add(gate.fail_to)

    # Figure out which gate each fail target belongs to, so we can position
    # the branch node at the same X as the gate step (not the next main step)
    fail_to_gate_step = {}
    for gate in spec.gates:
        if gate.fail_to:
            fail_to_gate_step[gate.fail_to] = gate.after_step

    nodes = []
    main_x = START_X + X_SPACING  # First step starts one spacing right of trigger
    step_x = {}  # Track x position of each step by id

    # First pass: position main-path nodes
    for step in spec.steps:
        if step.id in fail_targets:
            continue  # Position branch nodes in second pass
        step_x[step.id] = main_x
        nodes.append({
            'id': step.id,
            'name': step.name,
            'type': step.node_type,
            'typeVersion': _default_type_version(step.node_type),
            'position': [main_x, START_Y],
            'parameters': {},  # Empty — configured in WIRE phase
        })
        main_x += X_SPACING

    # Second pass: position branch nodes below their gate step
    for step in spec.steps:
        if step.id not in fail_targets:
            continue
        gate_step_id = fail_to_gate_step.get(step.id, '')
        x = step_x.get(gate_step_id, main_x)  # Same X as gate, or end if unknown
        nodes.append({
            'id': step.id,
            'name': step.name,
            'type': step.node_type,
            'typeVersion': _default_type_version(step.node_type),
            'position': [x, START_Y + Y_BRANCH_OFFSET],
            'parameters': {},  # Empty — configured in WIRE phase
        })

    return nodes


def _default_type_version(node_type: str) -> float:
    """Return a sensible default typeVersion for common node types."""
    defaults = {
        'n8n-nodes-base.set': 3.4,
        'n8n-nodes-base.if': 2,
        'n8n-nodes-base.code': 2,
        'n8n-nodes-base.webhook': 2,
        'n8n-nodes-base.httpRequest': 4.2,
        'n8n-nodes-base.scheduleTrigger': 1.2,
        'n8n-nodes-base.merge': 3,
        'n8n-nodes-base.respondToWebhook': 1.1,
        'n8n-nodes-base.slack': 2.2,
        'n8n-nodes-base.emailSend': 2.1,
        'n8n-nodes-base.rssFeedRead': 1,
        'n8n-nodes-base.noOp': 1,
        'n8n-nodes-base.splitInBatches': 3,
    }
    return defaults.get(node_type, 1)


def scaffold(spec: WorkflowSpec, client: N8nClient) -> str:
    """Create a workflow skeleton in n8n. Returns the workflow ID.

    Creates all nodes (trigger + steps) with correct names and types,
    positioned left-to-right. No parameters configured, no connections.
    """
    trigger_node = _build_trigger_node(spec)
    step_nodes = _build_step_nodes(spec)
    all_nodes = [trigger_node] + step_nodes

    settings = {
        'executionTimeout': spec.error_handling.get('global_timeout_seconds', 300),
        'saveExecutionProgress': True,
        'saveDataErrorExecution': 'all',
        'saveDataSuccessExecution': 'all',
    }

    result = client.create_workflow(
        name=spec.workflow_name,
        nodes=all_nodes,
        connections={},  # No connections — that's WIRE phase
        settings=settings,
    )

    return result['id']

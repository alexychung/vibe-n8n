"""Unit tests for SCAFFOLD phase — pure logic, no n8n required.

Tests node building, positioning, trigger mapping, and the scaffold function
with a mocked API client.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from models import WorkflowSpec, Trigger, Step, Gate, TestCase
from scaffold import (
    _build_trigger_node,
    _build_step_nodes,
    _default_type_version,
    _find_gate_for_step,
    scaffold,
    TRIGGER_TYPE_MAP,
    START_X,
    START_Y,
    X_SPACING,
    Y_BRANCH_OFFSET,
)


def _make_spec(**overrides):
    """Build a minimal WorkflowSpec for testing."""
    defaults = dict(
        workflow_name='Test Workflow',
        trigger=Trigger(type='webhook', path='test-hook', method='POST', description='Test Webhook'),
        steps=[
            Step(id='step_1', name='Do Thing', node_type='n8n-nodes-base.set'),
        ],
        gates=[],
        test_cases=[TestCase(name='tc1', input={'a': 1}, expected={'b': 2})],
    )
    defaults.update(overrides)
    return WorkflowSpec(**defaults)


class TestBuildTriggerNode(unittest.TestCase):
    """Tests for _build_trigger_node."""

    def test_webhook_trigger(self):
        spec = _make_spec()
        node = _build_trigger_node(spec)
        self.assertEqual(node['id'], 'trigger')
        self.assertEqual(node['type'], 'n8n-nodes-base.webhook')
        self.assertEqual(node['parameters']['path'], 'test-hook')
        self.assertEqual(node['parameters']['httpMethod'], 'POST')
        self.assertEqual(node['parameters']['responseMode'], 'lastNode')
        self.assertIn('webhookId', node)

    def test_manual_trigger(self):
        spec = _make_spec(trigger=Trigger(type='manual', description='Manual'))
        node = _build_trigger_node(spec)
        self.assertEqual(node['type'], 'n8n-nodes-base.manualTrigger')
        self.assertEqual(node['parameters'], {})
        self.assertNotIn('webhookId', node)

    def test_cron_trigger(self):
        spec = _make_spec(trigger=Trigger(type='cron'))
        node = _build_trigger_node(spec)
        self.assertEqual(node['type'], 'n8n-nodes-base.scheduleTrigger')

    def test_chained_trigger(self):
        spec = _make_spec(trigger=Trigger(type='chained'))
        node = _build_trigger_node(spec)
        self.assertEqual(node['type'], 'n8n-nodes-base.executeWorkflowTrigger')

    def test_unknown_trigger_defaults_to_webhook(self):
        spec = _make_spec(trigger=Trigger(type='unknown'))
        node = _build_trigger_node(spec)
        self.assertEqual(node['type'], 'n8n-nodes-base.webhook')

    def test_trigger_position(self):
        spec = _make_spec()
        node = _build_trigger_node(spec)
        self.assertEqual(node['position'], [START_X, START_Y])

    def test_webhook_path_defaults_from_name(self):
        spec = _make_spec(
            trigger=Trigger(type='webhook', path=''),
            workflow_name='My Cool Workflow',
        )
        node = _build_trigger_node(spec)
        self.assertEqual(node['parameters']['path'], 'my-cool-workflow')

    def test_trigger_description_used_as_name(self):
        spec = _make_spec(trigger=Trigger(type='manual', description='Start Here'))
        node = _build_trigger_node(spec)
        self.assertEqual(node['name'], 'Start Here')

    def test_trigger_name_fallback_when_no_description(self):
        spec = _make_spec(trigger=Trigger(type='cron', description=''))
        node = _build_trigger_node(spec)
        self.assertEqual(node['name'], 'Cron Trigger')

    def test_trigger_name_fallback_when_description_is_prose(self):
        # PM Agents sometimes put multi-sentence prose in description; that
        # would blow up connection keys. Use the synthesized name instead.
        long_desc = (
            'Accepts POST requests with Content-Type: application/json body '
            'containing name and value fields. Configure the webhook node Body '
            'Content Type to JSON.'
        )
        spec = _make_spec(trigger=Trigger(type='webhook', path='p', description=long_desc))
        node = _build_trigger_node(spec)
        self.assertEqual(node['name'], 'Webhook Trigger')

    def test_trigger_name_fallback_when_description_has_newline(self):
        spec = _make_spec(trigger=Trigger(type='webhook', path='p', description='line1\nline2'))
        node = _build_trigger_node(spec)
        self.assertEqual(node['name'], 'Webhook Trigger')

    def test_webhook_response_mode_switches_when_respond_node_present(self):
        spec = _make_spec(steps=[
            Step(id='step_1', name='Set X', node_type='n8n-nodes-base.set'),
            Step(id='step_2', name='Respond', node_type='n8n-nodes-base.respondToWebhook'),
        ])
        node = _build_trigger_node(spec)
        self.assertEqual(node['parameters']['responseMode'], 'responseNode')

    def test_webhook_response_mode_stays_last_node_without_respond_step(self):
        spec = _make_spec()  # only a Set step
        node = _build_trigger_node(spec)
        self.assertEqual(node['parameters']['responseMode'], 'lastNode')


class TestBuildStepNodes(unittest.TestCase):
    """Tests for _build_step_nodes."""

    def test_single_step_position(self):
        spec = _make_spec()
        nodes = _build_step_nodes(spec)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]['position'][0], START_X + X_SPACING)
        self.assertEqual(nodes[0]['position'][1], START_Y)

    def test_multiple_steps_left_to_right(self):
        spec = _make_spec(steps=[
            Step(id='s1', name='A', node_type='n8n-nodes-base.set'),
            Step(id='s2', name='B', node_type='n8n-nodes-base.code'),
            Step(id='s3', name='C', node_type='n8n-nodes-base.if'),
        ])
        nodes = _build_step_nodes(spec)
        xs = [n['position'][0] for n in nodes]
        self.assertEqual(xs, [START_X + X_SPACING, START_X + 2 * X_SPACING, START_X + 3 * X_SPACING])

    def test_fail_branch_positioned_below(self):
        spec = _make_spec(
            steps=[
                Step(id='s1', name='Check', node_type='n8n-nodes-base.if'),
                Step(id='s2', name='Success', node_type='n8n-nodes-base.set'),
                Step(id='s3', name='Error', node_type='n8n-nodes-base.set'),
            ],
            gates=[Gate(after_step='s1', pass_to='s2', fail_to='s3')],
        )
        nodes = _build_step_nodes(spec)
        node_map = {n['id']: n for n in nodes}

        # s3 is a fail target — should be below main path
        self.assertEqual(node_map['s3']['position'][1], START_Y + Y_BRANCH_OFFSET)
        # s1 and s2 are on main path
        self.assertEqual(node_map['s1']['position'][1], START_Y)
        self.assertEqual(node_map['s2']['position'][1], START_Y)

    def test_node_ids_preserved(self):
        spec = _make_spec(steps=[
            Step(id='my-custom-id', name='Custom', node_type='n8n-nodes-base.set'),
        ])
        nodes = _build_step_nodes(spec)
        self.assertEqual(nodes[0]['id'], 'my-custom-id')

    def test_parameters_empty_in_scaffold(self):
        spec = _make_spec(steps=[
            Step(id='s1', name='A', node_type='n8n-nodes-base.set', parameters={'foo': 'bar'}),
        ])
        nodes = _build_step_nodes(spec)
        self.assertEqual(nodes[0]['parameters'], {})


class TestDefaultTypeVersion(unittest.TestCase):
    def test_known_types(self):
        self.assertEqual(_default_type_version('n8n-nodes-base.set'), 3.4)
        self.assertEqual(_default_type_version('n8n-nodes-base.if'), 2)
        self.assertEqual(_default_type_version('n8n-nodes-base.code'), 2)
        self.assertEqual(_default_type_version('n8n-nodes-base.webhook'), 2)
        self.assertEqual(_default_type_version('n8n-nodes-base.httpRequest'), 4.2)

    def test_unknown_type_defaults_to_1(self):
        self.assertEqual(_default_type_version('n8n-nodes-base.somethingNew'), 1)

    def test_merge_node_has_version(self):
        """LLM frequently generates merge nodes — must not default to 1."""
        self.assertGreater(_default_type_version('n8n-nodes-base.merge'), 1)

    def test_respond_to_webhook_has_version(self):
        """Webhook specs need respondToWebhook — must not default to 1."""
        self.assertGreater(_default_type_version('n8n-nodes-base.respondToWebhook'), 1)

    def test_slack_node_has_version(self):
        """LLM generates Slack nodes for notification workflows."""
        self.assertGreater(_default_type_version('n8n-nodes-base.slack'), 1)


class TestFindGateForStep(unittest.TestCase):
    def test_finds_matching_gate(self):
        spec = _make_spec(gates=[Gate(after_step='s1', pass_to='s2', fail_to='s3')])
        gate = _find_gate_for_step(spec, 's1')
        self.assertIsNotNone(gate)
        self.assertEqual(gate.pass_to, 's2')

    def test_returns_none_when_no_gate(self):
        spec = _make_spec(gates=[Gate(after_step='s1')])
        gate = _find_gate_for_step(spec, 'no-such-step')
        self.assertIsNone(gate)


class TestScaffoldFunction(unittest.TestCase):
    """Test scaffold() with a mocked client."""

    def test_scaffold_calls_create_workflow(self):
        spec = _make_spec()
        client = MagicMock()
        client.create_workflow.return_value = {'id': 'wf-123'}

        result = scaffold(spec, client)

        self.assertEqual(result, 'wf-123')
        client.create_workflow.assert_called_once()

    def test_scaffold_passes_correct_node_count(self):
        spec = _make_spec(steps=[
            Step(id='s1', name='A', node_type='n8n-nodes-base.set'),
            Step(id='s2', name='B', node_type='n8n-nodes-base.code'),
        ])
        client = MagicMock()
        client.create_workflow.return_value = {'id': 'wf-1'}

        scaffold(spec, client)

        call_args = client.create_workflow.call_args
        nodes = call_args.kwargs.get('nodes') or call_args[1].get('nodes')
        # 1 trigger + 2 steps = 3
        self.assertEqual(len(nodes), 3)

    def test_scaffold_passes_empty_connections(self):
        spec = _make_spec()
        client = MagicMock()
        client.create_workflow.return_value = {'id': 'wf-1'}

        scaffold(spec, client)

        call_args = client.create_workflow.call_args
        connections = call_args.kwargs.get('connections') or call_args[1].get('connections')
        self.assertEqual(connections, {})

    def test_scaffold_passes_settings(self):
        spec = _make_spec(error_handling={'global_timeout_seconds': 60})
        client = MagicMock()
        client.create_workflow.return_value = {'id': 'wf-1'}

        scaffold(spec, client)

        call_args = client.create_workflow.call_args
        settings = call_args.kwargs.get('settings') or call_args[1].get('settings')
        self.assertEqual(settings['executionTimeout'], 60)
        self.assertTrue(settings['saveExecutionProgress'])


if __name__ == '__main__':
    unittest.main()

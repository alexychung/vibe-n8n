"""Tests for spec parser and models."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from models import WorkflowSpec, parse_spec, ValidationError

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'workflows', 'test-data')
ECHO_SPEC_PATH = os.path.join(FIXTURE_DIR, 'echo-spec.json')


class TestParseSpec(unittest.TestCase):
    """Test spec JSON parsing into typed dataclasses."""

    def test_parse_echo_spec(self):
        """Parses the echo spec fixture into a WorkflowSpec."""
        with open(ECHO_SPEC_PATH) as f:
            raw = json.load(f)
        spec = parse_spec(raw)

        self.assertIsInstance(spec, WorkflowSpec)
        self.assertEqual(spec.workflow_name, 'Webhook Echo')
        self.assertEqual(spec.trigger.type, 'webhook')
        self.assertEqual(spec.trigger.path, 'echo-test')
        self.assertEqual(len(spec.steps), 3)
        self.assertEqual(len(spec.gates), 1)
        self.assertEqual(len(spec.test_cases), 5)

    def test_step_fields(self):
        """Each step has id, name, node_type, parameters."""
        with open(ECHO_SPEC_PATH) as f:
            raw = json.load(f)
        spec = parse_spec(raw)

        step1 = spec.steps[0]
        self.assertEqual(step1.id, 'step_1')
        self.assertEqual(step1.name, 'Validate Input')
        self.assertEqual(step1.node_type, 'n8n-nodes-base.if')
        self.assertIn('conditions', step1.parameters)

    def test_gate_fields(self):
        """Gate has after_step, pass_to, fail_to."""
        with open(ECHO_SPEC_PATH) as f:
            raw = json.load(f)
        spec = parse_spec(raw)

        gate = spec.gates[0]
        self.assertEqual(gate.after_step, 'step_1')
        self.assertEqual(gate.pass_to, 'step_2')
        self.assertEqual(gate.fail_to, 'step_3')

    def test_test_case_fields(self):
        """Test cases have name, input, expected."""
        with open(ECHO_SPEC_PATH) as f:
            raw = json.load(f)
        spec = parse_spec(raw)

        tc = spec.test_cases[0]
        self.assertEqual(tc.name, 'Happy path')
        self.assertEqual(tc.input['name'], 'test-item')
        self.assertEqual(tc.input['value'], 42)
        self.assertEqual(tc.expected['status'], 'ok')

    def test_error_handling_fields(self):
        """Spec-level error handling is parsed."""
        with open(ECHO_SPEC_PATH) as f:
            raw = json.load(f)
        spec = parse_spec(raw)

        self.assertEqual(spec.error_handling['global_timeout_seconds'], 30)

    def test_trigger_webhook_fields(self):
        """Webhook trigger has path and method."""
        with open(ECHO_SPEC_PATH) as f:
            raw = json.load(f)
        spec = parse_spec(raw)

        self.assertEqual(spec.trigger.path, 'echo-test')
        self.assertEqual(spec.trigger.method, 'POST')

    def test_security_and_cost(self):
        """Security and cost_estimate are preserved."""
        with open(ECHO_SPEC_PATH) as f:
            raw = json.load(f)
        spec = parse_spec(raw)

        self.assertEqual(spec.security['credentials_needed'], [])
        self.assertIn('per_run', spec.cost_estimate)


_VALID_TC = [{'name': 'T', 'input': {}, 'expected': {}}]
_VALID_STEP = [{'id': 's1', 'name': 'S', 'node_type': 'n8n-nodes-base.set'}]


class TestValidation(unittest.TestCase):
    """Test spec validation catches malformed input."""

    def test_missing_workflow_name(self):
        """Rejects spec with no workflow_name."""
        with self.assertRaises(ValidationError) as ctx:
            parse_spec({'trigger': {'type': 'webhook'}, 'steps': _VALID_STEP, 'test_cases': _VALID_TC})
        self.assertIn('workflow_name', str(ctx.exception))

    def test_missing_trigger(self):
        """Rejects spec with no trigger."""
        with self.assertRaises(ValidationError) as ctx:
            parse_spec({'workflow_name': 'X', 'steps': _VALID_STEP, 'test_cases': _VALID_TC})
        self.assertIn('trigger', str(ctx.exception))

    def test_missing_steps(self):
        """Rejects spec with no steps."""
        with self.assertRaises(ValidationError) as ctx:
            parse_spec({'workflow_name': 'X', 'trigger': {'type': 'webhook'}, 'test_cases': _VALID_TC})
        self.assertIn('steps', str(ctx.exception))

    def test_missing_test_cases(self):
        """Rejects spec with no test_cases."""
        with self.assertRaises(ValidationError) as ctx:
            parse_spec({
                'workflow_name': 'X',
                'trigger': {'type': 'webhook'},
                'steps': _VALID_STEP,
            })
        self.assertIn('test_cases', str(ctx.exception))

    def test_step_missing_id(self):
        """Rejects step with no id."""
        with self.assertRaises(ValidationError) as ctx:
            parse_spec({
                'workflow_name': 'X',
                'trigger': {'type': 'webhook'},
                'steps': [{'name': 'S', 'node_type': 'n8n-nodes-base.set'}],
                'test_cases': _VALID_TC,
            })
        self.assertIn('id', str(ctx.exception))

    def test_empty_string_workflow_name(self):
        """Rejects empty string workflow_name."""
        with self.assertRaises(ValidationError) as ctx:
            parse_spec({
                'workflow_name': '',
                'trigger': {'type': 'webhook'},
                'steps': _VALID_STEP,
                'test_cases': _VALID_TC,
            })
        self.assertIn('workflow_name', str(ctx.exception))

    def test_empty_test_cases_rejected(self):
        """Rejects spec with empty test_cases list."""
        with self.assertRaises(ValidationError) as ctx:
            parse_spec({
                'workflow_name': 'X',
                'trigger': {'type': 'webhook'},
                'steps': _VALID_STEP,
                'test_cases': [],
            })
        self.assertIn('test_cases', str(ctx.exception))

    def test_test_cases_not_a_list_rejected(self):
        """Rejects spec where test_cases is a string instead of list."""
        with self.assertRaises(ValidationError) as ctx:
            parse_spec({
                'workflow_name': 'X',
                'trigger': {'type': 'webhook'},
                'steps': _VALID_STEP,
                'test_cases': 'not a list',
            })
        self.assertIn('test_cases must be a list', str(ctx.exception))

    def test_duplicate_step_ids_rejected(self):
        """Rejects spec with duplicate step IDs."""
        with self.assertRaises(ValidationError) as ctx:
            parse_spec({
                'workflow_name': 'X',
                'trigger': {'type': 'webhook'},
                'steps': [
                    {'id': 's1', 'name': 'A', 'node_type': 'n8n-nodes-base.set'},
                    {'id': 's1', 'name': 'B', 'node_type': 'n8n-nodes-base.set'},
                ],
                'test_cases': _VALID_TC,
            })
        self.assertIn('Duplicate step id', str(ctx.exception))

    def test_empty_trigger_type_rejected(self):
        """Rejects spec with empty trigger type."""
        with self.assertRaises(ValidationError) as ctx:
            parse_spec({
                'workflow_name': 'X',
                'trigger': {'type': ''},
                'steps': _VALID_STEP,
                'test_cases': _VALID_TC,
            })
        self.assertIn('trigger.type', str(ctx.exception))

    def test_missing_trigger_type_rejected(self):
        """Rejects spec with missing trigger type field."""
        with self.assertRaises(ValidationError) as ctx:
            parse_spec({
                'workflow_name': 'X',
                'trigger': {'path': '/hook'},
                'steps': _VALID_STEP,
                'test_cases': _VALID_TC,
            })
        self.assertIn('trigger.type', str(ctx.exception))


    def test_gate_references_unknown_after_step(self):
        """Rejects gate whose after_step doesn't match any step ID."""
        with self.assertRaises(ValidationError) as ctx:
            parse_spec({
                'workflow_name': 'X',
                'trigger': {'type': 'webhook', 'path': 'test'},
                'steps': [{'id': 's1', 'name': 'A', 'node_type': 'n8n-nodes-base.set'}],
                'gates': [{'after_step': 'nonexistent', 'pass_to': 's1'}],
                'test_cases': _VALID_TC,
            })
        self.assertIn('unknown step', str(ctx.exception))

    def test_gate_references_unknown_pass_to(self):
        """Rejects gate whose pass_to doesn't match any step ID."""
        with self.assertRaises(ValidationError) as ctx:
            parse_spec({
                'workflow_name': 'X',
                'trigger': {'type': 'webhook', 'path': 'test'},
                'steps': [{'id': 's1', 'name': 'A', 'node_type': 'n8n-nodes-base.if'}],
                'gates': [{'after_step': 's1', 'pass_to': 'nonexistent'}],
                'test_cases': _VALID_TC,
            })
        self.assertIn('unknown step', str(ctx.exception))

    def test_webhook_trigger_without_path_rejected(self):
        """Rejects webhook trigger with no path."""
        with self.assertRaises(ValidationError) as ctx:
            parse_spec({
                'workflow_name': 'X',
                'trigger': {'type': 'webhook'},
                'steps': [{'id': 's1', 'name': 'A', 'node_type': 'n8n-nodes-base.set'}],
                'test_cases': _VALID_TC,
            })
        self.assertIn('Webhook trigger requires', str(ctx.exception))

    def test_cron_trigger_without_path_accepted(self):
        """Cron triggers don't need a path."""
        spec = parse_spec({
            'workflow_name': 'X',
            'trigger': {'type': 'cron'},
            'steps': [{'id': 's1', 'name': 'A', 'node_type': 'n8n-nodes-base.set'}],
            'test_cases': _VALID_TC,
        })
        self.assertEqual(spec.trigger.type, 'cron')

    def test_valid_gate_references_accepted(self):
        """Gates referencing valid step IDs pass validation."""
        spec = parse_spec({
            'workflow_name': 'X',
            'trigger': {'type': 'webhook', 'path': 'test'},
            'steps': [
                {'id': 's1', 'name': 'Check', 'node_type': 'n8n-nodes-base.if'},
                {'id': 's2', 'name': 'OK', 'node_type': 'n8n-nodes-base.set'},
                {'id': 's3', 'name': 'Err', 'node_type': 'n8n-nodes-base.set'},
            ],
            'gates': [{'after_step': 's1', 'pass_to': 's2', 'fail_to': 's3'}],
            'test_cases': _VALID_TC,
        })
        self.assertEqual(len(spec.gates), 1)

    def test_unsupported_gate_types_are_dropped(self):
        """PM-generated 'sequential' and 'error_branch' gates are silently dropped.

        Only conditional_branch gates drive wiring — leaving the others in
        would cause _build_connections to emit broken edges.
        """
        spec = parse_spec({
            'workflow_name': 'X',
            'trigger': {'type': 'webhook', 'path': 'test'},
            'steps': [
                {'id': 's1', 'name': 'Code', 'node_type': 'n8n-nodes-base.code'},
                {'id': 's2', 'name': 'If', 'node_type': 'n8n-nodes-base.if'},
                {'id': 's3', 'name': 'Ok', 'node_type': 'n8n-nodes-base.set'},
                {'id': 's4', 'name': 'Err', 'node_type': 'n8n-nodes-base.set'},
                {'id': 's5', 'name': '500', 'node_type': 'n8n-nodes-base.set'},
            ],
            'gates': [
                {'after_step': 's2', 'pass_to': 's3', 'fail_to': 's4', 'type': 'conditional_branch'},
                {'after_step': 's3', 'pass_to': 's4', 'type': 'sequential'},
                {'from_step_error': 's1', 'pass_to': 's5', 'type': 'error_branch'},
            ],
            'test_cases': _VALID_TC,
        })
        self.assertEqual(len(spec.gates), 1)
        self.assertEqual(spec.gates[0].after_step, 's2')
        self.assertEqual(spec.gates[0].type, 'conditional_branch')

    def test_empty_type_gate_still_accepted(self):
        """Back-compat: legacy specs with no 'type' field still work."""
        spec = parse_spec({
            'workflow_name': 'X',
            'trigger': {'type': 'webhook', 'path': 'test'},
            'steps': [
                {'id': 's1', 'name': 'A', 'node_type': 'n8n-nodes-base.if'},
                {'id': 's2', 'name': 'B', 'node_type': 'n8n-nodes-base.set'},
                {'id': 's3', 'name': 'C', 'node_type': 'n8n-nodes-base.set'},
            ],
            'gates': [{'after_step': 's1', 'pass_to': 's2', 'fail_to': 's3'}],
            'test_cases': _VALID_TC,
        })
        self.assertEqual(len(spec.gates), 1)


if __name__ == '__main__':
    unittest.main()

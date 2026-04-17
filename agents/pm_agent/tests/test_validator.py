"""Tests for the spec output validator."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from validator import validate_spec

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'workflows', 'test-data')


class TestValidateSpec(unittest.TestCase):
    """Test extended spec validation beyond parse_spec."""

    def test_echo_spec_passes(self):
        """The known-good echo spec passes all validation checks."""
        with open(os.path.join(FIXTURE_DIR, 'echo-spec.json')) as f:
            spec = json.load(f)
        errors = validate_spec(spec)
        self.assertEqual(errors, [], f'Echo spec should pass but got: {errors}')

    def test_missing_workflow_name_caught(self):
        """Missing workflow_name is caught."""
        errors = validate_spec({
            'trigger': {'type': 'webhook'},
            'steps': [{'id': 's1', 'name': 'S', 'node_type': 'n8n-nodes-base.set'}],
            'test_cases': [{'name': 'T', 'input': {}, 'expected': {}}],
        })
        self.assertTrue(any('workflow_name' in e for e in errors))

    def test_too_few_test_cases(self):
        """Fewer than 3 test cases is flagged."""
        spec = {
            'workflow_name': 'Test',
            'trigger': {'type': 'webhook', 'path': 'test'},
            'steps': [{'id': 's1', 'name': 'S', 'node_type': 'n8n-nodes-base.set'}],
            'test_cases': [{'name': 'T', 'input': {}, 'expected': {}}],
        }
        errors = validate_spec(spec)
        self.assertTrue(any('test_cases' in e.lower() or 'test case' in e.lower() for e in errors),
                        f'Expected test_cases warning but got: {errors}')

    def test_llm_step_without_gate_flagged(self):
        """A step with determinism 3.0 and no gate after it is flagged."""
        spec = {
            'workflow_name': 'Test',
            'trigger': {'type': 'webhook', 'path': 'test'},
            'steps': [
                {'id': 's1', 'name': 'LLM Call', 'node_type': 'n8n-nodes-base.openAi',
                 'determinism': '3.0'},
            ],
            'gates': [],
            'test_cases': [
                {'name': 'T1', 'input': {}, 'expected': {}},
                {'name': 'T2', 'input': {}, 'expected': {}},
                {'name': 'T3', 'input': {}, 'expected': {}},
            ],
        }
        errors = validate_spec(spec)
        self.assertTrue(any('gate' in e.lower() for e in errors),
                        f'Expected gate warning for LLM step but got: {errors}')

    def test_llm_step_with_gate_passes(self):
        """A 3.0 step followed by a gate is fine."""
        spec = {
            'workflow_name': 'Test',
            'trigger': {'type': 'webhook', 'path': 'test'},
            'steps': [
                {'id': 's1', 'name': 'LLM', 'node_type': 'n8n-nodes-base.openAi',
                 'determinism': '3.0'},
                {'id': 's2', 'name': 'Check', 'node_type': 'n8n-nodes-base.if'},
            ],
            'gates': [{'after_step': 's1', 'pass_to': 's2'}],
            'test_cases': [
                {'name': 'T1', 'input': {}, 'expected': {}},
                {'name': 'T2', 'input': {}, 'expected': {}},
                {'name': 'T3', 'input': {}, 'expected': {}},
            ],
        }
        errors = validate_spec(spec)
        self.assertFalse(any('gate' in e.lower() for e in errors),
                         f'Gate is present, should not flag: {errors}')

    def test_returns_list(self):
        """validate_spec always returns a list (not None, not exception)."""
        result = validate_spec({})
        self.assertIsInstance(result, list)


if __name__ == '__main__':
    unittest.main()

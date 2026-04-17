"""Unit tests for PM agent decomposer — spec parameter translation."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from decomposer import _translate_spec_params


class TestTranslateSpecParams(unittest.TestCase):
    """Test _translate_spec_params — pure logic, no LLM."""

    def test_translates_set_node_params(self):
        spec = {
            'steps': [{
                'id': 's1',
                'node_type': 'n8n-nodes-base.set',
                'parameters': {
                    'assignments': [
                        {'name': 'status', 'value': 'ok', 'type': 'string'},
                    ]
                },
            }],
        }
        result = _translate_spec_params(spec)
        params = result['steps'][0]['parameters']
        # Should be nested format after translation
        self.assertIn('assignments', params['assignments'])

    def test_translates_if_node_params(self):
        spec = {
            'steps': [{
                'id': 's1',
                'node_type': 'n8n-nodes-base.if',
                'parameters': {
                    'conditions': {
                        'and': [{'field': '={{ $json.x }}', 'operation': 'isNotEmpty'}]
                    }
                },
            }],
        }
        result = _translate_spec_params(spec)
        params = result['steps'][0]['parameters']
        self.assertIn('combinator', params['conditions'])

    def test_passthrough_for_unknown_node_type(self):
        spec = {
            'steps': [{
                'id': 's1',
                'node_type': 'n8n-nodes-base.httpRequest',
                'parameters': {'url': 'https://example.com'},
            }],
        }
        result = _translate_spec_params(spec)
        self.assertEqual(result['steps'][0]['parameters']['url'], 'https://example.com')

    def test_empty_steps_no_crash(self):
        spec = {'steps': []}
        result = _translate_spec_params(spec)
        self.assertEqual(result['steps'], [])

    def test_missing_steps_no_crash(self):
        spec = {}
        result = _translate_spec_params(spec)
        self.assertEqual(result, {})

    def test_step_without_params_no_crash(self):
        spec = {
            'steps': [{'id': 's1', 'node_type': 'n8n-nodes-base.set'}],
        }
        result = _translate_spec_params(spec)
        # No parameters to translate, should pass through
        self.assertNotIn('parameters', result['steps'][0])

    def test_preserves_non_step_fields(self):
        spec = {
            'workflow_name': 'Test',
            'trigger': {'type': 'webhook'},
            'steps': [],
            'test_cases': [],
        }
        result = _translate_spec_params(spec)
        self.assertEqual(result['workflow_name'], 'Test')
        self.assertEqual(result['trigger'], {'type': 'webhook'})


if __name__ == '__main__':
    unittest.main()

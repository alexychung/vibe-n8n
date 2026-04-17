"""Tests for TEST phase — runs test data through the workflow.

Full integration: scaffold → wire → activate → send test data → verify responses.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from client import N8nClient
from scaffold import scaffold
from wire import wire
from test_runner import run_tests, RunResult
from tests.conftest import N8N_AVAILABLE, SKIP_MSG, load_env, load_echo_spec


@unittest.skipUnless(N8N_AVAILABLE, SKIP_MSG)
class TestRunTests(unittest.TestCase):
    """Integration: scaffold → wire → run_tests against echo workflow."""

    @classmethod
    def setUpClass(cls):
        load_env()
        cls.client = N8nClient()
        cls.spec = load_echo_spec()

        # Build the workflow
        cls.workflow_id = scaffold(cls.spec, cls.client)
        wire(cls.spec, cls.client, cls.workflow_id)

        # Run all test cases
        cls.results = run_tests(cls.spec, cls.client, cls.workflow_id)

    @classmethod
    def tearDownClass(cls):
        if cls.workflow_id:
            try:
                cls.client.deactivate_workflow(cls.workflow_id)
            except Exception:
                pass
            try:
                cls.client.delete_workflow(cls.workflow_id)
            except Exception:
                pass

    def test_01_all_results_returned(self):
        """run_tests returns a result for every test case in the spec."""
        self.assertEqual(len(self.results), len(self.spec.test_cases))

    def test_02_happy_path_passes(self):
        """Happy path test case passes."""
        result = next(r for r in self.results if r.test_name == 'Happy path')
        self.assertTrue(result.passed, f'Happy path failed: {result.actual}')
        self.assertEqual(result.actual['status'], 'ok')
        self.assertEqual(result.actual['name'], 'test-item')
        self.assertEqual(result.actual['value'], 42)
        self.assertTrue(len(result.actual.get('received_at', '')) > 0)

    def test_03_empty_name_rejected(self):
        """Empty name is caught by validation gate."""
        result = next(r for r in self.results if r.test_name == 'Empty name rejected')
        self.assertTrue(result.passed, f'Empty name test failed: {result.actual}')
        self.assertEqual(result.actual['status'], 'error')

    def test_04_value_too_high_rejected(self):
        """Value > 100 is caught by validation gate."""
        result = next(r for r in self.results if r.test_name == 'Value too high rejected')
        self.assertTrue(result.passed, f'Value too high test failed: {result.actual}')
        self.assertEqual(result.actual['status'], 'error')

    def test_05_value_negative_rejected(self):
        """Negative value is caught by validation gate."""
        result = next(r for r in self.results if r.test_name == 'Value negative rejected')
        self.assertTrue(result.passed, f'Value negative test failed: {result.actual}')
        self.assertEqual(result.actual['status'], 'error')

    def test_06_missing_fields_rejected(self):
        """Empty object is caught by validation gate."""
        result = next(r for r in self.results if r.test_name == 'Missing fields rejected')
        self.assertTrue(result.passed, f'Missing fields test failed: {result.actual}')
        self.assertEqual(result.actual['status'], 'error')

    def test_07_workflow_deactivated_after_tests(self):
        """Workflow is deactivated after run_tests completes."""
        wf = self.client.get_workflow(self.workflow_id)
        self.assertFalse(wf['active'])


if __name__ == '__main__':
    unittest.main()

"""Unit tests for HARDEN phase — tests automated fixes and the harden loop."""
import os
import sys
import unittest
from unittest.mock import MagicMock, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from harden import harden, _apply_fix
from auditor import Finding


class TestApplyFix(unittest.TestCase):

    def test_fix_no_timeout(self):
        wf = {'settings': {}}
        finding = Finding('best_practices', 'WARNING', 'no_timeout', 'msg')
        _apply_fix(wf, finding)
        self.assertEqual(wf['settings']['executionTimeout'], 300)

    def test_fix_no_error_save(self):
        wf = {'settings': {}}
        finding = Finding('resilience', 'INFO', 'no_error_save', 'msg')
        _apply_fix(wf, finding)
        self.assertEqual(wf['settings']['saveDataErrorExecution'], 'all')

    def test_fix_missing_retry(self):
        wf = {
            'nodes': [
                {'name': 'API', 'type': 'n8n-nodes-base.httpRequest', 'parameters': {}},
                {'name': 'Other', 'type': 'n8n-nodes-base.set', 'parameters': {}},
            ]
        }
        finding = Finding('best_practices', 'WARNING', 'missing_retry', 'msg')
        _apply_fix(wf, finding)
        retry = wf['nodes'][0]['parameters']['options']['retry']
        self.assertTrue(retry['retryOnFail'])
        self.assertEqual(retry['maxTries'], 3)
        # Other node should not be affected
        self.assertNotIn('options', wf['nodes'][1]['parameters'])

    def test_unfixable_finding_is_noop(self):
        wf = {'settings': {}, 'nodes': []}
        finding = Finding('security', 'CRITICAL', 'hardcoded_credentials', 'msg')
        _apply_fix(wf, finding)
        # Should not crash, should not modify anything meaningful
        self.assertEqual(wf['nodes'], [])


class TestHardenLoop(unittest.TestCase):

    def test_clean_workflow_returns_immediately(self):
        """If no actionable findings, harden returns after first audit."""
        client = MagicMock()
        client.get_workflow.return_value = {
            'nodes': [],
            'connections': {},
            'settings': {'executionTimeout': 300, 'saveDataErrorExecution': 'all'},
        }

        findings = harden(client, 'wf-1', max_iterations=3)

        # Only one get_workflow call (the initial audit)
        client.get_workflow.assert_called_once()
        client.update_workflow.assert_not_called()
        # All findings should be INFO or empty
        for f in findings:
            self.assertNotIn(f.severity, ('CRITICAL', 'WARNING'))

    def test_fixes_applied_and_re_audited(self):
        """Harden applies fixes then re-audits."""
        call_count = [0]

        def get_workflow_side_effect(wf_id):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: has a fixable warning
                return {
                    'nodes': [],
                    'connections': {},
                    'settings': {},
                }
            else:
                # After fix: clean
                return {
                    'nodes': [],
                    'connections': {},
                    'settings': {'executionTimeout': 300, 'saveDataErrorExecution': 'all'},
                }

        client = MagicMock()
        client.get_workflow.side_effect = get_workflow_side_effect

        findings = harden(client, 'wf-1', max_iterations=3)

        # Should have called update_workflow to apply fixes
        self.assertTrue(client.update_workflow.called)

    def test_max_iterations_respected(self):
        """Harden stops after max_iterations even if findings remain."""
        client = MagicMock()
        # Always return a workflow with unfixable warnings
        client.get_workflow.return_value = {
            'nodes': [
                {'name': 'Hook', 'type': 'n8n-nodes-base.webhook', 'parameters': {}},
            ],
            'connections': {},
            'settings': {'executionTimeout': 300, 'saveDataErrorExecution': 'all'},
        }

        findings = harden(client, 'wf-1', max_iterations=2)

        # 2 iterations + 1 final audit = 3 get_workflow calls
        self.assertEqual(client.get_workflow.call_count, 3)
        # Should still have the unfixable warning
        warnings = [f for f in findings if f.severity == 'WARNING']
        self.assertTrue(len(warnings) > 0)


if __name__ == '__main__':
    unittest.main()

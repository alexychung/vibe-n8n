"""Unit tests for rollback — snapshot restore + verification."""
import copy
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from rollback import rollback, _verify_structural_match
from snapshot import save_snapshot
from helpers import make_workflow, make_node


class TestVerifyMatch(unittest.TestCase):

    def test_match_ignores_n8n_managed_fields(self):
        a = make_workflow(nodes=[make_node('n1', 'X')])
        b = copy.deepcopy(a)
        a['updatedAt'] = '2026-05-01T00:00:00Z'
        b['active'] = True  # different active state — still considered structurally identical
        self.assertTrue(_verify_structural_match(a, b))

    def test_mismatch_when_node_param_differs(self):
        a = make_workflow(nodes=[make_node('n1', 'X', parameters={'p': 1})])
        b = make_workflow(nodes=[make_node('n1', 'X', parameters={'p': 2})])
        self.assertFalse(_verify_structural_match(a, b))


class TestRollback(unittest.TestCase):

    def _make_client(self, snap_wf, restored_wf=None, activate_returns_active=True):
        """Build a mocked N8nClient.

        After activate_workflow is called, get_workflow returns the workflow
        with active=True (mirrors real n8n behavior). `activate_returns_active`
        flips this to model the flaky-activation case where n8n reports OK
        but the workflow stays inactive.
        """
        client = MagicMock()
        client.deactivate_workflow.return_value = {}
        client.activate_workflow.return_value = {}
        base = restored_wf if restored_wf is not None else snap_wf
        active_state = {'value': bool(base.get('active'))}

        def _activate(_wid):
            if activate_returns_active:
                active_state['value'] = True
            return {}

        def _get(_wid):
            wf = copy.deepcopy(base)
            wf['active'] = active_state['value']
            return wf

        client.activate_workflow.side_effect = _activate
        client.get_workflow.side_effect = _get
        client.update_workflow.side_effect = lambda wid, modifier: modifier({})
        return client

    def test_successful_rollback_no_reactivation(self):
        snap_wf = make_workflow(nodes=[make_node('n1', 'X')])
        with tempfile.TemporaryDirectory() as d:
            snap = save_snapshot('wf-test', snap_wf, d)
            client = self._make_client(snap_wf)

            result = rollback(client, 'wf-test', snap.path,
                              snapshot_was_active=False)

            self.assertTrue(result.restored)
            self.assertFalse(result.reactivated)
            self.assertTrue(result.verification_passed)
            client.activate_workflow.assert_not_called()

    def test_rollback_reactivates_when_snapshot_was_active(self):
        snap_wf = make_workflow(nodes=[make_node('n1', 'X')])
        with tempfile.TemporaryDirectory() as d:
            snap = save_snapshot('wf-test', snap_wf, d)
            client = self._make_client(snap_wf)

            result = rollback(client, 'wf-test', snap.path,
                              snapshot_was_active=True)

            self.assertTrue(result.reactivated)
            client.activate_workflow.assert_called_once_with('wf-test')

    def test_rollback_fails_when_activate_silently_no_ops(self):
        """n8n sometimes returns 200 from /activate but the workflow stays inactive.
        Rollback must retry, then surface the failure rather than reporting success."""
        import time
        snap_wf = make_workflow(nodes=[make_node('n1', 'X')])
        with tempfile.TemporaryDirectory() as d:
            snap = save_snapshot('wf-test', snap_wf, d)
            client = self._make_client(snap_wf, activate_returns_active=False)
            # Patch sleep to keep the test fast
            original_sleep = time.sleep
            time.sleep = lambda _s: None
            try:
                result = rollback(client, 'wf-test', snap.path,
                                  snapshot_was_active=True)
            finally:
                time.sleep = original_sleep
            self.assertTrue(result.restored)
            self.assertFalse(result.reactivated)
            self.assertFalse(result.verification_passed)
            self.assertIn('did not become active', result.error)
            # Should have retried at least twice
            self.assertGreaterEqual(client.activate_workflow.call_count, 2)

    def test_rollback_verification_fails_on_mismatch(self):
        snap_wf = make_workflow(nodes=[make_node('n1', 'X')])
        wrong_wf = make_workflow(nodes=[make_node('n1', 'Y')])  # name diff
        with tempfile.TemporaryDirectory() as d:
            snap = save_snapshot('wf-test', snap_wf, d)
            client = self._make_client(snap_wf, restored_wf=wrong_wf)

            result = rollback(client, 'wf-test', snap.path,
                              snapshot_was_active=False)

            self.assertTrue(result.restored)
            self.assertFalse(result.verification_passed)
            self.assertIn('mismatch', result.error.lower())

    def test_rollback_does_not_modify_snapshot_file(self):
        snap_wf = make_workflow(nodes=[make_node('n1', 'X')])
        with tempfile.TemporaryDirectory() as d:
            snap = save_snapshot('wf-test', snap_wf, d)
            mtime_before = os.path.getmtime(snap.path)
            client = self._make_client(snap_wf)

            rollback(client, 'wf-test', snap.path, snapshot_was_active=False)

            self.assertEqual(os.path.getmtime(snap.path), mtime_before)


class TestSnapshotIntegrity(unittest.TestCase):
    """REGRESSION: load_snapshot used to raise FileNotFoundError /
    JSONDecodeError raw, bypassing change_log persistence. A truncated or
    tampered snapshot would PUT garbage and corrupt the live workflow.
    Both paths now route through RollbackResult."""

    def test_missing_snapshot_file_returns_clean_error(self):
        client = MagicMock()
        result = rollback(client, 'wf-test', '/nonexistent/path.json',
                          snapshot_was_active=False)
        self.assertFalse(result.restored)
        self.assertIn('Cannot load snapshot', result.error)
        client.update_workflow.assert_not_called()

    def test_corrupt_snapshot_returns_clean_error(self):
        with tempfile.TemporaryDirectory() as d:
            bad_path = os.path.join(d, 'bad.json')
            with open(bad_path, 'w') as f:
                f.write('{"this": "is missing nodes/connections/name"}')
            client = MagicMock()
            result = rollback(client, 'wf-test', bad_path, snapshot_was_active=False)
            self.assertFalse(result.restored)
            self.assertIn('missing required field', result.error)
            client.update_workflow.assert_not_called()

    def test_invalid_json_returns_clean_error(self):
        with tempfile.TemporaryDirectory() as d:
            bad_path = os.path.join(d, 'bad.json')
            with open(bad_path, 'w') as f:
                f.write('not valid json at all')
            client = MagicMock()
            result = rollback(client, 'wf-test', bad_path, snapshot_was_active=False)
            self.assertFalse(result.restored)
            self.assertIn('Cannot load snapshot', result.error)


if __name__ == '__main__':
    unittest.main()

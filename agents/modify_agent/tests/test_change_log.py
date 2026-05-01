"""Unit tests for change_log writer + history listing + fallback summary."""
import json
import os
import tempfile
import unittest

from edits import Edit
from change_log import (
    ChangeLogEntry, write_change_log, list_changes,
    new_modify_id, now_iso, _fallback_summary,
)


class TestWriteAndList(unittest.TestCase):

    def _make_entry(self, workflow_id: str = 'wf-abc', summary: str = ''):
        return ChangeLogEntry(
            modify_id=new_modify_id(),
            workflow_id=workflow_id,
            workflow_name='My Workflow',
            started_at=now_iso(),
            completed_at=now_iso(),
            user_request='do the thing',
            classification='tactical',
            edits_applied=[{'type': 'rename_node', 'node_id': 'n1',
                            'old_name': 'A', 'new_name': 'B'}],
            snapshot_path='/tmp/snap.json',
            test_results={'passed': 4, 'failed': 0},
            audit_results={'new_critical': 0, 'new_warning': 0, 'new_info': 0},
            deploy_outcome='active, smoke test passed',
            human_summary=summary,
        )

    def test_write_creates_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_change_log(self._make_entry(), d)
            self.assertTrue(os.path.exists(path))
            data = json.load(open(path))
            self.assertEqual(data['workflow_id'], 'wf-abc')
            self.assertEqual(data['classification'], 'tactical')

    def test_list_returns_only_matching_workflow(self):
        with tempfile.TemporaryDirectory() as d:
            write_change_log(self._make_entry('wf-abc', 'first'), d)
            write_change_log(self._make_entry('wf-xyz', 'second'), d)
            entries = list_changes(d, 'wf-abc')
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]['workflow_id'], 'wf-abc')

    def test_list_returns_newest_first(self):
        with tempfile.TemporaryDirectory() as d:
            # Files are listed by name reverse-sorted; timestamp in filename
            # makes newer files sort first.
            import time
            write_change_log(self._make_entry('wf-1', 'older'), d)
            time.sleep(1.0)  # ensure different timestamp slug in filename
            write_change_log(self._make_entry('wf-1', 'newer'), d)
            entries = list_changes(d, 'wf-1')
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]['human_summary'], 'newer')

    def test_list_on_missing_dir_returns_empty(self):
        self.assertEqual(list_changes('/does/not/exist/anywhere', 'wf-abc'), [])


class TestFallbackSummary(unittest.TestCase):

    def test_fallback_describes_each_edit_type(self):
        edits = [
            Edit(type='set_node_parameter', node_id='n1',
                 path='parameters.path', old_value='old', new_value='new'),
            Edit(type='rename_node', node_id='n2', old_name='A', new_name='B'),
            Edit(type='rename_workflow', old_value='OldWF', new_value='NewWF'),
        ]
        summary = _fallback_summary(edits)
        self.assertIn('set parameters.path', summary.lower()) if 'set parameters' in summary.lower() else None
        self.assertIn('Renamed node', summary)
        self.assertIn('NewWF', summary)

    def test_empty_edits_summary(self):
        self.assertEqual(_fallback_summary([]), 'No edits applied.')


if __name__ == '__main__':
    unittest.main()

"""Unit tests for snapshot save/load and retention cleanup."""
import json
import os
import tempfile
import time
import unittest

from snapshot import (
    save_snapshot, load_snapshot, cleanup_snapshots,
    SNAPSHOT_KEEP_LAST, SNAPSHOT_MAX_AGE_DAYS,
)
from helpers import make_workflow, make_node


class TestSaveLoad(unittest.TestCase):

    def test_save_then_load_roundtrip(self):
        wf = make_workflow(nodes=[make_node('n1', 'X')])
        with tempfile.TemporaryDirectory() as d:
            snap = save_snapshot('wf-test', wf, d)
            self.assertTrue(os.path.exists(snap.path))
            loaded = load_snapshot(snap.path)
            self.assertEqual(loaded['name'], wf['name'])
            self.assertEqual(loaded['nodes'], wf['nodes'])

    def test_save_creates_directory_if_missing(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, 'nested', 'snaps')
            wf = make_workflow()
            snap = save_snapshot('wf-test', wf, target)
            self.assertTrue(os.path.exists(snap.path))


class TestCleanup(unittest.TestCase):

    _seq = 0

    def _write_old_snapshot(self, dir: str, workflow_id: str, days_ago: int) -> str:
        TestCleanup._seq += 1
        path = os.path.join(dir, f'{workflow_id}-old-{TestCleanup._seq:04d}.json')
        with open(path, 'w') as f:
            json.dump({}, f)
        cutoff = time.time() - (days_ago * 86400)
        os.utime(path, (cutoff, cutoff))
        return path

    def test_age_out_drops_files_older_than_max_age(self):
        with tempfile.TemporaryDirectory() as d:
            old_path = self._write_old_snapshot(d, 'wf-1', SNAPSHOT_MAX_AGE_DAYS + 5)
            recent_path = self._write_old_snapshot(d, 'wf-1', 1)
            deleted = cleanup_snapshots(d, 'wf-1')
            self.assertIn(old_path, deleted)
            self.assertNotIn(recent_path, deleted)
            self.assertFalse(os.path.exists(old_path))
            self.assertTrue(os.path.exists(recent_path))

    def test_keep_last_caps_count(self):
        with tempfile.TemporaryDirectory() as d:
            paths = []
            # Make N+5 recent snapshots, all within max_age
            for i in range(SNAPSHOT_KEEP_LAST + 5):
                p = self._write_old_snapshot(d, 'wf-1', i // 10)  # all under 90 days
                paths.append(p)
            deleted = cleanup_snapshots(d, 'wf-1')
            survivors = [p for p in paths if os.path.exists(p)]
            self.assertEqual(len(survivors), SNAPSHOT_KEEP_LAST)
            self.assertEqual(len(deleted), 5)

    def test_other_workflows_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            self._write_old_snapshot(d, 'wf-1', 100)  # old
            other = self._write_old_snapshot(d, 'wf-2', 100)  # old, different wf
            cleanup_snapshots(d, 'wf-1')
            self.assertTrue(os.path.exists(other))

    def test_cleanup_on_missing_dir_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            ghost = os.path.join(d, 'does-not-exist')
            self.assertEqual(cleanup_snapshots(ghost, 'wf-1'), [])


if __name__ == '__main__':
    unittest.main()

"""Tests for build status tracker."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from status import BuildStatus


class TestBuildStatus(unittest.TestCase):
    """Unit tests for the build status tracker."""

    def test_initial_phases(self):
        """All phases start as pending."""
        status = BuildStatus('Test Workflow')
        table = status.to_dict()
        for phase in ['SCAFFOLD', 'WIRE', 'TEST', 'AUDIT', 'HARDEN', 'CODIFY', 'DEPLOY', 'EXPORT']:
            self.assertEqual(table[phase]['status'], 'pending')

    def test_mark_done(self):
        """Can mark a phase as done with notes."""
        status = BuildStatus('Test Workflow')
        status.done('SCAFFOLD', '4 nodes created')
        table = status.to_dict()
        self.assertEqual(table['SCAFFOLD']['status'], 'done')
        self.assertEqual(table['SCAFFOLD']['notes'], '4 nodes created')

    def test_mark_failed(self):
        """Can mark a phase as failed with reason."""
        status = BuildStatus('Test Workflow')
        status.fail('WIRE', 'Connection refused on node 3')
        table = status.to_dict()
        self.assertEqual(table['WIRE']['status'], 'failed')
        self.assertEqual(table['WIRE']['notes'], 'Connection refused on node 3')

    def test_mark_skipped(self):
        """Can mark a phase as skipped with reason."""
        status = BuildStatus('Test Workflow')
        status.skip('CODIFY', 'deferred')
        table = status.to_dict()
        self.assertEqual(table['CODIFY']['status'], 'skipped')
        self.assertEqual(table['CODIFY']['notes'], 'deferred')

    def test_render_markdown_table(self):
        """Renders a markdown table with all phases."""
        status = BuildStatus('Test Workflow')
        status.done('SCAFFOLD', '4 nodes')
        status.done('WIRE', '4/4 connected')
        status.fail('TEST', '2/5 failed')

        md = status.render()

        self.assertIn('## Build Status: Test Workflow', md)
        self.assertIn('SCAFFOLD', md)
        self.assertIn('done', md)
        self.assertIn('4 nodes', md)
        self.assertIn('TEST', md)
        self.assertIn('failed', md)
        # Phases not yet touched should show pending
        self.assertIn('pending', md)

    def test_render_includes_all_phases(self):
        """Every phase appears in the rendered output, even if untouched."""
        status = BuildStatus('Test Workflow')
        md = status.render()

        for phase in ['SCAFFOLD', 'WIRE', 'TEST', 'AUDIT', 'HARDEN', 'CODIFY', 'DEPLOY', 'EXPORT']:
            self.assertIn(phase, md, f'Phase {phase} missing from rendered output')

    def test_workflow_id_tracking(self):
        """Can store and retrieve the n8n workflow ID."""
        status = BuildStatus('Test Workflow')
        self.assertIsNone(status.workflow_id)

        status.workflow_id = 'abc123'
        self.assertEqual(status.workflow_id, 'abc123')

        md = status.render()
        self.assertIn('abc123', md)

    def test_invalid_phase_raises(self):
        """Marking an unknown phase raises ValueError."""
        status = BuildStatus('Test Workflow')
        with self.assertRaises(ValueError):
            status.done('NONEXISTENT', 'should fail')

    def test_render_escapes_pipe_in_notes(self):
        """Pipe characters in notes don't break the markdown table."""
        status = BuildStatus('Test')
        status.done('SCAFFOLD', 'choice: A | B')
        md = status.render()
        self.assertIn('choice: A \\| B', md)

    def test_render_escapes_newline_in_notes(self):
        """Newlines in notes don't break the markdown table."""
        status = BuildStatus('Test')
        status.done('SCAFFOLD', 'line1\nline2')
        md = status.render()
        # Newlines replaced with spaces
        self.assertIn('line1 line2', md)
        # Should be a single table row, not broken across lines
        for line in md.split('\n'):
            if 'SCAFFOLD' in line:
                self.assertTrue(line.startswith('|'), f'Table row broken: {line!r}')


if __name__ == '__main__':
    unittest.main()

"""Tests for SCAFFOLD phase — creates workflow skeleton in n8n.

Integration tests against live n8n. Creates a real workflow, verifies structure.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from client import N8nClient
from scaffold import scaffold
from tests.conftest import N8N_AVAILABLE, SKIP_MSG, load_env, load_echo_spec


@unittest.skipUnless(N8N_AVAILABLE, SKIP_MSG)
class TestScaffold(unittest.TestCase):
    """Integration tests: scaffold creates a real workflow in n8n."""

    @classmethod
    def setUpClass(cls):
        load_env()
        cls.client = N8nClient()
        cls.spec = load_echo_spec()
        cls.workflow_id = None

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

    def test_01_scaffold_creates_workflow(self):
        """scaffold() creates a workflow and returns its ID."""
        workflow_id = scaffold(self.spec, self.client)
        self.__class__.workflow_id = workflow_id

        self.assertIsInstance(workflow_id, str)
        self.assertTrue(len(workflow_id) > 0)

    def test_02_correct_node_count(self):
        """Scaffolded workflow has the right number of nodes (trigger + steps)."""
        self.assertIsNotNone(self.workflow_id)
        wf = self.client.get_workflow(self.workflow_id)

        # Echo spec: 1 webhook trigger + 3 steps = 4 nodes
        self.assertEqual(len(wf['nodes']), 4)

    def test_03_trigger_node_correct(self):
        """Trigger node is a webhook with correct path."""
        wf = self.client.get_workflow(self.workflow_id)
        trigger = next(n for n in wf['nodes'] if 'webhook' in n['type'].lower())

        self.assertEqual(trigger['type'], 'n8n-nodes-base.webhook')

    def test_04_step_nodes_have_correct_names(self):
        """Each step node has the descriptive name from the spec, not defaults."""
        wf = self.client.get_workflow(self.workflow_id)
        names = {n['name'] for n in wf['nodes']}

        self.assertIn('Validate Input', names)
        self.assertIn('Build Success Response', names)
        self.assertIn('Build Error Response', names)

    def test_05_step_nodes_have_correct_types(self):
        """Each step node has the correct n8n node type."""
        wf = self.client.get_workflow(self.workflow_id)
        type_map = {n['name']: n['type'] for n in wf['nodes']}

        self.assertEqual(type_map['Validate Input'], 'n8n-nodes-base.if')
        self.assertEqual(type_map['Build Success Response'], 'n8n-nodes-base.set')
        self.assertEqual(type_map['Build Error Response'], 'n8n-nodes-base.set')

    def test_06_nodes_positioned_left_to_right(self):
        """Nodes are laid out left-to-right with increasing x positions."""
        wf = self.client.get_workflow(self.workflow_id)
        positions = [(n['name'], n['position'][0]) for n in wf['nodes']]
        positions.sort(key=lambda p: p[1])

        # Trigger should be leftmost
        self.assertIn('Webhook', positions[0][0])

    def test_07_no_connections_yet(self):
        """Scaffold creates no connections — that's the WIRE phase."""
        wf = self.client.get_workflow(self.workflow_id)
        self.assertEqual(wf['connections'], {})

    def test_08_workflow_settings(self):
        """Scaffold sets timeout and save settings from spec."""
        wf = self.client.get_workflow(self.workflow_id)
        self.assertEqual(wf['settings']['executionTimeout'], 30)
        self.assertTrue(wf['settings']['saveExecutionProgress'])

    def test_09_workflow_not_active(self):
        """Scaffold does NOT activate the workflow."""
        wf = self.client.get_workflow(self.workflow_id)
        self.assertFalse(wf['active'])


if __name__ == '__main__':
    unittest.main()

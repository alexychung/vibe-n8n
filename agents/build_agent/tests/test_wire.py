"""Tests for WIRE phase — configures and connects nodes.

Integration tests. Scaffolds a workflow first, then wires it, then verifies.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from client import N8nClient
from scaffold import scaffold
from wire import wire
from tests.conftest import N8N_AVAILABLE, SKIP_MSG, load_env, load_echo_spec


@unittest.skipUnless(N8N_AVAILABLE, SKIP_MSG)
class TestWire(unittest.TestCase):
    """Integration tests: wire configures and connects a scaffolded workflow."""

    @classmethod
    def setUpClass(cls):
        load_env()
        cls.client = N8nClient()
        cls.spec = load_echo_spec()

        # Scaffold first, then wire
        cls.workflow_id = scaffold(cls.spec, cls.client)
        wire(cls.spec, cls.client, cls.workflow_id)
        cls.wf = cls.client.get_workflow(cls.workflow_id)

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

    def test_01_trigger_connected_to_if(self):
        """Webhook trigger connects to the Validate Input (IF) node."""
        conns = self.wf['connections']
        # Find the trigger node's name
        trigger_name = next(
            n['name'] for n in self.wf['nodes'] if 'webhook' in n['type'].lower()
        )
        self.assertIn(trigger_name, conns)
        targets = [c['node'] for c in conns[trigger_name]['main'][0]]
        self.assertIn('Validate Input', targets)

    def test_02_if_true_branch_to_success(self):
        """IF node true branch (output 0) connects to Build Success Response."""
        conns = self.wf['connections']
        if_outputs = conns['Validate Input']['main']
        true_targets = [c['node'] for c in if_outputs[0]]
        self.assertIn('Build Success Response', true_targets)

    def test_03_if_false_branch_to_error(self):
        """IF node false branch (output 1) connects to Build Error Response."""
        conns = self.wf['connections']
        if_outputs = conns['Validate Input']['main']
        false_targets = [c['node'] for c in if_outputs[1]]
        self.assertIn('Build Error Response', false_targets)

    def test_04_set_node_has_parameters(self):
        """Success Set node has assignment parameters configured."""
        success_node = next(
            n for n in self.wf['nodes'] if n['name'] == 'Build Success Response'
        )
        assignments = success_node['parameters'].get('assignments', {})
        self.assertTrue(
            len(assignments.get('assignments', [])) > 0,
            'Success node should have assignment parameters'
        )

    def test_05_if_node_has_conditions(self):
        """IF node has condition parameters configured."""
        if_node = next(
            n for n in self.wf['nodes'] if n['name'] == 'Validate Input'
        )
        params = if_node['parameters']
        self.assertTrue(
            'conditions' in params or 'options' in params,
            'IF node should have condition parameters'
        )

    def test_06_error_set_node_has_parameters(self):
        """Error Set node has assignment parameters configured."""
        error_node = next(
            n for n in self.wf['nodes'] if n['name'] == 'Build Error Response'
        )
        assignments = error_node['parameters'].get('assignments', {})
        self.assertTrue(
            len(assignments.get('assignments', [])) > 0,
            'Error node should have assignment parameters'
        )

    def test_07_webhook_response_mode(self):
        """Webhook trigger has responseMode=lastNode so it returns the Set node output."""
        trigger = next(
            n for n in self.wf['nodes'] if 'webhook' in n['type'].lower()
        )
        self.assertEqual(trigger['parameters'].get('responseMode'), 'lastNode')


if __name__ == '__main__':
    unittest.main()

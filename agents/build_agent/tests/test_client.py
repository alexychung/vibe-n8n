"""Tests for n8n API client.

These tests run against a live n8n instance. They create, modify, and delete
a real workflow to verify all API operations work correctly.

Requires: N8N_API_KEY and N8N_BASE_URL in environment (or ../.env)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from client import N8nClient
from tests.conftest import N8N_AVAILABLE, SKIP_MSG, load_env


@unittest.skipUnless(N8N_AVAILABLE, SKIP_MSG)
class TestN8nClient(unittest.TestCase):
    """Integration tests against live n8n instance."""

    @classmethod
    def setUpClass(cls):
        load_env()
        cls.client = N8nClient(
            base_url=os.environ['N8N_BASE_URL'],
            api_key=os.environ['N8N_API_KEY'],
        )
        cls.created_workflow_id = None

    @classmethod
    def tearDownClass(cls):
        """Clean up any workflow we created."""
        if cls.created_workflow_id:
            try:
                cls.client.deactivate_workflow(cls.created_workflow_id)
            except Exception:
                pass
            try:
                cls.client.delete_workflow(cls.created_workflow_id)
            except Exception:
                pass

    def test_01_create_workflow(self):
        """POST /api/v1/workflows — creates a workflow with nodes and settings."""
        result = self.client.create_workflow(
            name='Build Agent Test',
            nodes=[
                {
                    'id': 'trigger-1',
                    'name': 'Test Webhook',
                    'type': 'n8n-nodes-base.webhook',
                    'typeVersion': 2,
                    'position': [250, 300],
                    'parameters': {
                        'path': 'build-agent-test',
                        'httpMethod': 'POST',
                        'responseMode': 'lastNode',
                    },
                    'webhookId': 'build-agent-test-hook',
                },
                {
                    'id': 'set-1',
                    'name': 'Echo',
                    'type': 'n8n-nodes-base.set',
                    'typeVersion': 3.4,
                    'position': [500, 300],
                    'parameters': {
                        'assignments': {
                            'assignments': [
                                {'id': 'a1', 'name': 'status', 'value': 'ok', 'type': 'string'},
                            ]
                        }
                    },
                },
            ],
            connections={
                'Test Webhook': {
                    'main': [[{'node': 'Echo', 'type': 'main', 'index': 0}]]
                }
            },
            settings={'executionTimeout': 30, 'saveExecutionProgress': True},
        )

        self.assertIn('id', result)
        self.assertEqual(result['name'], 'Build Agent Test')
        self.assertEqual(len(result['nodes']), 2)
        self.assertFalse(result['active'])

        # Store for subsequent tests
        self.__class__.created_workflow_id = result['id']

    def test_02_get_workflow(self):
        """GET /api/v1/workflows/{id} — retrieves the workflow we just created."""
        self.assertIsNotNone(self.created_workflow_id, 'No workflow created yet')

        result = self.client.get_workflow(self.created_workflow_id)

        self.assertEqual(result['id'], self.created_workflow_id)
        self.assertEqual(result['name'], 'Build Agent Test')
        self.assertEqual(len(result['nodes']), 2)

    def test_03_list_workflows(self):
        """GET /api/v1/workflows — lists workflows including ours."""
        self.assertIsNotNone(self.created_workflow_id, 'No workflow created yet')

        result = self.client.list_workflows()

        ids = [w['id'] for w in result]
        self.assertIn(self.created_workflow_id, ids)

    def test_04_update_workflow(self):
        """PUT /api/v1/workflows/{id} via update_workflow(id, modifier_fn).

        Verifies the GET-modify-PUT pattern: modifier receives current workflow,
        returns modified version, client PUTs it back.
        """
        self.assertIsNotNone(self.created_workflow_id, 'No workflow created yet')

        def add_description(wf):
            wf['name'] = 'Build Agent Test (updated)'
            return wf

        result = self.client.update_workflow(self.created_workflow_id, add_description)

        self.assertEqual(result['name'], 'Build Agent Test (updated)')
        # Verify it persisted
        fetched = self.client.get_workflow(self.created_workflow_id)
        self.assertEqual(fetched['name'], 'Build Agent Test (updated)')

    def test_05_activate_workflow(self):
        """POST /api/v1/workflows/{id}/activate."""
        self.assertIsNotNone(self.created_workflow_id, 'No workflow created yet')

        result = self.client.activate_workflow(self.created_workflow_id)
        self.assertTrue(result['active'])

    def test_06_send_webhook(self):
        """POST /webhook/{path} — sends data to active workflow, gets response."""
        self.assertIsNotNone(self.created_workflow_id, 'No workflow created yet')

        result = self.client.send_webhook('build-agent-test', {'msg': 'hello'})

        self.assertEqual(result['status'], 'ok')

    def test_07_list_executions(self):
        """GET /api/v1/executions — finds the execution from our webhook call."""
        self.assertIsNotNone(self.created_workflow_id, 'No workflow created yet')

        result = self.client.list_executions(workflow_id=self.created_workflow_id)

        self.assertGreaterEqual(len(result), 1)
        self.assertEqual(result[0]['status'], 'success')

    def test_08_deactivate_workflow(self):
        """POST /api/v1/workflows/{id}/deactivate."""
        self.assertIsNotNone(self.created_workflow_id, 'No workflow created yet')

        result = self.client.deactivate_workflow(self.created_workflow_id)
        self.assertFalse(result['active'])

    def test_09_delete_workflow(self):
        """DELETE /api/v1/workflows/{id} — cleans up."""
        self.assertIsNotNone(self.created_workflow_id, 'No workflow created yet')

        result = self.client.delete_workflow(self.created_workflow_id)
        self.assertEqual(result['id'], self.created_workflow_id)

        # Verify it's gone
        remaining = self.client.list_workflows()
        ids = [w['id'] for w in remaining]
        self.assertNotIn(self.created_workflow_id, ids)

        # Prevent tearDown from trying to delete again
        self.__class__.created_workflow_id = None

    def test_10_list_credentials(self):
        """GET /api/v1/credentials — lists available credentials (may be empty)."""
        result = self.client.list_credentials()
        self.assertIsInstance(result, list)


if __name__ == '__main__':
    unittest.main()

"""Unit tests for PM agent auditor — workflow conflict detection."""
import importlib.util
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Load pm_agent's auditor explicitly to avoid name collision with build_agent's auditor
_pm_auditor_path = os.path.join(os.path.dirname(__file__), '..', 'auditor.py')
_spec = importlib.util.spec_from_file_location('pm_auditor', _pm_auditor_path)
pm_auditor = importlib.util.module_from_spec(_spec)

# Ensure build_agent is on path for the client import inside pm auditor
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'build_agent'))
_spec.loader.exec_module(pm_auditor)

audit_existing_workflows = pm_auditor.audit_existing_workflows
render_audit_summary = pm_auditor.render_audit_summary

from client import N8nApiError


class TestAuditExistingWorkflows(unittest.TestCase):

    @patch.object(pm_auditor, 'N8nClient')
    def test_returns_workflow_count(self, MockClient):
        client = MockClient.return_value
        client.list_workflows.return_value = [
            {'name': 'WF1', 'id': '1', 'active': True, 'nodes': []},
            {'name': 'WF2', 'id': '2', 'active': False, 'nodes': []},
        ]
        result = audit_existing_workflows({'trigger': 'cron'})
        self.assertEqual(result['workflow_count'], 2)
        self.assertEqual(len(result['workflows']), 2)

    @patch.object(pm_auditor, 'N8nClient')
    def test_detects_webhook_conflict(self, MockClient):
        client = MockClient.return_value
        client.list_workflows.return_value = [{
            'name': 'Existing Hook',
            'id': '1',
            'active': True,
            'nodes': [{
                'type': 'n8n-nodes-base.webhook',
                'parameters': {'path': 'my-hook'},
            }],
        }]
        result = audit_existing_workflows({'trigger': 'webhook'})
        self.assertTrue(len(result['conflicts']) > 0)
        self.assertIn('my-hook', result['conflicts'][0])

    @patch.object(pm_auditor, 'N8nClient')
    def test_no_conflict_for_non_webhook(self, MockClient):
        client = MockClient.return_value
        client.list_workflows.return_value = [{
            'name': 'Existing',
            'id': '1',
            'active': True,
            'nodes': [{'type': 'n8n-nodes-base.webhook', 'parameters': {'path': 'x'}}],
        }]
        result = audit_existing_workflows({'trigger': 'cron'})
        self.assertEqual(result['conflicts'], [])

    @patch.object(pm_auditor, 'N8nClient')
    def test_handles_api_error_gracefully(self, MockClient):
        client = MockClient.return_value
        client.list_workflows.side_effect = N8nApiError(0, 'Connection refused', 'http://localhost')
        result = audit_existing_workflows({})
        self.assertEqual(result['workflow_count'], 0)
        self.assertIn('Connection refused', result['error'])

    @patch.object(pm_auditor, 'N8nClient')
    def test_extracts_trigger_types(self, MockClient):
        client = MockClient.return_value
        client.list_workflows.return_value = [{
            'name': 'WF',
            'id': '1',
            'active': True,
            'nodes': [
                {'type': 'n8n-nodes-base.webhook', 'parameters': {}},
                {'type': 'n8n-nodes-base.set', 'parameters': {}},
            ],
        }]
        result = audit_existing_workflows({})
        self.assertIn('n8n-nodes-base.webhook', result['workflows'][0]['trigger_types'])


class TestRenderAuditSummary(unittest.TestCase):

    def test_renders_empty_audit(self):
        audit = {'workflow_count': 0, 'workflows': [], 'conflicts': []}
        result = render_audit_summary(audit)
        self.assertIn('Existing workflows: 0', result)
        self.assertIn('No conflicts', result)

    def test_renders_workflows(self):
        audit = {
            'workflow_count': 1,
            'workflows': [{'name': 'Test WF', 'active': True, 'node_count': 5}],
            'conflicts': [],
        }
        result = render_audit_summary(audit)
        self.assertIn('Test WF', result)
        self.assertIn('active', result)
        self.assertIn('5 nodes', result)

    def test_renders_conflicts(self):
        audit = {
            'workflow_count': 1,
            'workflows': [],
            'conflicts': ['Webhook path conflict with WF1'],
        }
        result = render_audit_summary(audit)
        self.assertIn('Potential conflicts', result)
        self.assertIn('WF1', result)

    def test_renders_error(self):
        audit = {
            'workflow_count': 0,
            'workflows': [],
            'conflicts': [],
            'error': 'Connection refused',
        }
        result = render_audit_summary(audit)
        self.assertIn('Could not connect', result)


if __name__ == '__main__':
    unittest.main()

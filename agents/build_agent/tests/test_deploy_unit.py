"""Unit tests for DEPLOY phase — tests with mocked client."""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from models import Trigger, Step, TestCase, WorkflowSpec
from deploy import deploy
from client import N8nApiError


def _make_spec(**overrides):
    defaults = dict(
        workflow_name='Test',
        trigger=Trigger(type='webhook', path='test-hook', method='POST'),
        steps=[Step(id='s1', name='A', node_type='n8n-nodes-base.set')],
        gates=[],
        test_cases=[TestCase(name='tc1', input={'a': 1}, expected={'status': 'ok'})],
    )
    defaults.update(overrides)
    return WorkflowSpec(**defaults)


class TestDeploy(unittest.TestCase):

    def test_activates_workflow(self):
        spec = _make_spec()
        client = MagicMock()
        client.activate_workflow.return_value = {'active': True}
        client.send_webhook.return_value = {'status': 'ok'}

        result = deploy(spec, client, 'wf-1')

        client.activate_workflow.assert_called_once_with('wf-1')
        self.assertTrue(result['active'])

    def test_smoke_test_passes(self):
        spec = _make_spec()
        client = MagicMock()
        client.activate_workflow.return_value = {'active': True}
        client.send_webhook.return_value = {'status': 'ok'}

        result = deploy(spec, client, 'wf-1')

        self.assertTrue(result['smoke_test_passed'])
        client.send_webhook.assert_called_once_with('test-hook', {'a': 1})

    def test_smoke_test_fails(self):
        spec = _make_spec()
        client = MagicMock()
        client.activate_workflow.return_value = {'active': True}
        client.send_webhook.return_value = {'status': 'error'}

        result = deploy(spec, client, 'wf-1')

        self.assertFalse(result['smoke_test_passed'])
        self.assertIn('status', result['smoke_test_error'])

    def test_smoke_test_api_error(self):
        spec = _make_spec()
        client = MagicMock()
        client.activate_workflow.return_value = {'active': True}
        client.send_webhook.side_effect = N8nApiError(500, 'Internal error', '/webhook/test')

        result = deploy(spec, client, 'wf-1')

        self.assertFalse(result['smoke_test_passed'])
        self.assertIn('Internal error', result['smoke_test_error'])

    def test_no_webhook_path_skips_smoke(self):
        spec = _make_spec(trigger=Trigger(type='cron', path=''))
        client = MagicMock()
        client.activate_workflow.return_value = {'active': True}

        result = deploy(spec, client, 'wf-1')

        self.assertFalse(result['smoke_test_passed'])
        client.send_webhook.assert_not_called()

    def test_no_test_cases_skips_smoke(self):
        spec = _make_spec(test_cases=[])
        client = MagicMock()
        client.activate_workflow.return_value = {'active': True}

        result = deploy(spec, client, 'wf-1')

        self.assertFalse(result['smoke_test_passed'])
        client.send_webhook.assert_not_called()

    def test_activate_failure_skips_smoke_test(self):
        spec = _make_spec()
        client = MagicMock()
        client.activate_workflow.side_effect = N8nApiError(400, 'Validation failed', '/activate')

        result = deploy(spec, client, 'wf-1')

        self.assertFalse(result['active'])
        self.assertFalse(result['smoke_test_passed'])
        self.assertIn('Validation failed', result['smoke_test_error'])
        client.send_webhook.assert_not_called()

    def test_result_includes_workflow_id(self):
        spec = _make_spec()
        client = MagicMock()
        client.activate_workflow.return_value = {'active': True}
        client.send_webhook.return_value = {'status': 'ok'}

        result = deploy(spec, client, 'wf-1')

        self.assertEqual(result['workflow_id'], 'wf-1')


if __name__ == '__main__':
    unittest.main()

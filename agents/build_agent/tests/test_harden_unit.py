"""Unit tests for HARDEN phase — tests automated fixes and the harden loop."""
import os
import sys
import unittest
from unittest.mock import MagicMock, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from harden import harden, _apply_fix, _create_webhook_auth_credentials, GeneratedAuth, WEBHOOK_AUTH_HEADER
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

    def test_fix_missing_webhook_auth_attaches_credential(self):
        """_apply_fix wires the pre-created credential into the matching node."""
        wf = {
            'nodes': [
                {'name': 'Hook', 'type': 'n8n-nodes-base.webhook', 'parameters': {}},
                {'name': 'Other', 'type': 'n8n-nodes-base.set', 'parameters': {}},
            ]
        }
        auth_by_node = {
            'Hook': GeneratedAuth(
                node_name='Hook',
                header_name=WEBHOOK_AUTH_HEADER,
                token='t0k3n',
                credential_id='cred-1',
                credential_name='My Wf — Hook auth',
            )
        }
        finding = Finding('security', 'WARNING', 'missing_webhook_auth', 'msg')
        _apply_fix(wf, finding, auth_by_node)
        hook = wf['nodes'][0]
        self.assertEqual(hook['parameters']['authentication'], 'headerAuth')
        self.assertEqual(hook['credentials']['httpHeaderAuth'], {
            'id': 'cred-1',
            'name': 'My Wf — Hook auth',
        })
        # Unrelated node untouched
        self.assertNotIn('credentials', wf['nodes'][1])

    def test_fix_missing_webhook_auth_without_assignment_is_noop(self):
        """If no assignment for the node (edge case), nothing is touched."""
        wf = {'nodes': [{'name': 'Hook', 'type': 'n8n-nodes-base.webhook', 'parameters': {}}]}
        finding = Finding('security', 'WARNING', 'missing_webhook_auth', 'msg')
        _apply_fix(wf, finding, {})
        self.assertNotIn('authentication', wf['nodes'][0]['parameters'])
        self.assertNotIn('credentials', wf['nodes'][0])


class TestCreateWebhookAuthCredentials(unittest.TestCase):

    def test_creates_one_credential_per_unprotected_webhook(self):
        client = MagicMock()
        client.create_credential.side_effect = lambda name, type, data: {
            'id': f'cred-{name[-1]}',
            'name': name,
            'type': type,
        }
        wf = {
            'nodes': [
                {'name': 'HookA', 'type': 'n8n-nodes-base.webhook', 'parameters': {}},
                {'name': 'HookB', 'type': 'n8n-nodes-base.webhook', 'parameters': {'authentication': 'none'}},
                {
                    'name': 'SecuredHook',
                    'type': 'n8n-nodes-base.webhook',
                    'parameters': {'authentication': 'headerAuth'},
                },
                {'name': 'NotAHook', 'type': 'n8n-nodes-base.set', 'parameters': {}},
            ]
        }
        findings = [Finding('security', 'WARNING', 'missing_webhook_auth', 'HookA')]

        auth = _create_webhook_auth_credentials(client, wf, findings, 'My Wf')

        self.assertEqual(len(auth), 2)
        names = {a.node_name for a in auth}
        self.assertEqual(names, {'HookA', 'HookB'})
        # Tokens are non-empty and distinct
        tokens = [a.token for a in auth]
        self.assertTrue(all(t and len(t) > 20 for t in tokens))
        self.assertNotEqual(tokens[0], tokens[1])
        # Header name is our standard
        for a in auth:
            self.assertEqual(a.header_name, WEBHOOK_AUTH_HEADER)
        # API calls used httpHeaderAuth type with correct data shape
        for call_args in client.create_credential.call_args_list:
            kwargs = call_args.kwargs
            self.assertEqual(kwargs['type'], 'httpHeaderAuth')
            self.assertEqual(kwargs['data']['name'], WEBHOOK_AUTH_HEADER)
            self.assertTrue(kwargs['data']['value'])

    def test_no_finding_means_no_api_call(self):
        client = MagicMock()
        wf = {'nodes': [{'name': 'Hook', 'type': 'n8n-nodes-base.webhook', 'parameters': {}}]}
        auth = _create_webhook_auth_credentials(client, wf, [], 'Wf')
        self.assertEqual(auth, [])
        client.create_credential.assert_not_called()


class TestHardenWebhookAuthIntegration(unittest.TestCase):

    def test_end_to_end_webhook_auth_fix(self):
        """A workflow with an unauth webhook gets a credential and attachment on harden."""
        # First get: unauth webhook. After update, the returned wf has the auth set.
        call_count = [0]
        applied_wf = {}

        def get_workflow_side_effect(wf_id):
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    'name': 'My Wf',
                    'nodes': [
                        {'name': 'Hook', 'type': 'n8n-nodes-base.webhook', 'parameters': {}},
                    ],
                    'connections': {},
                    'settings': {'executionTimeout': 300, 'saveDataErrorExecution': 'all'},
                }
            return applied_wf

        def update_side_effect(wf_id, modifier):
            wf = {
                'name': 'My Wf',
                'nodes': [
                    {'name': 'Hook', 'type': 'n8n-nodes-base.webhook', 'parameters': {}},
                ],
                'connections': {},
                'settings': {'executionTimeout': 300, 'saveDataErrorExecution': 'all'},
            }
            applied_wf.update(modifier(wf))
            return applied_wf

        client = MagicMock()
        client.get_workflow.side_effect = get_workflow_side_effect
        client.update_workflow.side_effect = update_side_effect
        client.create_credential.return_value = {'id': 'cred-xyz', 'name': 'stub'}

        result = harden(client, 'wf-1', workflow_name='My Wf', max_iterations=3)

        # One credential created, one auth assignment returned
        client.create_credential.assert_called_once()
        self.assertEqual(len(result.generated_auth), 1)
        assignment = result.generated_auth[0]
        self.assertEqual(assignment.node_name, 'Hook')
        self.assertEqual(assignment.header_name, WEBHOOK_AUTH_HEADER)
        self.assertEqual(assignment.credential_id, 'cred-xyz')
        self.assertTrue(assignment.token)

        # After update, the webhook node should have auth attached
        hook = applied_wf['nodes'][0]
        self.assertEqual(hook['parameters']['authentication'], 'headerAuth')
        self.assertEqual(hook['credentials']['httpHeaderAuth']['id'], 'cred-xyz')


class TestHardenLoop(unittest.TestCase):

    def test_clean_workflow_returns_immediately(self):
        """If no actionable findings, harden returns after first audit."""
        client = MagicMock()
        client.get_workflow.return_value = {
            'nodes': [],
            'connections': {},
            'settings': {'executionTimeout': 300, 'saveDataErrorExecution': 'all'},
        }

        result = harden(client, 'wf-1', max_iterations=3)

        # Only one get_workflow call (the initial audit)
        client.get_workflow.assert_called_once()
        client.update_workflow.assert_not_called()
        client.create_credential.assert_not_called()
        # All findings should be INFO or empty
        for f in result.findings:
            self.assertNotIn(f.severity, ('CRITICAL', 'WARNING'))
        self.assertEqual(result.generated_auth, [])

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
        """Harden stops after max_iterations even if findings remain.

        Uses a non-webhook unfixable finding (hardcoded credential) so the
        loop keeps running without the webhook-auth fix short-circuiting it.
        """
        client = MagicMock()
        client.get_workflow.return_value = {
            'nodes': [
                {
                    'name': 'API',
                    'type': 'n8n-nodes-base.httpRequest',
                    'parameters': {'url': 'sk-abcdefghijklmnopqrstuvwxyz123456'},
                },
            ],
            'connections': {},
            'settings': {'executionTimeout': 300, 'saveDataErrorExecution': 'all'},
        }

        result = harden(client, 'wf-1', max_iterations=2)

        # 2 iterations + 1 final audit = 3 get_workflow calls
        self.assertEqual(client.get_workflow.call_count, 3)
        # Should still have the unfixable finding (hardcoded_credentials = CRITICAL)
        critical = [f for f in result.findings if f.severity == 'CRITICAL']
        self.assertTrue(len(critical) > 0)


if __name__ == '__main__':
    unittest.main()

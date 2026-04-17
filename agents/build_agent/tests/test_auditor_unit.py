"""Unit tests for AUDIT phase — tests audit checks against crafted workflow JSON."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from auditor import audit_workflow, render_findings, Finding


def _make_workflow(**overrides):
    wf = {
        'nodes': [],
        'connections': {},
        'settings': {
            'executionTimeout': 300,
            'saveDataErrorExecution': 'all',
        },
    }
    wf.update(overrides)
    return wf


class TestSecurityAudit(unittest.TestCase):

    def test_detects_openai_key(self):
        wf = _make_workflow(nodes=[{
            'name': 'LLM',
            'type': 'n8n-nodes-base.httpRequest',
            'parameters': {'headers': {'Authorization': 'Bearer sk-abcdefghijklmnopqrstuvwxyz1234'}},
        }])
        findings = audit_workflow(wf)
        crit = [f for f in findings if f.check == 'hardcoded_credentials']
        self.assertEqual(len(crit), 1)
        self.assertEqual(crit[0].severity, 'CRITICAL')

    def test_detects_slack_token(self):
        wf = _make_workflow(nodes=[{
            'name': 'Slack',
            'parameters': {'token': 'xoxb-1234567890-abcdefghij'},
            'type': 'test',
        }])
        findings = audit_workflow(wf)
        crit = [f for f in findings if f.check == 'hardcoded_credentials']
        self.assertTrue(len(crit) >= 1)

    def test_detects_github_pat(self):
        wf = _make_workflow(nodes=[{
            'name': 'GH',
            'parameters': {'auth': 'ghp_aaaaaaaabbbbbbbbccccccccddddddddeeee'},
            'type': 'test',
        }])
        findings = audit_workflow(wf)
        crit = [f for f in findings if f.check == 'hardcoded_credentials']
        self.assertTrue(len(crit) >= 1)

    def test_clean_workflow_no_credential_findings(self):
        wf = _make_workflow(nodes=[{
            'name': 'Safe Node',
            'parameters': {'url': 'https://example.com'},
            'type': 'test',
        }])
        findings = audit_workflow(wf)
        crit = [f for f in findings if f.check == 'hardcoded_credentials']
        self.assertEqual(len(crit), 0)

    def test_webhook_without_auth_flagged(self):
        wf = _make_workflow(nodes=[{
            'name': 'Hook',
            'type': 'n8n-nodes-base.webhook',
            'parameters': {},
        }])
        findings = audit_workflow(wf)
        auth_findings = [f for f in findings if f.check == 'missing_webhook_auth']
        self.assertEqual(len(auth_findings), 1)
        self.assertEqual(auth_findings[0].severity, 'WARNING')

    def test_webhook_with_auth_not_flagged(self):
        wf = _make_workflow(nodes=[{
            'name': 'Hook',
            'type': 'n8n-nodes-base.webhook',
            'parameters': {'authentication': 'headerAuth'},
        }])
        findings = audit_workflow(wf)
        auth_findings = [f for f in findings if f.check == 'missing_webhook_auth']
        self.assertEqual(len(auth_findings), 0)

    def test_webhook_auth_none_flagged(self):
        wf = _make_workflow(nodes=[{
            'name': 'Hook',
            'type': 'n8n-nodes-base.webhook',
            'parameters': {'authentication': 'none'},
        }])
        findings = audit_workflow(wf)
        auth_findings = [f for f in findings if f.check == 'missing_webhook_auth']
        self.assertEqual(len(auth_findings), 1)


class TestBestPracticesAudit(unittest.TestCase):

    def test_default_name_flagged(self):
        wf = _make_workflow(nodes=[
            {'name': 'HTTP Request', 'type': 'test', 'parameters': {}},
        ])
        findings = audit_workflow(wf)
        name_findings = [f for f in findings if f.check == 'default_node_name']
        self.assertEqual(len(name_findings), 1)

    def test_custom_name_not_flagged(self):
        wf = _make_workflow(nodes=[
            {'name': 'Fetch User Data', 'type': 'test', 'parameters': {}},
        ])
        findings = audit_workflow(wf)
        name_findings = [f for f in findings if f.check == 'default_node_name']
        self.assertEqual(len(name_findings), 0)

    def test_no_timeout_flagged(self):
        wf = _make_workflow(settings={'executionTimeout': 0, 'saveDataErrorExecution': 'all'})
        findings = audit_workflow(wf)
        timeout_findings = [f for f in findings if f.check == 'no_timeout']
        self.assertEqual(len(timeout_findings), 1)

    def test_timeout_set_not_flagged(self):
        wf = _make_workflow(settings={'executionTimeout': 60, 'saveDataErrorExecution': 'all'})
        findings = audit_workflow(wf)
        timeout_findings = [f for f in findings if f.check == 'no_timeout']
        self.assertEqual(len(timeout_findings), 0)

    def test_http_without_retry_flagged(self):
        wf = _make_workflow(nodes=[{
            'name': 'Fetch API',
            'type': 'n8n-nodes-base.httpRequest',
            'parameters': {},
        }])
        findings = audit_workflow(wf)
        retry_findings = [f for f in findings if f.check == 'missing_retry']
        self.assertEqual(len(retry_findings), 1)

    def test_http_with_retry_not_flagged(self):
        wf = _make_workflow(nodes=[{
            'name': 'Fetch API',
            'type': 'n8n-nodes-base.httpRequest',
            'parameters': {'options': {'retry': {'retryOnFail': True, 'maxTries': 3}}},
        }])
        findings = audit_workflow(wf)
        retry_findings = [f for f in findings if f.check == 'missing_retry']
        self.assertEqual(len(retry_findings), 0)

    def test_too_many_nodes_flagged(self):
        nodes = [{'name': f'Node {i}', 'type': 'test', 'parameters': {}} for i in range(35)]
        wf = _make_workflow(nodes=nodes)
        findings = audit_workflow(wf)
        count_findings = [f for f in findings if f.check == 'too_many_nodes']
        self.assertEqual(len(count_findings), 1)
        self.assertEqual(count_findings[0].severity, 'INFO')


class TestResilienceAudit(unittest.TestCase):

    def test_no_error_paths_flagged(self):
        wf = _make_workflow(
            nodes=[{'name': 'A', 'type': 't', 'parameters': {}},
                   {'name': 'B', 'type': 't', 'parameters': {}},
                   {'name': 'C', 'type': 't', 'parameters': {}}],
            connections={'A': {'main': [[{'node': 'B'}]]}, 'B': {'main': [[{'node': 'C'}]]}},
        )
        findings = audit_workflow(wf)
        err_findings = [f for f in findings if f.check == 'no_error_paths']
        self.assertEqual(len(err_findings), 1)

    def test_branching_not_flagged(self):
        wf = _make_workflow(
            nodes=[{'name': 'A', 'type': 't', 'parameters': {}},
                   {'name': 'B', 'type': 't', 'parameters': {}},
                   {'name': 'C', 'type': 't', 'parameters': {}}],
            connections={'A': {'main': [[{'node': 'B'}], [{'node': 'C'}]]}},
        )
        findings = audit_workflow(wf)
        err_findings = [f for f in findings if f.check == 'no_error_paths']
        self.assertEqual(len(err_findings), 0)

    def test_no_error_save_flagged(self):
        wf = _make_workflow(settings={'executionTimeout': 300})
        findings = audit_workflow(wf)
        save_findings = [f for f in findings if f.check == 'no_error_save']
        self.assertEqual(len(save_findings), 1)


class TestSensitiveKeyScanning(unittest.TestCase):
    """Tests for the recursive sensitive key scanner."""

    def test_detects_password_in_nested_params(self):
        wf = _make_workflow(nodes=[{
            'name': 'DB',
            'type': 'test',
            'parameters': {'config': {'password': 'hunter2'}},
        }])
        findings = audit_workflow(wf)
        cred = [f for f in findings if f.check == 'credential_in_expression']
        self.assertTrue(len(cred) >= 1)

    def test_expression_password_not_flagged(self):
        wf = _make_workflow(nodes=[{
            'name': 'DB',
            'type': 'test',
            'parameters': {'password': '={{ $credentials.db.password }}'},
        }])
        findings = audit_workflow(wf)
        cred = [f for f in findings if f.check == 'credential_in_expression']
        self.assertEqual(len(cred), 0)

    def test_field_named_password_reset_not_flagged(self):
        """Only exact key matches like 'password', not 'password_reset_enabled'."""
        wf = _make_workflow(nodes=[{
            'name': 'User',
            'type': 'test',
            'parameters': {'password_reset_enabled': True},
        }])
        findings = audit_workflow(wf)
        cred = [f for f in findings if f.check == 'credential_in_expression']
        self.assertEqual(len(cred), 0)

    def test_api_key_in_deeply_nested_dict(self):
        wf = _make_workflow(nodes=[{
            'name': 'Service',
            'type': 'test',
            'parameters': {'level1': {'level2': {'api_key': 'my-secret-key-123'}}},
        }])
        findings = audit_workflow(wf)
        cred = [f for f in findings if f.check == 'credential_in_expression']
        self.assertTrue(len(cred) >= 1)

    def test_secret_key_in_list(self):
        wf = _make_workflow(nodes=[{
            'name': 'Multi',
            'type': 'test',
            'parameters': {'configs': [{'secret': 'abc123'}]},
        }])
        findings = audit_workflow(wf)
        cred = [f for f in findings if f.check == 'credential_in_expression']
        self.assertTrue(len(cred) >= 1)

    def test_detects_aws_access_key(self):
        wf = _make_workflow(nodes=[{
            'name': 'S3',
            'type': 'test',
            'parameters': {'accessKeyId': 'AKIAIOSFODNN7EXAMPLE'},
        }])
        findings = audit_workflow(wf)
        crit = [f for f in findings if f.check == 'hardcoded_credentials']
        self.assertTrue(len(crit) >= 1)


class TestRenderFindings(unittest.TestCase):

    def test_no_findings(self):
        self.assertEqual(render_findings([]), 'No findings.')

    def test_renders_table(self):
        findings = [
            Finding('security', 'CRITICAL', 'hardcoded_credentials', 'Found key'),
            Finding('best_practices', 'WARNING', 'no_timeout', 'No timeout'),
        ]
        result = render_findings(findings)
        self.assertIn('| 1 |', result)
        self.assertIn('CRITICAL', result)
        self.assertIn('1 critical, 1 warning, 0 info', result)


if __name__ == '__main__':
    unittest.main()

"""Unit tests for audit_diff — fingerprinting and delta logic."""
import unittest

from auditor import Finding
from audit_diff import audit_delta, _fingerprint, _extract_node_name
from helpers import make_workflow, make_node


class TestFingerprint(unittest.TestCase):

    def test_node_message_extracts_name(self):
        f = Finding('security', 'WARNING', 'missing_webhook_auth',
                    'Node "Webhook A": webhook has no authentication configured')
        self.assertEqual(_fingerprint(f), ('WARNING', 'missing_webhook_auth', 'Webhook A'))

    def test_workflow_level_message_no_node(self):
        f = Finding('best_practices', 'WARNING', 'no_timeout',
                    'Workflow has no execution timeout set — could run forever')
        self.assertEqual(_fingerprint(f), ('WARNING', 'no_timeout'))

    def test_extract_node_name_handles_quoted_names(self):
        self.assertEqual(_extract_node_name('Node "abc": foo'), 'abc')
        self.assertIsNone(_extract_node_name('Workflow has no timeout'))
        self.assertIsNone(_extract_node_name(''))

    def test_message_text_change_does_not_break_fingerprint(self):
        f1 = Finding('best_practices', 'WARNING', 'missing_retry',
                     'Node "API": HTTP request has no retry configured')
        f2 = Finding('best_practices', 'WARNING', 'missing_retry',
                     'Node "API": HTTP request needs retry — see docs')  # rewritten message
        self.assertEqual(_fingerprint(f1), _fingerprint(f2))


class TestAuditDelta(unittest.TestCase):

    def test_pre_existing_finding_suppressed(self):
        # Both snap and modified have a webhook without auth
        webhook_node = make_node('w1', 'Hook',
                                 type_='n8n-nodes-base.webhook',
                                 parameters={'path': 'foo'})
        snap = make_workflow(nodes=[webhook_node])
        modified = make_workflow(nodes=[webhook_node])

        delta = audit_delta(snap, modified)

        # Webhook auth finding is in both — should be suppressed
        self.assertEqual(delta.new_critical, 0)
        self.assertEqual(delta.new_warning, 0)
        self.assertTrue(any(
            f.check == 'missing_webhook_auth' for f in delta.suppressed
        ))

    def test_new_finding_surfaced(self):
        # Snap has no webhook; modified adds one without auth
        snap = make_workflow(nodes=[make_node('s1', 'SetX')])
        modified = make_workflow(nodes=[
            make_node('w1', 'Hook',
                      type_='n8n-nodes-base.webhook',
                      parameters={'path': 'foo'}),
        ])

        delta = audit_delta(snap, modified)

        self.assertGreaterEqual(delta.new_warning, 1)
        self.assertTrue(any(
            f.check == 'missing_webhook_auth' for f in delta.new_findings
        ))

    def test_no_changes_means_empty_delta(self):
        snap = make_workflow(nodes=[make_node('n1', 'X')])
        modified = make_workflow(nodes=[make_node('n1', 'X')])
        delta = audit_delta(snap, modified)
        self.assertEqual(delta.new_findings, [])

    def test_delta_counts_severities_correctly(self):
        # Add a node with a hardcoded credential to trigger a CRITICAL
        snap = make_workflow(nodes=[make_node('n1', 'X')])
        modified = make_workflow(nodes=[
            make_node('n1', 'X'),
            make_node('n2', 'API', type_='n8n-nodes-base.httpRequest',
                      parameters={'url': 'https://x', 'token': 'sk-abcdefghijklmnopqrstuvwxyz123456'}),
        ])
        delta = audit_delta(snap, modified)
        self.assertGreaterEqual(delta.new_critical, 1)


if __name__ == '__main__':
    unittest.main()

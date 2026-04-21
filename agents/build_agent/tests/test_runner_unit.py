"""Unit tests for TEST phase — tests match logic and run_tests with mocked client."""
import os
import sys
import unittest
from unittest.mock import MagicMock, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from models import Trigger, Step, TestCase, WorkflowSpec
from test_runner import _match_expected, _normalize_expected, run_tests, render_results, RunResult
from client import N8nApiError


def _make_spec(**overrides):
    defaults = dict(
        workflow_name='Test',
        trigger=Trigger(type='webhook', path='test-hook', method='POST'),
        steps=[Step(id='s1', name='A', node_type='n8n-nodes-base.set')],
        gates=[],
        test_cases=[
            TestCase(name='tc1', input={'a': 1}, expected={'status': 'ok'}),
            TestCase(name='tc2', input={'a': 2}, expected={'status': 'ok'}),
        ],
    )
    defaults.update(overrides)
    return WorkflowSpec(**defaults)


class TestMatchExpected(unittest.TestCase):

    def test_exact_match(self):
        passed, err = _match_expected({'status': 'ok', 'extra': 1}, {'status': 'ok'})
        self.assertTrue(passed)
        self.assertEqual(err, '')

    def test_missing_key(self):
        passed, err = _match_expected({'other': 1}, {'status': 'ok'})
        self.assertFalse(passed)
        self.assertIn('Missing key', err)

    def test_value_mismatch(self):
        passed, err = _match_expected({'status': 'error'}, {'status': 'ok'})
        self.assertFalse(passed)
        self.assertIn('expected', err)

    def test_any_non_empty_string_match(self):
        passed, _ = _match_expected({'ts': '2026-01-01'}, {'ts': 'any non-empty string'})
        self.assertTrue(passed)

    def test_any_non_empty_string_empty_fails(self):
        passed, err = _match_expected({'ts': ''}, {'ts': 'any non-empty string'})
        self.assertFalse(passed)

    def test_any_non_empty_string_non_string_fails(self):
        passed, err = _match_expected({'ts': 123}, {'ts': 'any non-empty string'})
        self.assertFalse(passed)

    def test_non_dict_actual(self):
        passed, err = _match_expected('not a dict', {'status': 'ok'})
        self.assertFalse(passed)
        self.assertIn('Expected dict', err)

    def test_empty_expected_always_passes(self):
        passed, _ = _match_expected({'anything': 'here'}, {})
        self.assertTrue(passed)


class TestNormalizeExpected(unittest.TestCase):
    """PM Agent emits expected in shapes the Build Agent's response dict doesn't match.
    _normalize_expected papers over the shape drift before _match_expected runs."""

    def test_renames_camel_http_status(self):
        out = _normalize_expected({'httpStatus': 200, 'status': 'ok'})
        self.assertEqual(out, {'http_status': 200, 'status': 'ok'})

    def test_prefers_existing_snake_case(self):
        out = _normalize_expected({'httpStatus': 200, 'http_status': 400})
        # snake_case already present — don't let the rename clobber it
        self.assertEqual(out['http_status'], 400)
        self.assertNotIn('httpStatus', out)

    def test_flattens_nested_body(self):
        out = _normalize_expected({
            'http_status': 200,
            'body': {'status': 'ok', 'greeting': 'hi'},
        })
        self.assertEqual(out, {'http_status': 200, 'status': 'ok', 'greeting': 'hi'})

    def test_flatten_preserves_top_level_collisions(self):
        out = _normalize_expected({
            'http_status': 200,
            'status': 'from_top',
            'body': {'status': 'from_body'},
        })
        # Flat already had 'status'; body.status must not overwrite
        self.assertEqual(out['status'], 'from_top')

    def test_non_dict_body_ignored(self):
        # An array or string body is a legitimate flat value — don't flatten
        out = _normalize_expected({'http_status': 200, 'body': [1, 2]})
        self.assertEqual(out, {'http_status': 200, 'body': [1, 2]})

    def test_camel_and_nested_body_combined(self):
        out = _normalize_expected({
            'httpStatus': 400,
            'body': {'status': 'error', 'message': 'bad'},
        })
        self.assertEqual(out, {'http_status': 400, 'status': 'error', 'message': 'bad'})

    def test_match_expected_applies_normalizer(self):
        # End-to-end: _match_expected on a PM-shaped expected matches a
        # Build-Agent-shaped actual without any caller rewrite.
        actual = {'status': 'ok', 'greeting': 'hi', 'http_status': 200}
        expected = {'httpStatus': 200, 'body': {'status': 'ok', 'greeting': 'hi'}}
        passed, err = _match_expected(actual, expected)
        self.assertTrue(passed, err)


class TestRunTests(unittest.TestCase):

    def test_activates_and_deactivates(self):
        spec = _make_spec()
        client = MagicMock()
        client.send_webhook.return_value = {'status': 'ok'}

        run_tests(spec, client, 'wf-1')

        client.activate_workflow.assert_called_once_with('wf-1')
        client.deactivate_workflow.assert_called_once_with('wf-1')

    def test_returns_result_per_test_case(self):
        spec = _make_spec()
        client = MagicMock()
        client.send_webhook.return_value = {'status': 'ok'}

        results = run_tests(spec, client, 'wf-1')

        self.assertEqual(len(results), 2)

    def test_passing_test(self):
        spec = _make_spec()
        client = MagicMock()
        client.send_webhook.return_value = {'status': 'ok'}

        results = run_tests(spec, client, 'wf-1')

        self.assertTrue(results[0].passed)

    def test_failing_test(self):
        spec = _make_spec()
        client = MagicMock()
        client.send_webhook.return_value = {'status': 'error'}

        results = run_tests(spec, client, 'wf-1')

        self.assertFalse(results[0].passed)

    def test_api_error_handled(self):
        spec = _make_spec()
        client = MagicMock()
        client.send_webhook.side_effect = N8nApiError(500, 'boom', '/webhook/test')

        results = run_tests(spec, client, 'wf-1')

        self.assertFalse(results[0].passed)
        self.assertIn('500', results[0].error)

    def test_deactivates_even_on_error(self):
        spec = _make_spec()
        client = MagicMock()
        client.send_webhook.side_effect = Exception('network error')

        results = run_tests(spec, client, 'wf-1')

        client.deactivate_workflow.assert_called_once()

    def test_no_webhook_path_raises(self):
        spec = _make_spec(trigger=Trigger(type='cron', path=''))
        client = MagicMock()

        with self.assertRaises(ValueError):
            run_tests(spec, client, 'wf-1')

    def test_sends_correct_data(self):
        spec = _make_spec(test_cases=[
            TestCase(name='tc1', input={'key': 'val'}, expected={'status': 'ok'}),
        ])
        client = MagicMock()
        client.send_webhook.return_value = {'status': 'ok'}

        run_tests(spec, client, 'wf-1')

        client.send_webhook.assert_called_once_with('test-hook', {'key': 'val'})

    def test_get_trigger_sends_query_not_body(self):
        """GET trigger: inputs must go on the URL as query params; send_webhook
        is called with method='GET' and query=, not as a POST body."""
        spec = _make_spec(
            trigger=Trigger(type='webhook', path='test-hook', method='GET'),
            test_cases=[
                TestCase(name='tc1', input={'name': 'Alice', 'hour': 9}, expected={'status': 'ok'}),
            ],
        )
        client = MagicMock()
        client.send_webhook.return_value = {'status': 'ok'}

        run_tests(spec, client, 'wf-1')

        client.send_webhook.assert_called_once_with(
            'test-hook', method='GET', query={'name': 'Alice', 'hour': 9}
        )

    def test_get_trigger_strips_query_wrapper(self):
        """PM Agent often wraps GET inputs as {query: {...}}. Unwrap before sending
        so $json.query.name resolves on n8n's side (and the raw dict doesn't
        become a single ?query= param)."""
        spec = _make_spec(
            trigger=Trigger(type='webhook', path='test-hook', method='GET'),
            test_cases=[
                TestCase(name='tc1', input={'query': {'name': 'Bob', 'hour': 14}}, expected={'status': 'ok'}),
            ],
        )
        client = MagicMock()
        client.send_webhook.return_value = {'status': 'ok'}

        run_tests(spec, client, 'wf-1')

        client.send_webhook.assert_called_once_with(
            'test-hook', method='GET', query={'name': 'Bob', 'hour': 14}
        )


class TestRunTestsValidation(unittest.TestCase):

    def test_empty_test_cases_raises(self):
        spec = _make_spec(test_cases=[])
        # Bypass models validation by setting directly
        spec.test_cases = []
        client = MagicMock()
        with self.assertRaises(ValueError) as ctx:
            run_tests(spec, client, 'wf-1')
        self.assertIn('no test cases', str(ctx.exception))


    def test_no_webhook_path_raises_clear_error(self):
        """Non-webhook specs should get a clear skip message, not a crash."""
        spec = _make_spec(
            trigger=Trigger(type='cron', schedule='0 7 * * *'),
        )
        client = MagicMock()
        with self.assertRaises(ValueError) as ctx:
            run_tests(spec, client, 'wf-1')
        self.assertIn('webhook', str(ctx.exception).lower())

    def test_cron_trigger_message_mentions_manual_testing(self):
        """Error for cron triggers should suggest manual testing."""
        spec = _make_spec(
            trigger=Trigger(type='cron', schedule='0 7 * * *'),
        )
        client = MagicMock()
        with self.assertRaises(ValueError) as ctx:
            run_tests(spec, client, 'wf-1')
        msg = str(ctx.exception).lower()
        self.assertTrue('manual' in msg or 'cron' in msg or 'skip' in msg,
                        f'Error should mention manual/cron/skip, got: {ctx.exception}')


class TestRenderResults(unittest.TestCase):

    def test_renders_table(self):
        results = [
            RunResult(test_name='tc1', passed=True, expected={}, actual={}),
            RunResult(test_name='tc2', passed=False, expected={}, actual={}, error='bad value'),
        ]
        output = render_results(results)
        self.assertIn('PASS', output)
        self.assertIn('FAIL', output)
        self.assertIn('1/2 passed', output)


if __name__ == '__main__':
    unittest.main()

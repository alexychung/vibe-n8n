"""Unit tests for the n8n API client — no live n8n required."""
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from client import N8nClient, N8nApiError


class _FakeResponse:
    """Minimal stand-in for the object returned by urlopen's context manager."""

    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestClientInit(unittest.TestCase):

    def test_default_timeout(self):
        client = N8nClient(base_url='http://test', api_key='key')
        self.assertEqual(client.timeout, 30)

    def test_custom_timeout(self):
        client = N8nClient(base_url='http://test', api_key='key', timeout=60)
        self.assertEqual(client.timeout, 60)

    def test_base_url_trailing_slash_stripped(self):
        client = N8nClient(base_url='http://test:5678/', api_key='key')
        self.assertEqual(client.base_url, 'http://test:5678')


class TestN8nApiError(unittest.TestCase):

    def test_error_message(self):
        err = N8nApiError(404, 'Not Found', 'http://test/api/v1/workflows/123')
        self.assertIn('404', str(err))
        self.assertIn('Not Found', str(err))
        self.assertIn('http://test/api/v1/workflows/123', str(err))

    def test_error_attributes(self):
        err = N8nApiError(500, 'Internal error', 'http://test/')
        self.assertEqual(err.status_code, 500)
        self.assertEqual(err.message, 'Internal error')


class TestSendWebhookInjectsHttpStatus(unittest.TestCase):
    """send_webhook merges http_status into the JSON response body."""

    def _client(self):
        return N8nClient(base_url='http://test', api_key='key')

    def test_200_json_object_merges_status(self):
        fake = _FakeResponse(json.dumps({'status': 'ok', 'name': 'demo'}).encode(), status=200)
        with patch('urllib.request.urlopen', return_value=fake):
            result = self._client().send_webhook('p', {'in': 1})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['name'], 'demo')
        self.assertEqual(result['http_status'], 200)

    def test_400_response_returns_dict_with_status(self):
        # Error responses should NOT raise — they must flow through so test
        # cases can assert http_status: 400.
        body = json.dumps({'status': 'error', 'message': 'bad'}).encode()
        err = urllib.error.HTTPError(
            'http://test/webhook/p', 400, 'Bad Request', {}, io.BytesIO(body)
        )
        with patch('urllib.request.urlopen', side_effect=err):
            result = self._client().send_webhook('p', {'in': 1})
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['http_status'], 400)

    def test_json_array_wrapped_under_body_key(self):
        fake = _FakeResponse(json.dumps([1, 2, 3]).encode(), status=200)
        with patch('urllib.request.urlopen', return_value=fake):
            result = self._client().send_webhook('p')
        self.assertEqual(result, {'body': [1, 2, 3], 'http_status': 200})

    def test_non_json_body_kept_as_text(self):
        fake = _FakeResponse(b'plain text', status=200)
        with patch('urllib.request.urlopen', return_value=fake):
            result = self._client().send_webhook('p')
        self.assertEqual(result, {'body': 'plain text', 'http_status': 200})

    def test_connection_error_still_raises(self):
        # Network failures (not HTTP errors) should still raise so the test
        # runner reports them as API errors, not silent passes.
        with patch('urllib.request.urlopen', side_effect=urllib.error.URLError('refused')):
            with self.assertRaises(N8nApiError) as ctx:
                self._client().send_webhook('p')
        self.assertEqual(ctx.exception.status_code, 0)

    def test_send_webhook_forwards_custom_headers(self):
        """send_webhook must attach caller-provided headers (e.g. X-Webhook-Auth)."""
        captured = {}

        def fake_urlopen(req, timeout):
            captured['headers'] = dict(req.header_items())
            return _FakeResponse(json.dumps({'ok': True}).encode(), status=200)

        with patch('urllib.request.urlopen', side_effect=fake_urlopen):
            self._client().send_webhook('p', {'in': 1}, headers={'X-Webhook-Auth': 't0k3n'})

        # urllib title-cases header names
        self.assertEqual(captured['headers'].get('X-webhook-auth'), 't0k3n')
        self.assertEqual(captured['headers'].get('Content-type'), 'application/json')

    def test_get_sends_query_string_no_body(self):
        """GET webhooks must put inputs in the URL query string and send no body —
        n8n's webhook node routes GET requests only when the URL matches,
        and expects query params via $json.query.*."""
        captured = {}

        def fake_urlopen(req, timeout):
            captured['url'] = req.full_url
            captured['method'] = req.get_method()
            captured['data'] = req.data
            captured['headers'] = dict(req.header_items())
            return _FakeResponse(json.dumps({'ok': True}).encode(), status=200)

        with patch('urllib.request.urlopen', side_effect=fake_urlopen):
            self._client().send_webhook('p', method='GET', query={'name': 'Alice', 'hour': 9})

        self.assertEqual(captured['method'], 'GET')
        self.assertIsNone(captured['data'])
        # Both param orderings are acceptable; check membership
        self.assertIn('name=Alice', captured['url'])
        self.assertIn('hour=9', captured['url'])
        # No Content-Type for a bodyless GET
        self.assertNotIn('Content-type', captured['headers'])

    def test_get_coerces_non_string_query_values(self):
        """PM Agent test cases write hour: 9 (int); n8n treats query values as strings.
        The client stringifies so downstream parseInt / regex checks see what a
        real browser would send."""
        captured = {}

        def fake_urlopen(req, timeout):
            captured['url'] = req.full_url
            return _FakeResponse(b'{}', status=200)

        with patch('urllib.request.urlopen', side_effect=fake_urlopen):
            self._client().send_webhook('p', method='GET', query={'hour': 9, 'active': True})

        self.assertIn('hour=9', captured['url'])
        self.assertIn('active=True', captured['url'])


class TestCreateCredential(unittest.TestCase):

    def _client(self):
        return N8nClient(base_url='http://test', api_key='key')

    def test_posts_to_credentials_endpoint(self):
        captured = {}

        def fake_urlopen(req, timeout):
            captured['url'] = req.full_url
            captured['method'] = req.get_method()
            captured['body'] = json.loads(req.data.decode('utf-8'))
            captured['api_key'] = req.get_header('X-n8n-api-key')
            return _FakeResponse(
                json.dumps({'id': 'cred-1', 'name': 'n', 'type': 'httpHeaderAuth'}).encode(),
                status=200,
            )

        with patch('urllib.request.urlopen', side_effect=fake_urlopen):
            result = self._client().create_credential(
                name='n',
                type='httpHeaderAuth',
                data={'name': 'X-Webhook-Auth', 'value': 'abc'},
            )

        self.assertEqual(captured['url'], 'http://test/api/v1/credentials')
        self.assertEqual(captured['method'], 'POST')
        self.assertEqual(captured['body'], {
            'name': 'n',
            'type': 'httpHeaderAuth',
            'data': {'name': 'X-Webhook-Auth', 'value': 'abc'},
        })
        self.assertEqual(captured['api_key'], 'key')
        self.assertEqual(result['id'], 'cred-1')


if __name__ == '__main__':
    unittest.main()

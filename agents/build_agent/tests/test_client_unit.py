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


if __name__ == '__main__':
    unittest.main()

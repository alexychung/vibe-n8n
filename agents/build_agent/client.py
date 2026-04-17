"""n8n REST API client.

Thin wrapper over urllib. No external dependencies.
Handles the GET-modify-PUT pattern since n8n has no PATCH endpoint.
"""
import json
import os
import urllib.request
import urllib.error
from typing import Any, Callable, Optional


class N8nApiError(Exception):
    """Raised when the n8n API returns an error."""

    def __init__(self, status_code: int, message: str, url: str):
        self.status_code = status_code
        self.message = message
        self.url = url
        super().__init__(f'n8n API error {status_code} on {url}: {message}')


class N8nClient:
    """Client for the n8n REST API."""

    def __init__(self, base_url: str = '', api_key: str = '', timeout: int = 30):
        self.base_url = (base_url or os.environ.get('N8N_BASE_URL', 'http://localhost:5678')).rstrip('/')
        self.api_key = api_key or os.environ.get('N8N_API_KEY', '')
        self.timeout = timeout

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        """Make an HTTP request to the n8n API. Returns parsed JSON."""
        url = f'{self.base_url}{path}'
        headers = {'X-N8N-API-KEY': self.api_key}

        data = None
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'

        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8', errors='replace')
            try:
                error_msg = json.loads(error_body).get('message', error_body)
            except (json.JSONDecodeError, AttributeError):
                error_msg = error_body
            raise N8nApiError(e.code, error_msg, url) from e
        except urllib.error.URLError as e:
            raise N8nApiError(0, str(e.reason), url) from e

    def _webhook_request(self, path: str, body: Any = None) -> Any:
        """Send data to a webhook endpoint. Returns response body with http_status merged in.

        If the response body is a JSON object, returns {**body, 'http_status': code}.
        If the response body is a JSON array or scalar, returns {'body': parsed, 'http_status': code}.
        On non-JSON responses, returns {'body': raw_text, 'http_status': code}.
        HTTP 4xx/5xx responses also return a dict (not raised) so test cases can
        match against status codes and error bodies.
        """
        url = f'{self.base_url}/webhook/{path}'
        data = json.dumps(body).encode('utf-8') if body is not None else None
        headers = {'Content-Type': 'application/json'} if data else {}

        req = urllib.request.Request(url, data=data, headers=headers, method='POST')

        def _wrap(raw: bytes, status: int) -> Any:
            text = raw.decode('utf-8', errors='replace')
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return {'body': text, 'http_status': status}
            if isinstance(parsed, dict):
                return {**parsed, 'http_status': status}
            return {'body': parsed, 'http_status': status}

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return _wrap(resp.read(), resp.status)
        except urllib.error.HTTPError as e:
            return _wrap(e.read(), e.code)
        except urllib.error.URLError as e:
            raise N8nApiError(0, str(e.reason), url) from e

    # --- Workflows ---

    def create_workflow(
        self,
        name: str,
        nodes: list[dict],
        connections: dict,
        settings: Optional[dict] = None,
    ) -> dict:
        """Create a new workflow. Returns the full workflow object."""
        body: dict[str, Any] = {
            'name': name,
            'nodes': nodes,
            'connections': connections,
        }
        if settings:
            body['settings'] = settings
        return self._request('POST', '/api/v1/workflows', body)

    def get_workflow(self, workflow_id: str) -> dict:
        """Get a single workflow by ID."""
        return self._request('GET', f'/api/v1/workflows/{workflow_id}')

    def list_workflows(self) -> list[dict]:
        """List all workflows. Returns the data array."""
        result = self._request('GET', '/api/v1/workflows')
        return result.get('data', [])

    def update_workflow(self, workflow_id: str, modifier: Callable[[dict], dict]) -> dict:
        """GET-modify-PUT update pattern.

        Fetches the current workflow, passes it to `modifier` which returns
        the modified version, then PUTs it back. This is the only way to
        update workflows since n8n has no PATCH endpoint.
        """
        current = self.get_workflow(workflow_id)
        modified = modifier(current)
        put_body = {
            'name': modified['name'],
            'nodes': modified['nodes'],
            'connections': modified['connections'],
            'settings': modified.get('settings', {}),
        }
        return self._request('PUT', f'/api/v1/workflows/{workflow_id}', put_body)

    def delete_workflow(self, workflow_id: str) -> dict:
        """Delete a workflow."""
        return self._request('DELETE', f'/api/v1/workflows/{workflow_id}')

    def activate_workflow(self, workflow_id: str) -> dict:
        """Activate a workflow."""
        return self._request('POST', f'/api/v1/workflows/{workflow_id}/activate')

    def deactivate_workflow(self, workflow_id: str) -> dict:
        """Deactivate a workflow."""
        return self._request('POST', f'/api/v1/workflows/{workflow_id}/deactivate')

    # --- Webhooks ---

    def send_webhook(self, path: str, data: Any = None) -> Any:
        """Send data to a workflow's webhook endpoint."""
        return self._webhook_request(path, data)

    # --- Executions ---

    def list_executions(self, workflow_id: Optional[str] = None) -> list[dict]:
        """List executions, optionally filtered by workflow ID."""
        path = '/api/v1/executions'
        if workflow_id:
            path += f'?workflowId={workflow_id}'
        result = self._request('GET', path)
        return result.get('data', [])

    # --- Credentials ---

    def list_credentials(self) -> list[dict]:
        """List all credentials. Returns the data array."""
        result = self._request('GET', '/api/v1/credentials')
        return result.get('data', [])

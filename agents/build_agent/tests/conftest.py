"""Shared fixtures and helpers for build agent tests."""
import json
import os
import sys
import urllib.request
import urllib.error

# Add parent to path so we can import the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'workflows', 'test-data')


def load_env():
    """Load .env from project root if env vars not already set."""
    if os.environ.get('N8N_API_KEY'):
        return
    env_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, val = line.split('=', 1)
                    os.environ[key.strip()] = val.strip()


def n8n_is_reachable() -> bool:
    """Check if the n8n instance is reachable."""
    load_env()
    base_url = os.environ.get('N8N_BASE_URL', 'http://localhost:5678')
    api_key = os.environ.get('N8N_API_KEY', '')
    if not api_key:
        return False
    try:
        req = urllib.request.Request(
            f'{base_url}/api/v1/workflows',
            headers={'X-N8N-API-KEY': api_key},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


N8N_AVAILABLE = n8n_is_reachable()
SKIP_MSG = 'n8n instance not reachable — skipping integration test'


def load_echo_spec():
    """Load and parse the echo-spec.json fixture."""
    from models import parse_spec
    with open(os.path.join(FIXTURE_DIR, 'echo-spec.json')) as f:
        return parse_spec(json.load(f))

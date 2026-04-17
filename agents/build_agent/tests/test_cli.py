"""Unit tests for the build agent CLI — tests cmd_build control flow."""
import importlib
import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Can't use "from __main__ import" because pytest's __main__ shadows it.
# Load the module by path instead.
import importlib.util
_cli_path = os.path.join(os.path.dirname(__file__), '..', '__main__.py')
_spec = importlib.util.spec_from_file_location('build_agent_cli', _cli_path)
_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cli)

cmd_build = _cli.cmd_build
cmd_single_phase = _cli.cmd_single_phase
cmd_list = _cli.cmd_list
cmd_export = _cli.cmd_export
load_spec = _cli.load_spec
_extract_flag_value = _cli._extract_flag_value

from models import ValidationError

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'workflows', 'test-data')
ECHO_SPEC = os.path.join(FIXTURE_DIR, 'echo-spec.json')


class TestLoadSpec(unittest.TestCase):

    def test_loads_valid_spec(self):
        spec = load_spec(ECHO_SPEC)
        self.assertEqual(spec.workflow_name, 'Webhook Echo')

    def test_missing_file_exits(self):
        with self.assertRaises(SystemExit) as ctx:
            load_spec('nonexistent.json')
        self.assertIn('not found', str(ctx.exception))

    def test_invalid_json_exits(self):
        # Create a temp file with invalid JSON
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{invalid json}')
            f.flush()
            path = f.name
        try:
            with self.assertRaises(SystemExit) as ctx:
                load_spec(path)
            self.assertIn('Invalid JSON', str(ctx.exception))
        finally:
            os.unlink(path)


class TestCmdBuildDryRun(unittest.TestCase):

    def test_dry_run_returns_0(self):
        result = cmd_build(ECHO_SPEC, dry_run=True)
        self.assertEqual(result, 0)

    def test_dry_run_does_not_touch_n8n(self):
        # If it tried to touch n8n, it would fail (no API key set)
        with patch.dict(os.environ, {'N8N_API_KEY': ''}, clear=False):
            result = cmd_build(ECHO_SPEC, dry_run=True)
            self.assertEqual(result, 0)


class TestCmdBuildNoApiKey(unittest.TestCase):

    def test_returns_1_without_api_key(self):
        with patch.dict(os.environ, {'N8N_API_KEY': ''}, clear=False):
            # Temporarily remove env var
            old = os.environ.pop('N8N_API_KEY', None)
            try:
                result = cmd_build(ECHO_SPEC, dry_run=False)
                self.assertEqual(result, 1)
            finally:
                if old:
                    os.environ['N8N_API_KEY'] = old


class TestCmdSinglePhase(unittest.TestCase):

    def test_validate_returns_0(self):
        result = cmd_single_phase('validate', ECHO_SPEC)
        self.assertEqual(result, 0)

    def test_unknown_phase_returns_1(self):
        result = cmd_single_phase('nonexistent', ECHO_SPEC)
        self.assertEqual(result, 1)


class TestExtractFlagValue(unittest.TestCase):

    def test_space_form(self):
        self.assertEqual(_extract_flag_value(['--export-dir', 'out'], 'export-dir', 'default'), 'out')

    def test_equals_form(self):
        self.assertEqual(_extract_flag_value(['--export-dir=out'], 'export-dir', 'default'), 'out')

    def test_default_when_missing(self):
        self.assertEqual(_extract_flag_value(['--other', 'x'], 'export-dir', 'default'), 'default')

    def test_default_when_flag_has_no_value(self):
        # --flag at end of args without a value → default
        self.assertEqual(_extract_flag_value(['--export-dir'], 'export-dir', 'default'), 'default')


class TestCmdList(unittest.TestCase):

    def test_requires_api_key(self):
        with patch.dict(os.environ, {'N8N_API_KEY': ''}, clear=False):
            old = os.environ.pop('N8N_API_KEY', None)
            try:
                self.assertEqual(cmd_list(), 1)
            finally:
                if old:
                    os.environ['N8N_API_KEY'] = old

    def test_empty_list(self):
        with patch.dict(os.environ, {'N8N_API_KEY': 'test-key'}, clear=False):
            with patch.object(_cli, 'N8nClient') as MockClient:
                MockClient.return_value.list_workflows.return_value = []
                self.assertEqual(cmd_list(), 0)

    def test_renders_workflows(self):
        with patch.dict(os.environ, {'N8N_API_KEY': 'test-key'}, clear=False):
            with patch.object(_cli, 'N8nClient') as MockClient:
                MockClient.return_value.list_workflows.return_value = [
                    {'id': 'abc', 'name': 'Webhook Echo', 'active': True},
                    {'id': 'def', 'name': 'Daily Digest', 'active': False},
                ]
                self.assertEqual(cmd_list(), 0)


class TestCmdExport(unittest.TestCase):

    def test_requires_api_key(self):
        with patch.dict(os.environ, {'N8N_API_KEY': ''}, clear=False):
            old = os.environ.pop('N8N_API_KEY', None)
            try:
                self.assertEqual(cmd_export('wf_1', ECHO_SPEC), 1)
            finally:
                if old:
                    os.environ['N8N_API_KEY'] = old

    def test_exports_to_custom_dir(self):
        import tempfile
        with patch.dict(os.environ, {'N8N_API_KEY': 'test-key'}, clear=False):
            with patch.object(_cli, 'N8nClient') as MockClient:
                MockClient.return_value.get_workflow.return_value = {
                    'id': 'wf_1',
                    'name': 'Webhook Echo',
                    'nodes': [{'id': 'n1'}],
                    'connections': {},
                    'settings': {},
                }
                with tempfile.TemporaryDirectory() as tmp:
                    result = cmd_export('wf_1', ECHO_SPEC, export_dir=tmp)
                    self.assertEqual(result, 0)
                    self.assertTrue(os.path.exists(os.path.join(tmp, 'webhook-echo.json')))
                    self.assertTrue(os.path.exists(os.path.join(tmp, 'webhook-echo.README.md')))


if __name__ == '__main__':
    unittest.main()

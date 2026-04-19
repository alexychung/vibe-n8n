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
_render_topology = _cli._render_topology
_write_auth_log = _cli._write_auth_log

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

    def test_json_output_shape(self):
        """--json produces a parseable array with id/name/active/slug per workflow."""
        import io
        from contextlib import redirect_stdout
        with patch.dict(os.environ, {'N8N_API_KEY': 'test-key'}, clear=False):
            with patch.object(_cli, 'N8nClient') as MockClient:
                MockClient.return_value.list_workflows.return_value = [
                    {'id': 'abc', 'name': 'Webhook Echo', 'active': True},
                    {'id': 'def', 'name': 'Daily Digest!', 'active': False},
                ]
                buf = io.StringIO()
                with redirect_stdout(buf):
                    self.assertEqual(cmd_list(as_json=True), 0)
                data = json.loads(buf.getvalue())
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0], {'id': 'abc', 'name': 'Webhook Echo', 'active': True, 'slug': 'webhook-echo'})
        self.assertEqual(data[1]['slug'], 'daily-digest')
        self.assertFalse(data[1]['active'])

    def test_json_output_on_empty(self):
        """--json still emits valid JSON (empty array) when no workflows."""
        import io
        from contextlib import redirect_stdout
        with patch.dict(os.environ, {'N8N_API_KEY': 'test-key'}, clear=False):
            with patch.object(_cli, 'N8nClient') as MockClient:
                MockClient.return_value.list_workflows.return_value = []
                buf = io.StringIO()
                with redirect_stdout(buf):
                    self.assertEqual(cmd_list(as_json=True), 0)
                self.assertEqual(json.loads(buf.getvalue()), [])


class TestRenderTopology(unittest.TestCase):

    def test_topology_includes_trigger_and_steps(self):
        spec = load_spec(ECHO_SPEC)
        out = _render_topology(spec)
        # Trigger line
        self.assertIn('webhook', out)
        self.assertIn('/echo-test', out)
        self.assertIn('POST', out)
        # Every step labelled with id and node_type
        for s in spec.steps:
            self.assertIn(f'[{s.id}]', out)
            self.assertIn(s.name, out)
            self.assertIn(s.node_type, out)

    def test_topology_shows_gate_branches(self):
        """Gates render pass/fail targets."""
        spec = load_spec(ECHO_SPEC)
        out = _render_topology(spec)
        # echo-spec has step_1 gate → pass_to step_2, fail_to step_3
        self.assertIn('pass', out)
        self.assertIn('fail', out)
        self.assertIn('Build Success Response', out)
        self.assertIn('Build Error Response', out)

    def test_topology_handles_cron_trigger(self):
        from models import WorkflowSpec, Trigger
        spec = WorkflowSpec(
            workflow_name='Nightly',
            description='',
            trigger=Trigger(type='cron', path='', method='', schedule='0 3 * * *', description=''),
            steps=[],
            gates=[],
            error_handling={},
            output={},
            security={},
            cost_estimate={},
            test_cases=[],
        )
        out = _render_topology(spec)
        self.assertIn('cron', out)
        self.assertIn('0 3 * * *', out)


class TestWriteAuthLog(unittest.TestCase):

    def _make_auth(self, node_name='Hook', token='t0k3n'):
        from harden import GeneratedAuth, WEBHOOK_AUTH_HEADER
        return GeneratedAuth(
            node_name=node_name,
            header_name=WEBHOOK_AUTH_HEADER,
            token=token,
            credential_id='cred-1',
            credential_name='Wf — Hook auth',
        )

    def test_writes_dotenv_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_auth_log('My Workflow', [self._make_auth()], log_dir=tmp)
            self.assertTrue(os.path.exists(path))
            self.assertTrue(path.endswith('my-workflow-auth.env'))
            content = open(path, encoding='utf-8').read()
            self.assertIn('WEBHOOK_AUTH_HEADER=X-Webhook-Auth', content)
            self.assertIn('WEBHOOK_AUTH_TOKEN=t0k3n', content)
            self.assertIn('# node: Hook', content)

    def test_multiple_auths_get_suffixed_keys(self):
        import tempfile
        auths = [
            self._make_auth(node_name='HookA', token='tokA'),
            self._make_auth(node_name='HookB', token='tokB'),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_auth_log('Wf', auths, log_dir=tmp)
            content = open(path, encoding='utf-8').read()
            self.assertIn('WEBHOOK_AUTH_TOKEN_1=tokA', content)
            self.assertIn('WEBHOOK_AUTH_TOKEN_2=tokB', content)

    def test_creates_log_dir_if_missing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            nested = os.path.join(tmp, 'nested', 'logs')
            path = _write_auth_log('Wf', [self._make_auth()], log_dir=nested)
            self.assertTrue(os.path.exists(path))


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

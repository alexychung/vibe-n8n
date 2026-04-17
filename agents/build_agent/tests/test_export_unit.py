"""Unit tests for the EXPORT phase."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from export import _slugify, _portable_workflow, _format_credential, _render_readme, export_workflow
from models import WorkflowSpec, Trigger, Step, TestCase


def _make_spec(**overrides):
    defaults = dict(
        workflow_name='Webhook Echo',
        description='Receives a POST, echoes back with status and timestamp',
        trigger=Trigger(type='webhook', path='echo-test', method='POST'),
        steps=[Step(id='s1', name='Set', node_type='n8n-nodes-base.set')],
        test_cases=[TestCase(name='happy', input={}, expected={'status': 'ok'})],
        security={'credentials_needed': []},
    )
    defaults.update(overrides)
    return WorkflowSpec(**defaults)


class TestSlugify(unittest.TestCase):
    def test_simple_name(self):
        self.assertEqual(_slugify('Webhook Echo'), 'webhook-echo')

    def test_punctuation_stripped(self):
        self.assertEqual(_slugify('Daily NYC Weather: Freezing Alert!'), 'daily-nyc-weather-freezing-alert')

    def test_collapses_separators(self):
        self.assertEqual(_slugify('a   b---c'), 'a-b-c')

    def test_empty_name_fallback(self):
        self.assertEqual(_slugify(''), 'workflow')
        self.assertEqual(_slugify('   '), 'workflow')
        self.assertEqual(_slugify('!!!'), 'workflow')


class TestPortableWorkflow(unittest.TestCase):
    def test_strips_ephemeral_fields(self):
        wf = {
            'id': 'abc123',
            'name': 'Test',
            'nodes': [{'id': 'n1'}],
            'connections': {},
            'settings': {'timezone': 'UTC'},
            'active': True,
            'createdAt': '2026-01-01',
            'updatedAt': '2026-01-02',
            'versionId': 'v1',
            'triggerCount': 0,
        }
        portable = _portable_workflow(wf)
        self.assertEqual(set(portable.keys()), {'name', 'nodes', 'connections', 'settings'})

    def test_missing_fields_omitted(self):
        wf = {'name': 'Bare', 'nodes': []}
        portable = _portable_workflow(wf)
        self.assertEqual(portable, {'name': 'Bare', 'nodes': []})


class TestFormatCredential(unittest.TestCase):
    def test_string(self):
        self.assertEqual(_format_credential('slackApi'), '- `slackApi`')

    def test_dict_with_name_and_description(self):
        result = _format_credential({'name': 'samGovApi', 'description': 'SAM.gov contract API key'})
        self.assertEqual(result, '- `samGovApi` — SAM.gov contract API key')

    def test_dict_with_only_name(self):
        self.assertEqual(_format_credential({'name': 'foo'}), '- `foo`')

    def test_dict_without_name_falls_back(self):
        self.assertEqual(_format_credential({'type': 'gmail'}), '- `gmail`')


class TestRenderReadme(unittest.TestCase):
    def test_includes_workflow_name_and_description(self):
        spec = _make_spec()
        md = _render_readme(spec, {'nodes': [1, 2, 3, 4]}, 'webhook-echo.json')
        self.assertIn('# Webhook Echo', md)
        self.assertIn('Receives a POST', md)
        self.assertIn('**Nodes:** 4', md)

    def test_webhook_trigger_summary(self):
        spec = _make_spec()
        md = _render_readme(spec, {'nodes': []}, 'x.json')
        self.assertIn('webhook at `/echo-test` (POST)', md)

    def test_schedule_trigger_summary(self):
        spec = _make_spec(trigger=Trigger(type='schedule', schedule='0 9 * * *'))
        md = _render_readme(spec, {'nodes': []}, 'x.json')
        self.assertIn('schedule `0 9 * * *`', md)

    def test_no_credentials_section(self):
        spec = _make_spec(security={'credentials_needed': []})
        md = _render_readme(spec, {'nodes': []}, 'x.json')
        self.assertIn('## Required Credentials\n\nNone.', md)

    def test_with_credentials_string_list(self):
        spec = _make_spec(security={'credentials_needed': ['slackApi', 'openWeatherApi']})
        md = _render_readme(spec, {'nodes': []}, 'x.json')
        self.assertIn('- `slackApi`', md)
        self.assertIn('- `openWeatherApi`', md)

    def test_with_credentials_dict_list(self):
        spec = _make_spec(security={'credentials_needed': [
            {'name': 'samGovApi', 'description': 'For contract lookups'}
        ]})
        md = _render_readme(spec, {'nodes': []}, 'x.json')
        self.assertIn('- `samGovApi` — For contract lookups', md)

    def test_references_filename_in_import_section(self):
        spec = _make_spec()
        md = _render_readme(spec, {'nodes': []}, 'my-slug.json')
        self.assertIn('Select `my-slug.json`', md)
        self.assertIn('--data-binary @my-slug.json', md)


class TestExportWorkflow(unittest.TestCase):
    def test_writes_json_and_readme(self):
        spec = _make_spec()
        client = MagicMock()
        client.get_workflow.return_value = {
            'id': 'wf_1',
            'name': 'Webhook Echo',
            'nodes': [{'id': 'n1', 'name': 'Webhook'}],
            'connections': {'Webhook': {'main': [[]]}},
            'settings': {},
            'active': True,
        }

        with tempfile.TemporaryDirectory() as tmp:
            result = export_workflow(spec, client, 'wf_1', output_dir=tmp)

            self.assertTrue(os.path.exists(result['json_path']))
            self.assertTrue(os.path.exists(result['readme_path']))
            self.assertEqual(result['slug'], 'webhook-echo')
            self.assertEqual(result['node_count'], 1)

            with open(result['json_path']) as f:
                written = json.load(f)
            self.assertNotIn('id', written)
            self.assertNotIn('active', written)
            self.assertEqual(written['name'], 'Webhook Echo')
            self.assertEqual(len(written['nodes']), 1)

    def test_creates_output_dir_if_missing(self):
        spec = _make_spec()
        client = MagicMock()
        client.get_workflow.return_value = {'name': 'X', 'nodes': [], 'connections': {}, 'settings': {}}

        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'nested', 'dir')
            result = export_workflow(spec, client, 'wf_1', output_dir=target)
            self.assertTrue(os.path.exists(result['json_path']))


if __name__ == '__main__':
    unittest.main()

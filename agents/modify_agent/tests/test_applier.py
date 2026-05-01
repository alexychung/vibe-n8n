"""Unit tests for applier — mutation logic for each tactical edit type."""
import copy
import unittest
from unittest.mock import MagicMock

from edits import Edit
from fetcher import ModifyError
from applier import (
    apply_edits, _apply_one, _rewrite_connection_node_name,
    _matches_snapshot, _set_path,
)
from helpers import make_workflow, make_node


class TestApplyOne(unittest.TestCase):

    def test_set_node_parameter(self):
        wf = make_workflow(nodes=[make_node('n1', 'Hook', parameters={'path': 'old'})])
        _apply_one(wf, Edit(type='set_node_parameter', node_id='n1',
                            path='parameters.path', new_value='new'))
        self.assertEqual(wf['nodes'][0]['parameters']['path'], 'new')

    def test_set_node_parameter_creates_intermediate_dicts(self):
        wf = make_workflow(nodes=[make_node('n1', 'Hook', parameters={})])
        _apply_one(wf, Edit(type='set_node_parameter', node_id='n1',
                            path='parameters.options.retry', new_value=True))
        self.assertEqual(wf['nodes'][0]['parameters']['options']['retry'], True)

    def test_set_node_parameter_into_list_index(self):
        wf = make_workflow(nodes=[make_node('n1', 'Set', parameters={
            'assignments': {'assignments': [
                {'id': 'a0', 'name': 'msg', 'value': 'old', 'type': 'string'},
            ]},
        })])
        _apply_one(wf, Edit(type='set_node_parameter', node_id='n1',
                            path='parameters.assignments.assignments[0].value',
                            new_value='new'))
        self.assertEqual(
            wf['nodes'][0]['parameters']['assignments']['assignments'][0]['value'],
            'new',
        )

    def test_rename_node_updates_connection_keys_and_targets(self):
        wf = make_workflow(
            nodes=[
                make_node('n1', 'Hook', type_='n8n-nodes-base.webhook'),
                make_node('n2', 'Resp'),
            ],
            connections={
                'Hook': {'main': [[{'node': 'Resp', 'type': 'main', 'index': 0}]]},
            },
        )
        _apply_one(wf, Edit(type='rename_node', node_id='n1',
                            old_name='Hook', new_name='HookV2'))
        self.assertEqual(wf['nodes'][0]['name'], 'HookV2')
        # Source key rewritten
        self.assertIn('HookV2', wf['connections'])
        self.assertNotIn('Hook', wf['connections'])

    def test_rename_node_updates_target_references(self):
        wf = make_workflow(
            nodes=[make_node('n1', 'A'), make_node('n2', 'B')],
            connections={
                'A': {'main': [[{'node': 'B', 'type': 'main', 'index': 0}]]},
            },
        )
        _apply_one(wf, Edit(type='rename_node', node_id='n2',
                            old_name='B', new_name='B-renamed'))
        # 'A' still keys its connections, but the target is now 'B-renamed'
        target = wf['connections']['A']['main'][0][0]['node']
        self.assertEqual(target, 'B-renamed')

    def test_set_node_setting(self):
        wf = make_workflow(nodes=[make_node('n1', 'API', parameters={
            'options': {'retry': {'retryOnFail': False, 'maxTries': 3}},
        })])
        _apply_one(wf, Edit(type='set_node_setting', node_id='n1',
                            path='parameters.options.retry.maxTries', new_value=5))
        self.assertEqual(
            wf['nodes'][0]['parameters']['options']['retry']['maxTries'], 5,
        )

    def test_update_credential_ref_preserves_name(self):
        wf = make_workflow(nodes=[make_node('n1', 'API',
            credentials={'httpHeaderAuth': {'id': 'old', 'name': 'My Auth'}})])
        _apply_one(wf, Edit(type='update_credential_ref', node_id='n1',
                            credential_type='httpHeaderAuth',
                            old_value='old', new_value='new'))
        self.assertEqual(wf['nodes'][0]['credentials']['httpHeaderAuth'], {
            'id': 'new', 'name': 'My Auth',
        })

    def test_update_credential_ref_creates_credentials_block_if_missing(self):
        wf = make_workflow(nodes=[make_node('n1', 'API')])
        _apply_one(wf, Edit(type='update_credential_ref', node_id='n1',
                            credential_type='httpHeaderAuth', new_value='cred-new'))
        self.assertEqual(
            wf['nodes'][0]['credentials']['httpHeaderAuth'], {'id': 'cred-new'},
        )

    def test_set_workflow_setting(self):
        wf = make_workflow(settings={'executionTimeout': 300})
        _apply_one(wf, Edit(type='set_workflow_setting', path='executionTimeout',
                            new_value=600))
        self.assertEqual(wf['settings']['executionTimeout'], 600)

    def test_rename_workflow(self):
        wf = make_workflow(name='Old')
        _apply_one(wf, Edit(type='rename_workflow', old_value='Old', new_value='New'))
        self.assertEqual(wf['name'], 'New')

    def test_unknown_edit_type_raises(self):
        wf = make_workflow()
        with self.assertRaises(ModifyError):
            _apply_one(wf, Edit(type='nope_not_real'))

    def test_node_disappears_during_apply_raises(self):
        wf = make_workflow()  # no nodes
        with self.assertRaises(ModifyError) as cm:
            _apply_one(wf, Edit(type='set_node_parameter', node_id='n1',
                                path='parameters.x', new_value='y'))
        self.assertIn('disappeared', str(cm.exception))


class TestRewriteConnections(unittest.TestCase):

    def test_unaffected_connections_untouched(self):
        wf = {'connections': {
            'Trigger': {'main': [[{'node': 'A', 'type': 'main', 'index': 0}]]},
            'A': {'main': [[{'node': 'B', 'type': 'main', 'index': 0}]]},
        }}
        # Rename C — which doesn't appear anywhere
        _rewrite_connection_node_name(wf, 'C', 'C-new')
        self.assertEqual(wf['connections']['Trigger']['main'][0][0]['node'], 'A')
        self.assertEqual(wf['connections']['A']['main'][0][0]['node'], 'B')

    def test_branching_node_preserved(self):
        # A node with two outputs (e.g. an IF) — both arrays preserved
        wf = {'connections': {
            'IF': {'main': [
                [{'node': 'TruePath', 'type': 'main', 'index': 0}],
                [{'node': 'FalsePath', 'type': 'main', 'index': 0}],
            ]},
        }}
        _rewrite_connection_node_name(wf, 'TruePath', 'YesPath')
        self.assertEqual(len(wf['connections']['IF']['main']), 2)
        self.assertEqual(wf['connections']['IF']['main'][0][0]['node'], 'YesPath')
        self.assertEqual(wf['connections']['IF']['main'][1][0]['node'], 'FalsePath')


class TestSnapshotMatching(unittest.TestCase):

    def test_identical_workflows_match(self):
        a = make_workflow(nodes=[make_node('n1', 'X')])
        b = make_workflow(nodes=[make_node('n1', 'X')])
        # Add ignored fields — should still match
        a['updatedAt'] = '2026-05-01T00:00:00Z'
        b['updatedAt'] = '2026-05-01T01:00:00Z'
        self.assertTrue(_matches_snapshot(a, b))

    def test_changed_node_param_does_not_match(self):
        a = make_workflow(nodes=[make_node('n1', 'X', parameters={'p': 'a'})])
        b = make_workflow(nodes=[make_node('n1', 'X', parameters={'p': 'b'})])
        self.assertFalse(_matches_snapshot(a, b))

    def test_dict_key_order_does_not_break_match(self):
        a = make_workflow(settings={'executionTimeout': 300, 'saveDataErrorExecution': 'all'})
        b = make_workflow(settings={'saveDataErrorExecution': 'all', 'executionTimeout': 300})
        self.assertTrue(_matches_snapshot(a, b))


class TestApplyEditsIntegration(unittest.TestCase):

    def test_apply_edits_full_flow_with_mock_client(self):
        snap_wf = make_workflow(nodes=[make_node('n1', 'Hook', parameters={'path': 'old'})])
        client = MagicMock()
        client.deactivate_workflow.return_value = {}
        client.get_workflow.return_value = copy.deepcopy(snap_wf)
        client.update_workflow.side_effect = lambda wid, modifier: modifier(copy.deepcopy(snap_wf))

        edits = [Edit(type='set_node_parameter', node_id='n1',
                      path='parameters.path', old_value='old', new_value='new')]

        result = apply_edits(
            client=client, workflow_id='wf-test',
            edits=edits, snapshot_workflow=snap_wf, was_active=True,
        )

        client.deactivate_workflow.assert_called_once_with('wf-test')
        self.assertEqual(result.edits_applied, edits)
        self.assertTrue(result.was_active)
        self.assertEqual(
            result.final_workflow['nodes'][0]['parameters']['path'], 'new',
        )

    def test_apply_edits_aborts_on_ui_drift(self):
        snap_wf = make_workflow(nodes=[make_node('n1', 'Hook', parameters={'path': 'old'})])
        # Live diverges from snapshot — UI edit happened
        live_wf = make_workflow(nodes=[
            make_node('n1', 'Hook', parameters={'path': 'someone-else-changed-this'}),
        ])
        client = MagicMock()
        client.deactivate_workflow.return_value = {}
        # The drift check now lives inside the update_workflow modifier so
        # the GET-and-mutate happen on the same JSON the PUT will use. The
        # mock invokes the modifier with the live (drifted) workflow.
        client.update_workflow.side_effect = lambda wid, modifier: modifier(copy.deepcopy(live_wf))

        edits = [Edit(type='set_node_parameter', node_id='n1',
                      path='parameters.path', old_value='old', new_value='new')]

        with self.assertRaises(ModifyError) as cm:
            apply_edits(client=client, workflow_id='wf-test',
                        edits=edits, snapshot_workflow=snap_wf, was_active=False)
        self.assertIn('externally', str(cm.exception))


class TestSetPathErrors(unittest.TestCase):

    def test_set_path_on_non_dict_raises(self):
        with self.assertRaises(ModifyError):
            _set_path({'a': 'string'}, 'a.b', 'x')

    def test_set_path_with_out_of_range_index_raises(self):
        with self.assertRaises(ModifyError):
            _set_path({'a': []}, 'a[5]', 'x')

    def test_set_path_empty_path_raises(self):
        with self.assertRaises(ModifyError):
            _set_path({}, '', 'x')


if __name__ == '__main__':
    unittest.main()

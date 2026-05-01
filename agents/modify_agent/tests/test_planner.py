"""Unit tests for the planner — validation against the live workflow."""
import unittest

from edits import Edit
from fetcher import ModifyError
from planner import validate_tactical_edits, _split_path, _get_path
from helpers import make_workflow, make_node


class TestPathHelpers(unittest.TestCase):

    def test_split_path_basic(self):
        self.assertEqual(_split_path('a.b.c'), ['a', 'b', 'c'])

    def test_split_path_with_index(self):
        self.assertEqual(_split_path('a.b[0].c'), ['a', 'b', 0, 'c'])

    def test_split_path_assignments_pattern(self):
        self.assertEqual(
            _split_path('parameters.assignments.assignments[0].value'),
            ['parameters', 'assignments', 'assignments', 0, 'value'],
        )

    def test_get_path_walks_lists(self):
        obj = {'a': {'b': [{'c': 'hit'}, {'c': 'miss'}]}}
        self.assertEqual(_get_path(obj, 'a.b[0].c'), 'hit')
        self.assertEqual(_get_path(obj, 'a.b[1].c'), 'miss')

    def test_get_path_returns_none_on_miss(self):
        self.assertIsNone(_get_path({'a': {}}, 'a.b.c'))
        self.assertIsNone(_get_path({}, 'missing'))
        self.assertIsNone(_get_path({'a': []}, 'a[5]'))


class TestValidateTacticalEdits(unittest.TestCase):

    def test_empty_edit_list_rejected(self):
        wf = make_workflow()
        with self.assertRaises(ModifyError) as cm:
            validate_tactical_edits([], wf)
        self.assertIn('empty edit list', str(cm.exception))

    def test_set_node_parameter_passes_with_matching_old_value(self):
        wf = make_workflow(nodes=[make_node('n1', 'Hook', parameters={'path': 'old-path'})])
        edit = Edit(type='set_node_parameter', node_id='n1',
                    path='parameters.path', old_value='old-path', new_value='new-path')
        result = validate_tactical_edits([edit], wf)
        self.assertEqual(result.edits, [edit])

    def test_set_node_parameter_rejects_old_value_drift(self):
        wf = make_workflow(nodes=[make_node('n1', 'Hook', parameters={'path': 'actual'})])
        edit = Edit(type='set_node_parameter', node_id='n1',
                    path='parameters.path', old_value='stale', new_value='new')
        with self.assertRaises(ModifyError) as cm:
            validate_tactical_edits([edit], wf)
        self.assertIn('drift', str(cm.exception))

    def test_unknown_node_id_rejected(self):
        wf = make_workflow(nodes=[make_node('n1', 'Hook')])
        edit = Edit(type='set_node_parameter', node_id='nope',
                    path='parameters.path', new_value='x')
        with self.assertRaises(ModifyError) as cm:
            validate_tactical_edits([edit], wf)
        self.assertIn("'nope'", str(cm.exception))

    def test_rename_node_collision_rejected(self):
        wf = make_workflow(nodes=[
            make_node('n1', 'Hook'),
            make_node('n2', 'Existing'),
        ])
        edit = Edit(type='rename_node', node_id='n1', old_name='Hook', new_name='Existing')
        with self.assertRaises(ModifyError) as cm:
            validate_tactical_edits([edit], wf)
        self.assertIn('collides', str(cm.exception))

    def test_rename_node_old_name_must_match(self):
        wf = make_workflow(nodes=[make_node('n1', 'CurrentName')])
        edit = Edit(type='rename_node', node_id='n1', old_name='WrongName', new_name='NewName')
        with self.assertRaises(ModifyError) as cm:
            validate_tactical_edits([edit], wf)
        self.assertIn('does not match', str(cm.exception))

    def test_rename_workflow_validates_old_value(self):
        wf = make_workflow(name='Real Name')
        edit = Edit(type='rename_workflow', old_value='Wrong Name', new_value='New')
        with self.assertRaises(ModifyError):
            validate_tactical_edits([edit], wf)
        # Correct old_value passes
        edit_ok = Edit(type='rename_workflow', old_value='Real Name', new_value='New')
        result = validate_tactical_edits([edit_ok], wf)
        self.assertEqual(result.edits, [edit_ok])

    def test_set_workflow_setting_validates(self):
        wf = make_workflow(settings={'executionTimeout': 300})
        edit = Edit(type='set_workflow_setting', path='executionTimeout',
                    old_value=300, new_value=600)
        result = validate_tactical_edits([edit], wf)
        self.assertEqual(result.edits, [edit])

    def test_update_credential_ref_validates_existing_id(self):
        wf = make_workflow(nodes=[make_node('n1', 'API',
            credentials={'httpHeaderAuth': {'id': 'cred-old', 'name': 'Old Cred'}})])
        edit = Edit(type='update_credential_ref', node_id='n1',
                    credential_type='httpHeaderAuth',
                    old_value='cred-old', new_value='cred-new')
        result = validate_tactical_edits([edit], wf)
        self.assertEqual(result.edits, [edit])

    def test_structural_edit_in_tactical_list_rejected(self):
        wf = make_workflow()
        edit = Edit(type='add_node', new_node={'name': 'X'})
        with self.assertRaises(ModifyError) as cm:
            validate_tactical_edits([edit], wf)
        self.assertIn('structural', str(cm.exception))


if __name__ == '__main__':
    unittest.main()

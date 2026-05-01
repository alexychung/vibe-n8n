"""Unit tests for Edit dataclass serialization."""
import unittest

from edits import Edit, TACTICAL_TYPES, STRUCTURAL_TYPES


class TestEditClassification(unittest.TestCase):

    def test_tactical_types_classify_correctly(self):
        for t in TACTICAL_TYPES:
            self.assertTrue(Edit(type=t).is_tactical(), t)
            self.assertFalse(Edit(type=t).is_structural(), t)

    def test_structural_types_classify_correctly(self):
        for t in STRUCTURAL_TYPES:
            self.assertTrue(Edit(type=t).is_structural(), t)
            self.assertFalse(Edit(type=t).is_tactical(), t)


class TestEditSerialization(unittest.TestCase):

    def test_to_dict_omits_empty_fields(self):
        e = Edit(type='set_node_parameter', node_id='n1', path='parameters.path',
                 old_value='a', new_value='b')
        d = e.to_dict()
        self.assertEqual(d, {
            'type': 'set_node_parameter',
            'node_id': 'n1',
            'path': 'parameters.path',
            'old_value': 'a',
            'new_value': 'b',
        })
        self.assertNotIn('old_name', d)
        self.assertNotIn('credential_type', d)

    def test_round_trip(self):
        original = Edit(type='rename_node', node_id='n1', old_name='Old', new_name='New')
        restored = Edit.from_dict(original.to_dict())
        self.assertEqual(restored.type, original.type)
        self.assertEqual(restored.node_id, original.node_id)
        self.assertEqual(restored.old_name, original.old_name)
        self.assertEqual(restored.new_name, original.new_name)

    def test_to_dict_preserves_falsy_old_value(self):
        # old_value=False or 0 is meaningful — must be preserved
        e = Edit(type='set_node_setting', node_id='n1', path='retryOnFail',
                 old_value=False, new_value=True)
        d = e.to_dict()
        self.assertEqual(d['old_value'], False)
        self.assertEqual(d['new_value'], True)


if __name__ == '__main__':
    unittest.main()

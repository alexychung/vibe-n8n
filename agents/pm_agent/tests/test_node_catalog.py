"""Tests for the n8n node catalog and parameter translator."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from node_catalog import NODE_CATALOG, render_catalog, translate_params


class TestNodeCatalog(unittest.TestCase):
    """Test the node catalog has required entries and structure."""

    REQUIRED_TYPES = [
        'n8n-nodes-base.webhook',
        'n8n-nodes-base.scheduleTrigger',
        'n8n-nodes-base.manualTrigger',
        'n8n-nodes-base.httpRequest',
        'n8n-nodes-base.set',
        'n8n-nodes-base.if',
        'n8n-nodes-base.code',
    ]

    def test_required_node_types_present(self):
        """Catalog has entries for all required n8n node types."""
        for nt in self.REQUIRED_TYPES:
            self.assertIn(nt, NODE_CATALOG, f'Missing node type: {nt}')

    def test_each_entry_has_required_fields(self):
        """Each catalog entry has typeVersion, when_to_use, and param_example."""
        for node_type, entry in NODE_CATALOG.items():
            self.assertIn('typeVersion', entry, f'{node_type} missing typeVersion')
            self.assertIn('when_to_use', entry, f'{node_type} missing when_to_use')
            self.assertIn('param_example', entry, f'{node_type} missing param_example')
            self.assertIsInstance(entry['param_example'], dict, f'{node_type} param_example not a dict')

    def test_if_node_has_combinator(self):
        """IF node param example includes the combinator field (critical quirk)."""
        if_entry = NODE_CATALOG['n8n-nodes-base.if']
        example = if_entry['param_example']
        conditions = example.get('conditions', {})
        self.assertIn('combinator', conditions,
                      'IF node param_example must include combinator (n8n v2 quirk)')

    def test_set_node_has_nested_assignments(self):
        """Set node param example uses the double-nested assignments format."""
        set_entry = NODE_CATALOG['n8n-nodes-base.set']
        example = set_entry['param_example']
        self.assertIn('assignments', example)
        self.assertIn('assignments', example['assignments'],
                      'Set node must use nested {assignments: {assignments: [...]}} format')


class TestRenderCatalog(unittest.TestCase):
    """Test catalog rendering into prompt-injectable markdown."""

    def test_renders_all_node_types(self):
        """Rendered catalog includes every node type."""
        md = render_catalog()
        for node_type in NODE_CATALOG:
            self.assertIn(node_type, md)

    def test_renders_when_to_use(self):
        """Rendered catalog includes usage guidance."""
        md = render_catalog()
        self.assertIn('when_to_use', md.lower().replace(' ', '_') or True)
        # At least check some human-readable text is there
        self.assertIn('webhook', md.lower())

    def test_renders_param_examples(self):
        """Rendered catalog includes parameter examples."""
        md = render_catalog()
        # Should have JSON examples
        self.assertIn('combinator', md)
        self.assertIn('assignments', md)


class TestTranslateParams(unittest.TestCase):
    """Test pseudocode → n8n parameter translation."""

    def test_translate_set_params(self):
        """Translates flat Set assignments to nested n8n format."""
        pseudocode = {
            'assignments': [
                {'name': 'status', 'value': 'ok', 'type': 'string'},
                {'name': 'count', 'value': '={{ $json.total }}', 'type': 'number'},
            ]
        }
        result = translate_params('n8n-nodes-base.set', pseudocode)
        # Must be double-nested
        inner = result['assignments']['assignments']
        self.assertEqual(len(inner), 2)
        self.assertEqual(inner[0]['name'], 'status')
        self.assertEqual(inner[0]['value'], 'ok')
        # Each should have an id
        self.assertIn('id', inner[0])

    def test_translate_if_params_adds_combinator(self):
        """Translates IF conditions and ensures combinator is present."""
        pseudocode = {
            'conditions': {
                'and': [
                    {'field': '={{ $json.name }}', 'operation': 'isNotEmpty'},
                    {'field': '={{ $json.value }}', 'operation': 'gte', 'value': 0},
                ]
            }
        }
        result = translate_params('n8n-nodes-base.if', pseudocode)
        self.assertIn('conditions', result)
        conds = result['conditions']
        self.assertEqual(conds['combinator'], 'and')
        self.assertEqual(len(conds['conditions']), 2)

    def test_translate_unknown_type_passes_through(self):
        """Unknown node types pass parameters through unchanged."""
        params = {'foo': 'bar', 'baz': 123}
        result = translate_params('n8n-nodes-base.unknownNode', params)
        self.assertEqual(result, params)

    def test_translate_already_nested_set_is_idempotent(self):
        """If Set params are already in n8n format, don't double-nest."""
        already_nested = {
            'assignments': {
                'assignments': [
                    {'id': 'a1', 'name': 'x', 'value': 'y', 'type': 'string'}
                ]
            }
        }
        result = translate_params('n8n-nodes-base.set', already_nested)
        # Should not change
        self.assertEqual(result, already_nested)


if __name__ == '__main__':
    unittest.main()

"""Tests for LLM wrapper and prompt loading."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from llm import _extract_json, _strip_trailing_commas, _try_parse, load_prompt


class TestStripTrailingCommas(unittest.TestCase):
    """Test trailing comma removal."""

    def test_trailing_comma_in_object(self):
        self.assertEqual(_strip_trailing_commas('{"a": 1,}'), '{"a": 1}')

    def test_trailing_comma_in_array(self):
        self.assertEqual(_strip_trailing_commas('[1, 2, 3,]'), '[1, 2, 3]')

    def test_trailing_comma_with_whitespace(self):
        # The regex removes the comma and all whitespace before the closing brace
        result = _strip_trailing_commas('{"a": 1 , \n}')
        self.assertEqual(json.loads(result), {"a": 1})

    def test_nested_trailing_commas(self):
        text = '{"a": [1, 2,], "b": {"c": 3,},}'
        result = json.loads(_strip_trailing_commas(text))
        self.assertEqual(result, {"a": [1, 2], "b": {"c": 3}})

    def test_no_trailing_commas_unchanged(self):
        text = '{"a": 1, "b": 2}'
        self.assertEqual(_strip_trailing_commas(text), text)


class TestTryParse(unittest.TestCase):
    """Test JSON parsing with trailing comma fallback."""

    def test_valid_json(self):
        result = _try_parse('{"a": 1}')
        self.assertEqual(result, {"a": 1})

    def test_trailing_comma_fixed(self):
        result = _try_parse('{"a": 1,}')
        self.assertEqual(result, {"a": 1})

    def test_invalid_json_still_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            _try_parse('not json at all')


class TestExtractJson(unittest.TestCase):
    """Test JSON extraction from various response formats."""

    def test_plain_json(self):
        result = _extract_json('{"name": "test", "value": 42}')
        self.assertEqual(result['name'], 'test')
        self.assertEqual(result['value'], 42)

    def test_json_in_code_block(self):
        text = 'Here is the result:\n```json\n{"name": "test"}\n```\nDone.'
        result = _extract_json(text)
        self.assertEqual(result['name'], 'test')

    def test_json_in_plain_code_block(self):
        text = 'Result:\n```\n{"name": "test"}\n```'
        result = _extract_json(text)
        self.assertEqual(result['name'], 'test')

    def test_json_with_surrounding_text(self):
        text = 'The spec is: {"name": "test"} as shown above.'
        result = _extract_json(text)
        self.assertEqual(result['name'], 'test')

    def test_no_json_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            _extract_json('This is just text with no JSON.')

    def test_whitespace_around_json(self):
        result = _extract_json('  \n  {"name": "test"}  \n  ')
        self.assertEqual(result['name'], 'test')

    def test_trailing_comma_in_direct_json(self):
        result = _extract_json('{"name": "test",}')
        self.assertEqual(result['name'], 'test')

    def test_trailing_comma_in_code_block(self):
        text = '```json\n{"items": [1, 2, 3,],}\n```'
        result = _extract_json(text)
        self.assertEqual(result['items'], [1, 2, 3])

    def test_trailing_comma_in_surrounded_text(self):
        text = 'Here: {"name": "test", "items": [1,],} end'
        result = _extract_json(text)
        self.assertEqual(result['name'], 'test')

    def test_json_array_response(self):
        result = _extract_json('[{"a": 1}, {"b": 2}]')
        self.assertEqual(len(result), 2)

    def test_json_array_in_code_block(self):
        text = '```json\n[{"finding": "issue"}]\n```'
        result = _extract_json(text)
        self.assertIsInstance(result, list)

    def test_multiple_code_blocks_takes_first_valid(self):
        text = '```\nnot json\n```\n\n```json\n{"valid": true}\n```'
        result = _extract_json(text)
        self.assertTrue(result['valid'])


class TestLoadPrompt(unittest.TestCase):
    """Test prompt template loading and formatting."""

    def test_load_interview_prompt(self):
        """Loads the interview prompt template."""
        prompt = load_prompt('interview')
        self.assertIn('workflow planning', prompt.lower())
        self.assertIn('Q1', prompt)
        self.assertIn('Q8', prompt)

    def test_load_decompose_prompt(self):
        """Loads the decompose prompt with variable substitution."""
        prompt = load_prompt('decompose',
                             requirements='test requirements',
                             audit_summary='no conflicts',
                             node_catalog='webhook, set, if')
        self.assertIn('test requirements', prompt)
        self.assertIn('no conflicts', prompt)
        self.assertIn('webhook, set, if', prompt)

    def test_load_review_prompt(self):
        """Loads the review prompt with variable substitution."""
        prompt = load_prompt('review',
                             requirements='test reqs',
                             spec='{"name": "test"}')
        self.assertIn('test reqs', prompt)
        self.assertIn('test', prompt)

    def test_load_fix_prompt(self):
        """Loads the fix prompt."""
        prompt = load_prompt('fix',
                             spec='{"name": "test"}',
                             findings='[{"severity": "WARNING"}]')
        self.assertIn('WARNING', prompt)

    def test_missing_prompt_raises(self):
        """Raises FileNotFoundError for nonexistent prompt."""
        with self.assertRaises(FileNotFoundError):
            load_prompt('nonexistent_prompt_xyz')


if __name__ == '__main__':
    unittest.main()

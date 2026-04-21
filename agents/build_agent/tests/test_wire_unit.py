"""Unit tests for WIRE phase — pure logic, no n8n required.

Tests parameter translation, connection building, and the wire function
with a mocked API client.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from models import WorkflowSpec, Trigger, Step, Gate, TestCase
from wire import (
    _translate_set_params,
    _translate_if_params,
    _translate_respond_to_webhook_params,
    _configure_node,
    _build_connections,
    _find_node_by_id,
    _find_node_by_name,
    wire,
)


def _make_spec(**overrides):
    defaults = dict(
        workflow_name='Test',
        trigger=Trigger(type='webhook', path='test', method='POST', description='Webhook'),
        steps=[Step(id='s1', name='A', node_type='n8n-nodes-base.set')],
        gates=[],
        test_cases=[TestCase(name='tc1', input={}, expected={})],
    )
    defaults.update(overrides)
    return WorkflowSpec(**defaults)


class TestTranslateSetParams(unittest.TestCase):

    def test_converts_list_to_nested_format(self):
        params = {
            'assignments': [
                {'name': 'status', 'value': 'ok', 'type': 'string'},
                {'name': 'count', 'value': '5', 'type': 'number'},
            ]
        }
        result = _translate_set_params(params)
        inner = result['assignments']['assignments']
        self.assertEqual(len(inner), 2)
        self.assertEqual(inner[0]['id'], 'a0')
        self.assertEqual(inner[0]['name'], 'status')
        self.assertEqual(inner[0]['value'], 'ok')
        self.assertEqual(inner[1]['id'], 'a1')

    def test_already_nested_passes_through(self):
        params = {
            'assignments': {
                'assignments': [{'id': 'a1', 'name': 'x', 'value': '1', 'type': 'string'}]
            }
        }
        result = _translate_set_params(params)
        self.assertEqual(result, params)

    def test_default_type_is_string(self):
        params = {'assignments': [{'name': 'foo', 'value': 'bar'}]}
        result = _translate_set_params(params)
        self.assertEqual(result['assignments']['assignments'][0]['type'], 'string')


class TestTranslateIfParams(unittest.TestCase):

    def test_basic_conditions(self):
        params = {
            'conditions': {
                'and': [
                    {'field': '={{ $json.name }}', 'operation': 'isNotEmpty'},
                    {'field': '={{ $json.value }}', 'operation': 'gte', 'value': 0},
                ]
            }
        }
        result = _translate_if_params(params)
        conds = result['conditions']
        self.assertEqual(conds['combinator'], 'and')
        self.assertEqual(len(conds['conditions']), 2)

        # First condition: string operation
        c0 = conds['conditions'][0]
        self.assertEqual(c0['leftValue'], '={{ $json.name }}')
        self.assertEqual(c0['operator']['type'], 'string')
        self.assertEqual(c0['operator']['operation'], 'notEmpty')

        # Second condition: number operation
        c1 = conds['conditions'][1]
        self.assertEqual(c1['leftValue'], '={{ $json.value }}')
        self.assertEqual(c1['operator']['type'], 'number')
        self.assertEqual(c1['operator']['operation'], 'gte')
        self.assertEqual(c1['rightValue'], '0')

    def test_operation_mapping(self):
        for spec_op, n8n_op in [
            ('isNotEmpty', 'notEmpty'),
            ('isEmpty', 'empty'),
            ('gte', 'gte'),
            ('equals', 'equals'),
        ]:
            params = {'conditions': {'and': [{'field': 'x', 'operation': spec_op}]}}
            result = _translate_if_params(params)
            self.assertEqual(result['conditions']['conditions'][0]['operator']['operation'], n8n_op)

    def test_empty_conditions(self):
        params = {'conditions': {'and': []}}
        result = _translate_if_params(params)
        self.assertEqual(result['conditions']['conditions'], [])
        self.assertEqual(result['conditions']['combinator'], 'and')

    def test_or_conditions_translated(self):
        """OR conditions should produce combinator='or', not be silently dropped."""
        params = {
            'conditions': {
                'or': [
                    {'field': '={{ $json.email }}', 'operation': 'isNotEmpty'},
                    {'field': '={{ $json.phone }}', 'operation': 'isNotEmpty'},
                ]
            }
        }
        result = _translate_if_params(params)
        self.assertEqual(result['conditions']['combinator'], 'or')
        self.assertEqual(len(result['conditions']['conditions']), 2)
        self.assertEqual(result['conditions']['conditions'][0]['operator']['operation'], 'notEmpty')

    def test_or_conditions_not_silently_empty(self):
        """Regression: OR conditions must not produce empty conditions list."""
        params = {
            'conditions': {
                'or': [
                    {'field': '={{ $json.status }}', 'operation': 'equals', 'value': 'active'},
                ]
            }
        }
        result = _translate_if_params(params)
        self.assertEqual(len(result['conditions']['conditions']), 1)
        self.assertEqual(result['conditions']['combinator'], 'or')

    def test_boolean_true_equals_uses_boolean_operator(self):
        """When checking field == 'true', use n8n boolean operator isTrue."""
        params = {
            'conditions': {
                'and': [
                    {'field': '={{ $json.success }}', 'operation': 'equals', 'value': 'true'}
                ]
            }
        }
        result = _translate_if_params(params)
        cond = result['conditions']['conditions'][0]
        self.assertEqual(cond['leftValue'], '={{ $json.success }}')
        self.assertEqual(cond['operator']['type'], 'boolean')
        self.assertEqual(cond['operator']['operation'], 'true')
        # Boolean operators don't need a rightValue
        self.assertNotIn('rightValue', cond)

    def test_boolean_false_equals_uses_boolean_operator(self):
        """When checking field == 'false', use n8n boolean operator isFalse."""
        params = {
            'conditions': {
                'and': [
                    {'field': '={{ $json.is_error }}', 'operation': 'equals', 'value': 'false'}
                ]
            }
        }
        result = _translate_if_params(params)
        cond = result['conditions']['conditions'][0]
        self.assertEqual(cond['operator']['type'], 'boolean')
        self.assertEqual(cond['operator']['operation'], 'false')
        self.assertNotIn('rightValue', cond)

    def test_boolean_true_value_not_string(self):
        """When value is Python bool True (not string), also use boolean operator."""
        params = {
            'conditions': {
                'and': [
                    {'field': '={{ $json.valid }}', 'operation': 'equals', 'value': True}
                ]
            }
        }
        result = _translate_if_params(params)
        cond = result['conditions']['conditions'][0]
        self.assertEqual(cond['operator']['type'], 'boolean')
        self.assertEqual(cond['operator']['operation'], 'true')

    def test_non_boolean_equals_stays_as_number(self):
        """Regular equals with a numeric value should still use number type."""
        params = {
            'conditions': {
                'and': [
                    {'field': '={{ $json.count }}', 'operation': 'equals', 'value': 42}
                ]
            }
        }
        result = _translate_if_params(params)
        cond = result['conditions']['conditions'][0]
        self.assertEqual(cond['operator']['type'], 'number')
        self.assertEqual(cond['operator']['operation'], 'equals')
        self.assertEqual(cond['rightValue'], '42')

    def test_already_translated_boolean_gets_fixed(self):
        """When LLM outputs native n8n format with boolean equals, fix it."""
        params = {
            'conditions': {
                'options': {'caseSensitive': True, 'leftValue': ''},
                'conditions': [
                    {
                        'id': 'cond_0',
                        'leftValue': '={{ $json.fields_valid }}',
                        'rightValue': 'true',
                        'operator': {'type': 'number', 'operation': 'equals'},
                    }
                ],
                'combinator': 'and',
            }
        }
        result = _translate_if_params(params)
        cond = result['conditions']['conditions'][0]
        self.assertEqual(cond['operator']['type'], 'boolean')
        self.assertEqual(cond['operator']['operation'], 'true')
        self.assertNotIn('rightValue', cond)

    def test_boolean_operator_unwraps_string_cast_on_leftvalue(self):
        """LLM fixer sometimes wraps boolean expressions in String(...) — unwrap it."""
        params = {
            'conditions': {
                'options': {'caseSensitive': True, 'leftValue': ''},
                'conditions': [
                    {
                        'id': 'cond_0',
                        'leftValue': '={{ String($json.valid) }}',
                        'operator': {'type': 'boolean', 'operation': 'true'},
                    }
                ],
                'combinator': 'and',
            }
        }
        result = _translate_if_params(params)
        cond = result['conditions']['conditions'][0]
        self.assertEqual(cond['leftValue'], '={{ $json.valid }}')

    def test_boolean_operator_leaves_plain_expression_alone(self):
        params = {
            'conditions': {
                'options': {'caseSensitive': True, 'leftValue': ''},
                'conditions': [
                    {
                        'id': 'cond_0',
                        'leftValue': '={{ $json.valid }}',
                        'operator': {'type': 'boolean', 'operation': 'true'},
                    }
                ],
                'combinator': 'and',
            }
        }
        result = _translate_if_params(params)
        self.assertEqual(
            result['conditions']['conditions'][0]['leftValue'],
            '={{ $json.valid }}',
        )

    def test_string_operator_keeps_string_cast(self):
        """Unwrapping only applies to boolean operators, not string ones."""
        params = {
            'conditions': {
                'options': {'caseSensitive': True, 'leftValue': ''},
                'conditions': [
                    {
                        'id': 'cond_0',
                        'leftValue': '={{ String($json.foo) }}',
                        'rightValue': 'bar',
                        'operator': {'type': 'string', 'operation': 'equals'},
                    }
                ],
                'combinator': 'and',
            }
        }
        result = _translate_if_params(params)
        self.assertEqual(
            result['conditions']['conditions'][0]['leftValue'],
            '={{ String($json.foo) }}',
        )

    def test_already_translated_non_boolean_unchanged(self):
        """Native n8n format with numeric equals should stay unchanged."""
        params = {
            'conditions': {
                'options': {'caseSensitive': True, 'leftValue': ''},
                'conditions': [
                    {
                        'id': 'cond_0',
                        'leftValue': '={{ $json.count }}',
                        'rightValue': '42',
                        'operator': {'type': 'number', 'operation': 'gte'},
                    }
                ],
                'combinator': 'and',
            }
        }
        result = _translate_if_params(params)
        cond = result['conditions']['conditions'][0]
        self.assertEqual(cond['operator']['type'], 'number')
        self.assertEqual(cond['operator']['operation'], 'gte')
        self.assertEqual(cond['rightValue'], '42')

    def test_string_equals_stays_as_string(self):
        """Equals with a non-boolean string value should use string type."""
        params = {
            'conditions': {
                'and': [
                    {'field': '={{ $json.status }}', 'operation': 'equals', 'value': 'active'}
                ]
            }
        }
        result = _translate_if_params(params)
        cond = result['conditions']['conditions'][0]
        self.assertEqual(cond['operator']['type'], 'string')
        self.assertEqual(cond['operator']['operation'], 'equals')
        self.assertEqual(cond['rightValue'], 'active')


class TestConfigureNode(unittest.TestCase):

    def test_set_node_translated(self):
        node = {'parameters': {}}
        step = Step(id='s1', name='X', node_type='n8n-nodes-base.set', parameters={
            'assignments': [{'name': 'a', 'value': 'b'}]
        })
        _configure_node(node, step)
        self.assertIn('assignments', node['parameters']['assignments'])

    def test_if_node_translated(self):
        node = {'parameters': {}}
        step = Step(id='s1', name='X', node_type='n8n-nodes-base.if', parameters={
            'conditions': {'and': [{'field': 'x', 'operation': 'isNotEmpty'}]}
        })
        _configure_node(node, step)
        self.assertEqual(node['parameters']['conditions']['combinator'], 'and')

    def test_other_node_passthrough(self):
        node = {'parameters': {}}
        step = Step(id='s1', name='X', node_type='n8n-nodes-base.httpRequest', parameters={
            'url': 'https://example.com', 'method': 'GET'
        })
        _configure_node(node, step)
        self.assertEqual(node['parameters']['url'], 'https://example.com')


class TestBuildConnections(unittest.TestCase):

    def test_sequential_steps(self):
        spec = _make_spec(steps=[
            Step(id='s1', name='A', node_type='n8n-nodes-base.set'),
            Step(id='s2', name='B', node_type='n8n-nodes-base.set'),
            Step(id='s3', name='C', node_type='n8n-nodes-base.set'),
        ])
        nodes = [
            {'id': 'trigger', 'name': 'Webhook'},
            {'id': 's1', 'name': 'A'},
            {'id': 's2', 'name': 'B'},
            {'id': 's3', 'name': 'C'},
        ]
        conns = _build_connections(spec, nodes)

        # Trigger -> A
        self.assertEqual(conns['Webhook']['main'][0][0]['node'], 'A')
        # A -> B
        self.assertEqual(conns['A']['main'][0][0]['node'], 'B')
        # B -> C
        self.assertEqual(conns['B']['main'][0][0]['node'], 'C')
        # C has no outgoing connection
        self.assertNotIn('C', conns)

    def test_gate_branching(self):
        spec = _make_spec(
            steps=[
                Step(id='s1', name='Check', node_type='n8n-nodes-base.if'),
                Step(id='s2', name='Success', node_type='n8n-nodes-base.set'),
                Step(id='s3', name='Error', node_type='n8n-nodes-base.set'),
            ],
            gates=[Gate(after_step='s1', pass_to='s2', fail_to='s3')],
        )
        nodes = [
            {'id': 'trigger', 'name': 'Webhook'},
            {'id': 's1', 'name': 'Check'},
            {'id': 's2', 'name': 'Success'},
            {'id': 's3', 'name': 'Error'},
        ]
        conns = _build_connections(spec, nodes)

        # Trigger -> Check
        self.assertEqual(conns['Webhook']['main'][0][0]['node'], 'Check')
        # Check true (output 0) -> Success
        self.assertEqual(conns['Check']['main'][0][0]['node'], 'Success')
        # Check false (output 1) -> Error
        self.assertEqual(conns['Check']['main'][1][0]['node'], 'Error')


class TestGateContinuation(unittest.TestCase):
    """Tests for gate branch targets connecting to continuation steps."""

    def test_branch_target_wires_to_next_main_step(self):
        """After branching, the success step should connect to the next main step."""
        spec = _make_spec(
            steps=[
                Step(id='s1', name='Check', node_type='n8n-nodes-base.if'),
                Step(id='s2', name='Success', node_type='n8n-nodes-base.set'),
                Step(id='s3', name='Error', node_type='n8n-nodes-base.set'),
                Step(id='s4', name='Final', node_type='n8n-nodes-base.set'),
            ],
            gates=[Gate(after_step='s1', pass_to='s2', fail_to='s3')],
        )
        nodes = [
            {'id': 'trigger', 'name': 'Webhook'},
            {'id': 's1', 'name': 'Check'},
            {'id': 's2', 'name': 'Success'},
            {'id': 's3', 'name': 'Error'},
            {'id': 's4', 'name': 'Final'},
        ]
        conns = _build_connections(spec, nodes)

        # s1 gates to s2/s3
        self.assertEqual(conns['Check']['main'][0][0]['node'], 'Success')
        self.assertEqual(conns['Check']['main'][1][0]['node'], 'Error')
        # Both branches continue to Final
        self.assertEqual(conns['Success']['main'][0][0]['node'], 'Final')
        self.assertEqual(conns['Error']['main'][0][0]['node'], 'Final')

    def test_terminal_branches_no_continuation(self):
        """When there are no steps after the gate, branches stay terminal."""
        spec = _make_spec(
            steps=[
                Step(id='s1', name='Check', node_type='n8n-nodes-base.if'),
                Step(id='s2', name='Success', node_type='n8n-nodes-base.set'),
                Step(id='s3', name='Error', node_type='n8n-nodes-base.set'),
            ],
            gates=[Gate(after_step='s1', pass_to='s2', fail_to='s3')],
        )
        nodes = [
            {'id': 'trigger', 'name': 'Webhook'},
            {'id': 's1', 'name': 'Check'},
            {'id': 's2', 'name': 'Success'},
            {'id': 's3', 'name': 'Error'},
        ]
        conns = _build_connections(spec, nodes)

        # Branches are terminal — no outgoing connections
        self.assertNotIn('Success', conns)
        self.assertNotIn('Error', conns)


    def test_two_sequential_gates(self):
        """Two IF gates in sequence: step1→gate1→step2→step3→gate2→step4→step5."""
        spec = _make_spec(
            steps=[
                Step(id='s1', name='Validate', node_type='n8n-nodes-base.if'),
                Step(id='s2', name='Valid Branch', node_type='n8n-nodes-base.set'),
                Step(id='s3', name='Process', node_type='n8n-nodes-base.code'),
                Step(id='s4', name='Route', node_type='n8n-nodes-base.if'),
                Step(id='s5', name='Path A', node_type='n8n-nodes-base.set'),
                Step(id='s6', name='Path B', node_type='n8n-nodes-base.set'),
                Step(id='s7', name='Invalid Branch', node_type='n8n-nodes-base.set'),
            ],
            gates=[
                Gate(after_step='s1', pass_to='s2', fail_to='s7'),
                Gate(after_step='s4', pass_to='s5', fail_to='s6'),
            ],
        )
        nodes = [
            {'id': 'trigger', 'name': 'Webhook'},
            {'id': 's1', 'name': 'Validate'},
            {'id': 's2', 'name': 'Valid Branch'},
            {'id': 's3', 'name': 'Process'},
            {'id': 's4', 'name': 'Route'},
            {'id': 's5', 'name': 'Path A'},
            {'id': 's6', 'name': 'Path B'},
            {'id': 's7', 'name': 'Invalid Branch'},
        ]
        conns = _build_connections(spec, nodes)

        # Trigger → s1 (Validate)
        self.assertEqual(conns['Webhook']['main'][0][0]['node'], 'Validate')
        # s1 gates: true → s2, false → s7
        self.assertEqual(conns['Validate']['main'][0][0]['node'], 'Valid Branch')
        self.assertEqual(conns['Validate']['main'][1][0]['node'], 'Invalid Branch')
        # s2 (Valid Branch) → s3 (Process) — continuation after first gate
        self.assertEqual(conns['Valid Branch']['main'][0][0]['node'], 'Process')
        # s3 (Process) → s4 (Route)
        self.assertEqual(conns['Process']['main'][0][0]['node'], 'Route')
        # s4 gates: true → s5, false → s6
        self.assertEqual(conns['Route']['main'][0][0]['node'], 'Path A')
        self.assertEqual(conns['Route']['main'][1][0]['node'], 'Path B')


    def test_gate_with_fail_branch_after_second_gate(self):
        """Fail branch from gate 1 is listed after gate 2's targets in step order.

        Real pattern: Validate(IF) → Process → Route(IF) → PathA / PathB / ErrorResponse
        Where ErrorResponse is fail_to for gate 1 but listed last.
        """
        spec = _make_spec(
            steps=[
                Step(id='s1', name='Validate', node_type='n8n-nodes-base.if'),
                Step(id='s2', name='Process', node_type='n8n-nodes-base.code'),
                Step(id='s3', name='Success', node_type='n8n-nodes-base.set'),
                Step(id='s4', name='ErrorResp', node_type='n8n-nodes-base.set'),
            ],
            gates=[
                Gate(after_step='s1', pass_to='s2', fail_to='s4'),
            ],
        )
        nodes = [
            {'id': 'trigger', 'name': 'Webhook'},
            {'id': 's1', 'name': 'Validate'},
            {'id': 's2', 'name': 'Process'},
            {'id': 's3', 'name': 'Success'},
            {'id': 's4', 'name': 'ErrorResp'},
        ]
        conns = _build_connections(spec, nodes)

        # Gate: true → s2 (Process), false → s4 (ErrorResp)
        self.assertEqual(conns['Validate']['main'][0][0]['node'], 'Process')
        self.assertEqual(conns['Validate']['main'][1][0]['node'], 'ErrorResp')
        # s2 (Process) should connect to s3 (Success) — next main step after pass branch
        self.assertEqual(conns['Process']['main'][0][0]['node'], 'Success')


class TestGateValidation(unittest.TestCase):
    """Tests that invalid gate targets are caught."""

    def test_unknown_pass_to_raises(self):
        spec = _make_spec(
            steps=[Step(id='s1', name='Check', node_type='n8n-nodes-base.if')],
            gates=[Gate(after_step='s1', pass_to='nonexistent')],
        )
        nodes = [
            {'id': 'trigger', 'name': 'Webhook'},
            {'id': 's1', 'name': 'Check'},
        ]
        with self.assertRaises(ValueError) as ctx:
            _build_connections(spec, nodes)
        self.assertIn('unknown step', str(ctx.exception))

    def test_unknown_fail_to_raises(self):
        spec = _make_spec(
            steps=[Step(id='s1', name='Check', node_type='n8n-nodes-base.if')],
            gates=[Gate(after_step='s1', fail_to='nonexistent')],
        )
        nodes = [
            {'id': 'trigger', 'name': 'Webhook'},
            {'id': 's1', 'name': 'Check'},
        ]
        with self.assertRaises(ValueError) as ctx:
            _build_connections(spec, nodes)
        self.assertIn('unknown step', str(ctx.exception))


class TestTranslateRespondToWebhookParams(unittest.TestCase):
    """respondToWebhook requires responseCode under options.responseCode; PM
    specs put it top-level and n8n silently falls back to 200 on pass-through."""

    def test_moves_response_code_into_options(self):
        out = _translate_respond_to_webhook_params(
            {'respondWith': 'json', 'responseCode': 400, 'responseBody': '={{ $json }}'}
        )
        self.assertEqual(out['respondWith'], 'json')
        self.assertEqual(out['responseBody'], '={{ $json }}')
        self.assertNotIn('responseCode', out)
        self.assertEqual(out['options'], {'responseCode': 400})

    def test_preserves_existing_options(self):
        out = _translate_respond_to_webhook_params({
            'respondWith': 'json',
            'responseCode': 404,
            'options': {'responseHeaders': {'X-Test': '1'}},
        })
        self.assertEqual(out['options']['responseCode'], 404)
        self.assertEqual(out['options']['responseHeaders'], {'X-Test': '1'})

    def test_preserves_dynamic_expression(self):
        out = _translate_respond_to_webhook_params(
            {'responseCode': '={{ $json.code }}'}
        )
        self.assertEqual(out['options'], {'responseCode': '={{ $json.code }}'})

    def test_passthrough_when_no_response_code(self):
        params = {'respondWith': 'json', 'responseBody': '={{ $json }}'}
        out = _translate_respond_to_webhook_params(params)
        self.assertEqual(out, params)
        self.assertIsNot(out, params)  # defensive copy

    def test_configure_node_routes_to_translator(self):
        node = {'id': 's1', 'name': 'Respond', 'type': 'n8n-nodes-base.respondToWebhook'}
        step = Step(
            id='s1', name='Respond',
            node_type='n8n-nodes-base.respondToWebhook',
            parameters={'respondWith': 'json', 'responseCode': 400},
        )
        out = _configure_node(node, step)
        self.assertEqual(out['parameters']['options']['responseCode'], 400)
        self.assertNotIn('responseCode', out['parameters'])


class TestPerBranchSequentialWiring(unittest.TestCase):
    """When a conditional_branch has its own terminal responder per branch
    (connected via sequential gates), each branch must chain to its own
    responder — not share a continuation."""

    def test_conditional_branch_with_per_branch_sequential_followups(self):
        # Mirrors the greeting-webhook gate graph:
        #   s1 → s2 (sequential)
        #   s2 (IF) → s3 pass / s5 fail (conditional_branch)
        #   s3 → s4 (sequential; s4 is respondToWebhook terminal)
        #   s5 → s6 (sequential; s6 is respondToWebhook terminal)
        spec = _make_spec(
            steps=[
                Step(id='s1', name='Extract', node_type='n8n-nodes-base.code'),
                Step(id='s2', name='Check', node_type='n8n-nodes-base.if'),
                Step(id='s3', name='Generate', node_type='n8n-nodes-base.code'),
                Step(id='s4', name='Ok', node_type='n8n-nodes-base.respondToWebhook'),
                Step(id='s5', name='BuildError', node_type='n8n-nodes-base.set'),
                Step(id='s6', name='Err', node_type='n8n-nodes-base.respondToWebhook'),
            ],
            gates=[
                Gate(after_step='s1', pass_to='s2', type='sequential'),
                Gate(after_step='s2', pass_to='s3', fail_to='s5', type='conditional_branch'),
                Gate(after_step='s3', pass_to='s4', type='sequential'),
                Gate(after_step='s5', pass_to='s6', type='sequential'),
            ],
        )
        nodes = [
            {'id': 'trigger', 'name': 'Webhook', 'type': 'n8n-nodes-base.webhook'},
            {'id': 's1', 'name': 'Extract', 'type': 'n8n-nodes-base.code'},
            {'id': 's2', 'name': 'Check', 'type': 'n8n-nodes-base.if'},
            {'id': 's3', 'name': 'Generate', 'type': 'n8n-nodes-base.code'},
            {'id': 's4', 'name': 'Ok', 'type': 'n8n-nodes-base.respondToWebhook'},
            {'id': 's5', 'name': 'BuildError', 'type': 'n8n-nodes-base.set'},
            {'id': 's6', 'name': 'Err', 'type': 'n8n-nodes-base.respondToWebhook'},
        ]

        conns = _build_connections(spec, nodes)

        # Trigger → s1
        self.assertEqual(conns['Webhook']['main'][0][0]['node'], 'Extract')
        # s1 → s2 (from sequential gate)
        self.assertEqual(conns['Extract']['main'][0][0]['node'], 'Check')
        # s2 (IF) → [s3, s5]
        self.assertEqual(conns['Check']['main'][0][0]['node'], 'Generate')
        self.assertEqual(conns['Check']['main'][1][0]['node'], 'BuildError')
        # Success branch: s3 → s4 (not s5)
        self.assertEqual(conns['Generate']['main'][0][0]['node'], 'Ok')
        # Error branch: s5 → s6 (NOT s4) — this is the bug that was fixed
        self.assertEqual(conns['BuildError']['main'][0][0]['node'], 'Err')
        # Terminals: no outbound connections
        self.assertNotIn('Ok', conns)
        self.assertNotIn('Err', conns)

    def test_sequential_gate_only_spec(self):
        # Pure linear chain expressed via sequential gates, no branching.
        spec = _make_spec(
            steps=[
                Step(id='s1', name='A', node_type='n8n-nodes-base.code'),
                Step(id='s2', name='B', node_type='n8n-nodes-base.code'),
                Step(id='s3', name='C', node_type='n8n-nodes-base.respondToWebhook'),
            ],
            gates=[
                Gate(after_step='s1', pass_to='s2', type='sequential'),
                Gate(after_step='s2', pass_to='s3', type='sequential'),
            ],
        )
        nodes = [
            {'id': 'trigger', 'name': 'Webhook', 'type': 'n8n-nodes-base.webhook'},
            {'id': 's1', 'name': 'A', 'type': 'n8n-nodes-base.code'},
            {'id': 's2', 'name': 'B', 'type': 'n8n-nodes-base.code'},
            {'id': 's3', 'name': 'C', 'type': 'n8n-nodes-base.respondToWebhook'},
        ]
        conns = _build_connections(spec, nodes)
        self.assertEqual(conns['A']['main'][0][0]['node'], 'B')
        self.assertEqual(conns['B']['main'][0][0]['node'], 'C')
        self.assertNotIn('C', conns)


class TestFindHelpers(unittest.TestCase):

    def test_find_by_name(self):
        nodes = [{'name': 'A'}, {'name': 'B'}]
        self.assertEqual(_find_node_by_name(nodes, 'B'), {'name': 'B'})

    def test_find_by_name_raises(self):
        with self.assertRaises(ValueError):
            _find_node_by_name([{'name': 'A'}], 'Z')

    def test_find_by_id(self):
        nodes = [{'id': 'x'}, {'id': 'y'}]
        self.assertEqual(_find_node_by_id(nodes, 'y'), {'id': 'y'})

    def test_find_by_id_raises(self):
        with self.assertRaises(ValueError):
            _find_node_by_id([{'id': 'x'}], 'z')


class TestWireFunction(unittest.TestCase):

    def test_wire_calls_update_workflow(self):
        spec = _make_spec()
        client = MagicMock()

        # Simulate what update_workflow does: call the modifier on a workflow dict
        def fake_update(wf_id, modifier):
            wf = {
                'nodes': [
                    {'id': 'trigger', 'name': 'Webhook', 'type': 'n8n-nodes-base.webhook', 'parameters': {}},
                    {'id': 's1', 'name': 'A', 'type': 'n8n-nodes-base.set', 'parameters': {}},
                ],
                'connections': {},
            }
            return modifier(wf)

        client.update_workflow.side_effect = fake_update

        result = wire(spec, client, 'wf-1')

        client.update_workflow.assert_called_once_with('wf-1', unittest.mock.ANY)
        # Should have connections now
        self.assertIn('Webhook', result['connections'])


if __name__ == '__main__':
    unittest.main()

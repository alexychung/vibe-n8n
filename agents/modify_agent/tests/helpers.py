"""Test helpers — workflow/node factories.

Lives outside conftest.py because pytest's conftest collection conflicts
with the project-root conftest at import time.
"""


def make_workflow(
    name: str = 'Test WF',
    nodes: list | None = None,
    connections: dict | None = None,
    settings: dict | None = None,
    active: bool = False,
) -> dict:
    return {
        'id': 'wf-test',
        'name': name,
        'active': active,
        'nodes': nodes if nodes is not None else [],
        'connections': connections if connections is not None else {},
        'settings': settings if settings is not None else {'executionTimeout': 300},
    }


def make_node(node_id: str, name: str, type_: str = 'n8n-nodes-base.set',
              parameters: dict | None = None, credentials: dict | None = None) -> dict:
    n = {
        'id': node_id,
        'name': name,
        'type': type_,
        'typeVersion': 1,
        'position': [250, 300],
        'parameters': parameters if parameters is not None else {},
    }
    if credentials is not None:
        n['credentials'] = credentials
    return n

"""Edit type definitions for the Modify Agent.

The Edit dataclass is the unit of work — planner produces a list, applier
consumes it, change_log persists it. Phase 1 supports six tactical edit
types (the 'TACTICAL_TYPES' tuple). Structural types are reserved for
Phase 2 of the rollout and currently raise NotImplementedError on apply.
"""
from dataclasses import dataclass, field
from typing import Any


# Phase 1 — tactical, no graph shape change
TACTICAL_TYPES = (
    'set_node_parameter',
    'rename_node',
    'set_node_setting',
    'update_credential_ref',
    'set_workflow_setting',
    'rename_workflow',
)

# Phase 2 — structural, change graph shape
STRUCTURAL_TYPES = (
    'add_node',
    'remove_node',
    'add_connection',
    'remove_connection',
)


@dataclass
class Edit:
    """One change to apply to a workflow.

    `type` selects which fields are required:
      set_node_parameter:  node_id, path, old_value, new_value
      rename_node:         node_id, old_name, new_name
      set_node_setting:    node_id, path (within node, e.g. 'retryOnFail'), old_value, new_value
      update_credential_ref: node_id, credential_type, old_value (cred id), new_value (cred id)
      set_workflow_setting: path (e.g. 'executionTimeout'), old_value, new_value
      rename_workflow:     old_value, new_value (workflow display name)
      add_node:            new_node (full n8n node dict), after_node_id (where to splice)
      remove_node:         node_id
      add_connection:      from_node_name, to_node_name, output_index (default 0)
      remove_connection:   from_node_name, to_node_name, output_index (default 0)

    Unused fields are left at their default. Validation happens in planner.
    """
    type: str
    node_id: str = ''
    path: str = ''
    old_value: Any = None
    new_value: Any = None
    old_name: str = ''
    new_name: str = ''
    credential_type: str = ''
    new_node: dict = field(default_factory=dict)
    after_node_id: str = ''
    from_node_name: str = ''
    to_node_name: str = ''
    output_index: int = 0

    def is_tactical(self) -> bool:
        return self.type in TACTICAL_TYPES

    def is_structural(self) -> bool:
        return self.type in STRUCTURAL_TYPES

    def to_dict(self) -> dict:
        """Serialize for the change log. Drops empty/default fields for readability."""
        d: dict[str, Any] = {'type': self.type}
        for f in ('node_id', 'path', 'old_name', 'new_name', 'credential_type',
                  'after_node_id', 'from_node_name', 'to_node_name'):
            v = getattr(self, f)
            if v:
                d[f] = v
        if self.old_value is not None:
            d['old_value'] = self.old_value
        if self.new_value is not None:
            d['new_value'] = self.new_value
        if self.new_node:
            d['new_node'] = self.new_node
        if self.output_index:
            d['output_index'] = self.output_index
        return d

    @classmethod
    def from_dict(cls, d: dict) -> 'Edit':
        """Deserialize. Used by --edits flag and rollback's manual mode."""
        return cls(
            type=d['type'],
            node_id=d.get('node_id', ''),
            path=d.get('path', ''),
            old_value=d.get('old_value'),
            new_value=d.get('new_value'),
            old_name=d.get('old_name', ''),
            new_name=d.get('new_name', ''),
            credential_type=d.get('credential_type', ''),
            new_node=d.get('new_node', {}),
            after_node_id=d.get('after_node_id', ''),
            from_node_name=d.get('from_node_name', ''),
            to_node_name=d.get('to_node_name', ''),
            output_index=d.get('output_index', 0),
        )

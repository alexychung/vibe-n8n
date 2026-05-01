"""Modify status tracker — phase-by-phase progress table.

Mirrors build_agent.status.BuildStatus but with the Modify Agent's phase set.
"""
from dataclasses import dataclass
from typing import Optional


PHASES = (
    'FETCH', 'CLASSIFY', 'PLAN', 'SNAPSHOT', 'APPLY',
    'TEST', 'AUDIT', 'HARDEN', 'DEPLOY', 'ROLLBACK',
)


@dataclass
class PhaseStatus:
    status: str = 'pending'  # pending | done | failed | skipped
    notes: str = ''


class ModifyStatus:
    def __init__(self, workflow_name: str, workflow_id: str):
        self.workflow_name = workflow_name
        self.workflow_id = workflow_id
        self._phases: dict[str, PhaseStatus] = {p: PhaseStatus() for p in PHASES}
        self.snapshot_path: Optional[str] = None

    def _set(self, phase: str, status: str, notes: str):
        if phase not in self._phases:
            raise ValueError(f'Unknown phase: {phase}. Must be one of {PHASES}')
        self._phases[phase] = PhaseStatus(status=status, notes=notes)

    def done(self, phase: str, notes: str = ''):
        self._set(phase, 'done', notes)

    def fail(self, phase: str, notes: str = ''):
        self._set(phase, 'failed', notes)

    def skip(self, phase: str, notes: str = ''):
        self._set(phase, 'skipped', notes)

    def render(self) -> str:
        lines = [
            f'## Modify Status: {self.workflow_name} (workflow_id: {self.workflow_id})',
            '',
            '| Phase | Status | Notes |',
            '|-------|--------|-------|',
        ]
        for phase, ps in self._phases.items():
            safe_notes = ps.notes.replace('|', '\\|').replace('\n', ' ')
            lines.append(f'| {phase} | {ps.status} | {safe_notes} |')
        if self.snapshot_path:
            lines.append('')
            lines.append(f'Snapshot: {self.snapshot_path}')
        return '\n'.join(lines)

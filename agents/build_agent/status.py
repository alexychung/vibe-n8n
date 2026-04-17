"""Build status tracker.

Tracks phase-by-phase progress of a workflow build.
Renders a markdown status table after each phase.
"""
from dataclasses import dataclass, field
from typing import Optional

PHASES = ('SCAFFOLD', 'WIRE', 'TEST', 'AUDIT', 'HARDEN', 'CODIFY', 'DEPLOY', 'EXPORT')


@dataclass
class PhaseStatus:
    status: str = 'pending'  # pending | done | failed | skipped
    notes: str = ''


class BuildStatus:
    """Tracks build progress across all phases."""

    def __init__(self, workflow_name: str):
        self.workflow_name = workflow_name
        self.workflow_id: Optional[str] = None
        self._phases: dict[str, PhaseStatus] = {p: PhaseStatus() for p in PHASES}

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

    def to_dict(self) -> dict[str, dict]:
        return {
            phase: {'status': ps.status, 'notes': ps.notes}
            for phase, ps in self._phases.items()
        }

    def render(self) -> str:
        lines = [f'## Build Status: {self.workflow_name}']
        if self.workflow_id:
            lines.append(f'Workflow ID: {self.workflow_id}')
        lines.append('')
        lines.append('| Phase | Status | Notes |')
        lines.append('|-------|--------|-------|')
        for phase, ps in self._phases.items():
            # Escape pipes and newlines so they don't break the markdown table
            safe_notes = ps.notes.replace('|', '\\|').replace('\n', ' ')
            lines.append(f'| {phase} | {ps.status} | {safe_notes} |')
        return '\n'.join(lines)

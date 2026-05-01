"""Phase 7 helper: audit-delta — only flag findings new since the snapshot.

Fingerprints findings by (severity, check, node_name) for node-level findings,
or (severity, check) for workflow-level. Pre-existing findings (default
node names, missing webhook auth, etc.) are not the modify's job to surface.

NEW CRITICALs always block, by spec — the orchestrator decides what to do
with the delta, but this module just returns it.
"""
import re
from dataclasses import dataclass
from typing import Optional

from auditor import Finding, audit_workflow


_NODE_NAME_RE = re.compile(r'^Node\s+"([^"]+)":')


def _fingerprint(f: Finding) -> tuple:
    """Stable identity for a finding across audits.

    Uses the auditor's `check` code (already stable per the spec). Extracts
    node name from messages of the form `Node "X": ...`. Workflow-level
    findings (no Node prefix) fingerprint without a node name — there's at
    most one of each per workflow so they don't collide.
    """
    name = _extract_node_name(f.message)
    if name is None:
        return (f.severity, f.check)
    return (f.severity, f.check, name)


def _extract_node_name(message: str) -> Optional[str]:
    m = _NODE_NAME_RE.match(message or '')
    return m.group(1) if m else None


@dataclass
class AuditDelta:
    new_findings: list[Finding]
    suppressed: list[Finding]  # findings present in both — surfaced for transparency

    @property
    def new_critical(self) -> int:
        return sum(1 for f in self.new_findings if f.severity == 'CRITICAL')

    @property
    def new_warning(self) -> int:
        return sum(1 for f in self.new_findings if f.severity == 'WARNING')

    @property
    def new_info(self) -> int:
        return sum(1 for f in self.new_findings if f.severity == 'INFO')


def audit_delta(snapshot_workflow: dict, modified_workflow: dict) -> AuditDelta:
    """Run audit on both workflows and return only the delta.

    A finding present in both is suppressed (not the modify's job).
    A finding present only in modified is new.
    """
    snap_findings = audit_workflow(snapshot_workflow)
    mod_findings = audit_workflow(modified_workflow)

    snap_fps = {_fingerprint(f) for f in snap_findings}

    new = [f for f in mod_findings if _fingerprint(f) not in snap_fps]
    suppressed = [f for f in mod_findings if _fingerprint(f) in snap_fps]

    return AuditDelta(new_findings=new, suppressed=suppressed)

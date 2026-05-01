"""Phase 4: SNAPSHOT — save the pre-change workflow JSON for rollback.

Snapshots live in build-logs/snapshots/<workflow_id>-<timestamp>.json.
At the start of every modify, retention runs: keep last 30 per workflow,
age out >90 days. Cheap (os.stat + delete) so it doesn't slow modifies.
"""
import datetime
import json
import os
import uuid
from dataclasses import dataclass


SNAPSHOT_KEEP_LAST = 30
SNAPSHOT_MAX_AGE_DAYS = 90


@dataclass
class Snapshot:
    workflow_id: str
    path: str
    timestamp: str  # ISO8601 UTC
    workflow: dict


def save_snapshot(
    workflow_id: str,
    workflow: dict,
    snapshot_dir: str,
) -> Snapshot:
    """Write a snapshot file and return its metadata.

    Snapshot filename uses a UTC timestamp. Directory is created if missing.
    """
    os.makedirs(snapshot_dir, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    # Second-precision timestamp + 4-char hex suffix avoids collisions when
    # two modifies fire in the same second (rapid retry, scripted runs).
    ts = now.strftime('%Y%m%d-%H%M%S')
    suffix = uuid.uuid4().hex[:4]
    path = os.path.join(snapshot_dir, f'{workflow_id}-{ts}-{suffix}.json')

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(workflow, f, indent=2, ensure_ascii=False)

    return Snapshot(
        workflow_id=workflow_id,
        path=path,
        timestamp=now.isoformat(),
        workflow=workflow,
    )


def load_snapshot(path: str) -> dict:
    """Read a snapshot file. Used by rollback and audit_diff."""
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def cleanup_snapshots(
    snapshot_dir: str,
    workflow_id: str,
    keep_last: int = SNAPSHOT_KEEP_LAST,
    max_age_days: int = SNAPSHOT_MAX_AGE_DAYS,
) -> list[str]:
    """Delete old snapshots for one workflow. Returns paths deleted.

    Two policies, both applied:
      - Drop everything older than max_age_days
      - After that, drop oldest until at most keep_last remain

    Other workflows' snapshots are untouched.
    """
    if not os.path.isdir(snapshot_dir):
        return []

    prefix = f'{workflow_id}-'
    entries = []
    try:
        for fname in os.listdir(snapshot_dir):
            if not fname.startswith(prefix) or not fname.endswith('.json'):
                continue
            full = os.path.join(snapshot_dir, fname)
            try:
                mtime = os.path.getmtime(full)
            except OSError:
                continue
            entries.append((mtime, full))
    except OSError:
        return []

    deleted: list[str] = []
    now = datetime.datetime.now().timestamp()
    cutoff = now - (max_age_days * 86400)

    # Drop by age
    survivors = []
    for mtime, path in entries:
        if mtime < cutoff:
            try:
                os.remove(path)
                deleted.append(path)
            except OSError:
                survivors.append((mtime, path))
        else:
            survivors.append((mtime, path))

    # Drop oldest beyond keep_last
    survivors.sort(key=lambda x: x[0])
    excess = max(0, len(survivors) - keep_last)
    for mtime, path in survivors[:excess]:
        try:
            os.remove(path)
            deleted.append(path)
        except OSError:
            pass

    return deleted

"""The git-like layer state machine.

A local QGIS layer is linked to a remote dataset through two persisted custom
properties: the dataset id and the content hash captured at the last push.
Comparing the current hash against the recorded one yields the familiar
git-status vocabulary shown in the dock.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class SyncState(str, Enum):
    UNTRACKED = "untracked"  # never pushed (no dataset id recorded)
    SYNCED = "synced"        # linked and content unchanged since the last push
    MODIFIED = "modified"    # linked but local content differs
    UNKNOWN = "unknown"      # linked but the hash could not be computed


def compute_sync_state(
    linked_dataset_id: Optional[str],
    last_pushed_hash: Optional[str],
    current_hash: Optional[str],
) -> SyncState:
    if not linked_dataset_id:
        return SyncState.UNTRACKED
    if not current_hash:
        return SyncState.UNKNOWN
    if not last_pushed_hash:
        # Linked but never hash-stamped (e.g. linked by a pull): treat as
        # modified so the user can push to establish a baseline.
        return SyncState.MODIFIED
    return SyncState.SYNCED if current_hash == last_pushed_hash else SyncState.MODIFIED

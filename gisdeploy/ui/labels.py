"""Display labels shared by the dock and the push dialog."""

from __future__ import annotations

from ..core.sync_state import SyncState
from ..translation import tr, tr_noop

STATE_LABELS = {
    SyncState.UNTRACKED: tr_noop("● untracked"),
    SyncState.MODIFIED: tr_noop("✚ modified"),
    SyncState.SYNCED: tr_noop("✔ synced"),
    SyncState.UNKNOWN: tr_noop("? unknown"),
}

# Dataset and revision statuses as the API reports them.
STATUS_LABELS = {
    "UPLOADED": tr_noop("Uploaded"),
    "PROCESSING": tr_noop("Processing"),
    "READY": tr_noop("Ready"),
    "FAILED": tr_noop("Failed"),
    "ACTIVE": tr_noop("Active"),
    "SUPERSEDED": tr_noop("Superseded"),
}

VISIBILITY_LABELS = {
    "PRIVATE": tr_noop("Private"),
    "PUBLIC": tr_noop("Public"),
}


def state_label(state: SyncState) -> str:
    label = STATE_LABELS.get(state)
    return tr(label) if label else state.value


def visibility_label(value: str) -> str:
    label = VISIBILITY_LABELS.get(value)
    return tr(label) if label else value


def status_label(value: str) -> str:
    label = STATUS_LABELS.get(value)
    return tr(label) if label else value

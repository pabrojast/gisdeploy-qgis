import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gisdeploy.core.sync_state import SyncState, compute_sync_state  # noqa: E402


def test_untracked_without_dataset_id():
    assert compute_sync_state(None, None, "abc") is SyncState.UNTRACKED
    assert compute_sync_state("", None, "abc") is SyncState.UNTRACKED


def test_synced_when_hashes_match():
    assert compute_sync_state("ds1", "h", "h") is SyncState.SYNCED


def test_modified_when_hashes_differ_or_baseline_missing():
    assert compute_sync_state("ds1", "h", "other") is SyncState.MODIFIED
    assert compute_sync_state("ds1", None, "h") is SyncState.MODIFIED


def test_unknown_when_current_hash_unavailable():
    assert compute_sync_state("ds1", "h", None) is SyncState.UNKNOWN

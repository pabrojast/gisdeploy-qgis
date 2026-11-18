import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gisdeploy.core.hashing import feature_hash, sha256_file  # noqa: E402


def test_feature_hash_is_stable_and_order_sensitive():
    features = [(b"\x01\x02", ["a", 1, None]), (b"\x03", [2.5])]
    assert feature_hash(features) == feature_hash(features)
    assert feature_hash(features) != feature_hash(list(reversed(features)))


def test_feature_hash_distinguishes_geometry_from_attributes():
    with_geometry = [(b"\x01", [])]
    without_geometry = [(None, ["\x01"])]
    assert feature_hash(with_geometry) != feature_hash(without_geometry)


def test_feature_hash_separates_adjacent_attributes():
    assert feature_hash([(None, ["ab", "c"])]) != feature_hash([(None, ["a", "bc"])])


def test_sha256_file(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"hello")
    assert sha256_file(str(path)) == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )

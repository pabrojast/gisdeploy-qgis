"""metadata.txt and package files as plugins.qgis.org validates them on upload."""

import configparser
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..", "gisdeploy")
REQUIRED = ("name", "qgisMinimumVersion", "description", "about", "version", "author", "email", "repository")


def _metadata():
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # QGIS keys are case-sensitive
    with open(os.path.join(ROOT, "metadata.txt"), encoding="utf-8") as handle:
        parser.read_file(handle)
    return parser["general"]


def _version(value):
    return tuple(int(part) for part in value.split("."))


def test_required_fields_present():
    general = _metadata()
    for key in REQUIRED + ("tracker", "homepage", "tags", "icon", "changelog"):
        assert general.get(key, "").strip(), key


def test_mandatory_files_exist():
    for name in ("__init__.py", "metadata.txt", "LICENSE"):
        assert os.path.isfile(os.path.join(ROOT, name)), name
    with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as handle:
        assert "GNU GENERAL PUBLIC LICENSE" in handle.read(200)
    assert os.path.isfile(os.path.join(ROOT, _metadata()["icon"]))


def test_qgis_3_and_4_compatibility_declared():
    general = _metadata()
    assert _version(general["qgisMinimumVersion"]) <= (3, 22)
    assert _version(general["qgisMaximumVersion"]) >= (4, 0)
    assert "supportsQt6" not in general  # dropped by QGIS; qgisMaximumVersion is what counts


def test_links_point_to_the_public_plugin_repository():
    general = _metadata()
    for key in ("repository", "tracker", "homepage"):
        assert re.match(r"^https://[^\s]+$", general[key]), key
    assert general["tracker"].startswith(general["repository"])
    assert "geodeploy" not in general["repository"]


def test_about_declares_the_external_paid_service():
    about = _metadata()["about"]
    assert "GISDeploy account" in about
    assert "paid plans" in about


def test_version_matches_latest_changelog_entry():
    general = _metadata()
    assert re.fullmatch(r"\d+\.\d+\.\d+", general["version"])
    assert general["changelog"].strip().splitlines()[0].strip() == general["version"]

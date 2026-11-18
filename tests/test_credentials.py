import base64
import io
import json
import os
import sys
import urllib.error
import zipfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from gisdeploy.core.api_client import ApiError, GisDeployClient
from gisdeploy.core.security import clean_url, safe_message
from gisdeploy.core.transport import NoRedirect, TransportError, TransportResponse, UrllibTransport
from gisdeploy.qgis_io.auth import AuthenticationError, connection_token, save_connection, saved_connection
from gisdeploy.qgis_io.wms import scrub_legacy_uri, wms_uri

TOKEN = "agt_fixture_secret_not_a_real_credential"

class Settings:
    def __init__(self): self.data = {"gisdeploy/token": TOKEN}
    def value(self, key, default="", type=str): return self.data.get(key, default)
    def setValue(self, key, value): self.data[key] = value
    def remove(self, key): self.data.pop(key, None)
    def sync(self): pass

class Config:
    def __init__(self): self.values = {}; self.identifier = ""; self.method_name = ""
    def setName(self, value): self.name = value
    def setMethod(self, value): self.method_name = value
    def method(self): return self.method_name
    def setUri(self, value): self.uri = value
    def setConfig(self, key, value): self.values[key] = value
    def config(self, key): return self.values.get(key, "")
    def id(self): return self.identifier

class Manager:
    def __init__(self, unlocked=True, success=True): self.unlocked = unlocked; self.success = success; self.saved = None
    def isDisabled(self): return False
    def masterPasswordIsSet(self): return self.unlocked
    def setMasterPassword(self, verify): return False
    def storeAuthenticationConfig(self, config):
        config.identifier = "abc1234"
        self.saved = config
        return self.success, config
    def loadAuthenticationConfig(self, authcfg, config, full): return True, self.saved


def test_authcfg_is_the_only_credential_reference_in_settings_and_project():
    settings, manager = Settings(), Manager()
    authcfg = save_connection("https://gisdeploy.test", TOKEN, manager, Config, settings)
    assert manager.saved.method() == "Basic"
    assert manager.saved.config("password") == TOKEN  # handed only to encrypted QGIS auth manager
    assert connection_token(authcfg, manager, Config) == TOKEN
    assert settings.data == {"gisdeploy/baseUrl": "https://gisdeploy.test", "gisdeploy/authcfg": "abc1234"}
    uri = wms_uri("https://ogc.test/ogc/own/wms", "layer-one", authcfg)
    assert "authcfg=abc1234" in uri
    assert "password" not in uri and "username" not in uri
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as project:
        project.writestr("fixture.qgs", f"<qgis><datasource>{uri}</datasource></qgis>")
    with zipfile.ZipFile(archive) as project:
        assert all(TOKEN.encode() not in project.read(name) for name in project.namelist())
    assert TOKEN not in json.dumps(settings.data)
    assert TOKEN not in repr(GisDeployClient("https://gisdeploy.test", TOKEN))


def test_legacy_settings_are_deleted_and_locked_store_has_no_plaintext_fallback():
    settings = Settings()
    assert saved_connection(settings) == ("https://gisdeploy.com", "")
    assert "gisdeploy/token" not in settings.data
    with pytest.raises(AuthenticationError):
        save_connection("https://gisdeploy.test", TOKEN, Manager(unlocked=False), Config, settings)
    assert TOKEN not in json.dumps(settings.data)
    with pytest.raises(AuthenticationError):
        save_connection("https://gisdeploy.test", TOKEN, Manager(success=False), Config, settings)
    assert TOKEN not in json.dumps(settings.data)


def test_provider_uri_never_accepts_a_raw_token_or_credential_bearing_endpoint():
    with pytest.raises(ValueError): wms_uri("https://ogc.test/ogc/own/wms", "layer", TOKEN)
    for url in [f"https://token:{TOKEN}@ogc.test/ogc/own/wms", f"https://ogc.test/ogc/own/wms?token={TOKEN}", "http://ogc.test/ogc/own/wms"]:
        with pytest.raises(ValueError): wms_uri(url, "layer", "abc1234")
    anonymous = wms_uri("https://ogc.test/ogc/own/wms", "layer-one")
    assert "LAYERS%3Dds_layer_one" in anonymous


def test_maps_are_requested_from_the_given_endpoint_not_advertised_urls():
    # Full-access capabilities can advertise the internal GeoServer address.
    for uri in (wms_uri("https://ogc.test/ogc/own/wms", "layer-one", "abc1234"), wms_uri("https://ogc.test/ogc/own/wms", "layer-one")):
        assert "IgnoreGetMapUrl=1" in uri.split("&") and "IgnoreGetFeatureInfoUrl=1" in uri.split("&")
    kept = scrub_legacy_uri("IgnoreGetMapUrl=1&layers=ds_layer&url=https%3A%2F%2Fogc.test%2Fogc%2Fown%2Fwms&password=x")
    assert "IgnoreGetMapUrl=1" in kept and "password" not in kept


def test_symbols_are_not_cropped_to_the_reported_layer_extent():
    # The advertised extent is the data's exact bounds: cropping to it cuts edge symbols in half.
    for uri in (wms_uri("https://ogc.test/ogc/own/wms", "layer-one", "abc1234"), wms_uri("https://ogc.test/ogc/own/wms", "layer-one")):
        assert "IgnoreReportedLayerExtents=1" in uri.split("&")
    kept = scrub_legacy_uri("IgnoreReportedLayerExtents=1&layers=ds_layer&url=https%3A%2F%2Fogc.test%2Fogc%2Fown%2Fwms")
    assert "IgnoreReportedLayerExtents=1" in kept


def test_legacy_project_sources_are_scrubbed_without_copying_the_old_token():
    source = f"layers=ds_layer&url=https%3A%2F%2Fogc.test%2Fogc%2Fown%2Fwms&username=token&password={TOKEN}"
    cleaned = scrub_legacy_uri(source)
    assert TOKEN not in cleaned and "password" not in cleaned and "username" not in cleaned
    assert "layers=ds_layer" in cleaned
    assert scrub_legacy_uri("url=https%3A%2F%2Funrelated.test%2Fwms&password=other") is None


def test_response_errors_and_transport_diagnostics_never_expose_token_or_basic_header():
    client = GisDeployClient("https://gisdeploy.test", TOKEN)
    basic = base64.b64encode(f"token:{TOKEN}".encode()).decode()
    response = TransportResponse(403, json.dumps({"ok": False, "error": {"message": f"Bearer {TOKEN}; Basic {basic}", "suggestedActions": [TOKEN]}}).encode())
    with pytest.raises(ApiError) as error: client._unwrap(response)
    assert TOKEN not in str(error.value) and basic not in str(error.value)
    assert TOKEN not in repr(error.value.suggested_actions)
    with patch("urllib.request.build_opener") as opener:
        opener.return_value.open.side_effect = urllib.error.URLError(f"secret: {TOKEN}")
        with pytest.raises(TransportError) as error:
            UrllibTransport().request("GET", "https://gisdeploy.test")
        assert TOKEN not in str(error.value)
        assert error.value.__suppress_context__
    assert TOKEN not in safe_message(f"task failed with {TOKEN}")


def test_transport_identifies_the_plugin_instead_of_python_urllib():
    # Cloudflare in front of gisdeploy.com answers 403 (error 1010) to "Python-urllib/3.x".
    import configparser
    from gisdeploy.core.transport import USER_AGENT
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(os.path.join(os.path.dirname(__file__), "..", "gisdeploy", "metadata.txt"), encoding="utf-8")
    assert USER_AGENT.startswith(f"GISDeploy-QGIS/{parser['general']['version']} ")
    with patch("urllib.request.build_opener") as opener:
        opener.return_value.open.return_value.__enter__.return_value.status = 200
        UrllibTransport().request("GET", "https://gisdeploy.test", headers={"Authorization": "Bearer x"})
        sent = opener.return_value.open.call_args[0][0]
    assert sent.get_header("User-agent") == USER_AGENT
    assert "Python-urllib" not in sent.get_header("User-agent")
    assert sent.get_header("Authorization") == "Bearer x"


def test_api_bearer_is_only_a_header_and_redirects_never_forward_it():
    class RecordingTransport:
        def request(self, method, url, headers, timeout):
            assert TOKEN not in url
            assert headers["Authorization"] == f"Bearer {TOKEN}"
            return TransportResponse(200, b'{"ok":true,"data":{}}')
    GisDeployClient("https://gisdeploy.test", TOKEN, transport=RecordingTransport()).context()
    assert NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.test") is None
    for url in [f"https://gisdeploy.test/?token={TOKEN}", f"https://token:{TOKEN}@gisdeploy.test", "http://gisdeploy.test"]:
        with pytest.raises(ValueError): clean_url(url)


def test_worker_failures_do_not_print_sensitive_tracebacks(capsys):
    import importlib
    import types
    class Task:
        Flag = types.SimpleNamespace(CanCancel=1)
        def __init__(self, *args): pass
    captured = []
    def fail(report): raise RuntimeError(f"Transport failed with {TOKEN}")
    qt_core = types.SimpleNamespace(
        QCoreApplication=types.SimpleNamespace(translate=lambda _context, text: text),
        QLocale=None, QTranslator=None,
    )
    fakes = {
        "qgis": types.ModuleType("qgis"),
        "qgis.core": types.SimpleNamespace(QgsApplication=None, QgsTask=Task),
        "qgis.PyQt": types.ModuleType("qgis.PyQt"),
        "qgis.PyQt.QtCore": qt_core,
    }
    with patch.dict(sys.modules, fakes):
        tasks = importlib.import_module("gisdeploy.qgis_io.tasks")
        task = tasks.WorkerTask("fixture", fail, lambda result, error: captured.append(str(error)))
        assert task.run() is False
        task.finished(False)
        assert TOKEN not in captured[0]
        assert capsys.readouterr().err == ""
    sys.modules.pop("gisdeploy.qgis_io.tasks", None)
    sys.modules.pop("gisdeploy.translation", None)

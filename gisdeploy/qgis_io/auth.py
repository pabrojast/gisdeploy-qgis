"""QGIS encrypted authentication storage; QgsSettings holds references only."""
from __future__ import annotations

import re

from ..core.security import clean_url, validate_token

SETTINGS_URL = "gisdeploy/baseUrl"
SETTINGS_AUTHCFG = "gisdeploy/authcfg"
LEGACY_TOKEN = "gisdeploy/token"
DEFAULT_URL = "https://gisdeploy.com"


class AuthenticationError(Exception):
    pass


def _qgis():
    from qgis.core import QgsApplication, QgsAuthMethodConfig, QgsSettings
    return QgsApplication.authManager(), QgsAuthMethodConfig, QgsSettings()


def saved_connection(settings=None):
    if settings is None:
        settings = _qgis()[2]
    # Previously persisted plaintext may already have been copied to projects or
    # backups. Remove it; reconnect with a newly issued token instead of reviving it.
    settings.remove(LEGACY_TOKEN)
    settings.sync()
    try:
        url = clean_url(settings.value(SETTINGS_URL, DEFAULT_URL, type=str))
    except ValueError:
        settings.remove(SETTINGS_URL)
        settings.sync()
        url = DEFAULT_URL
    authcfg = settings.value(SETTINGS_AUTHCFG, "", type=str)
    if authcfg and not re.fullmatch(r"[A-Za-z0-9]{7}", authcfg):
        settings.remove(SETTINGS_AUTHCFG)
        settings.sync()
        authcfg = ""
    return url, authcfg


def _unlock(manager):
    if manager.isDisabled() or (not manager.masterPasswordIsSet() and not manager.setMasterPassword(True)):
        raise AuthenticationError("Unlock the QGIS authentication database to continue.")


def _result(result, config):
    # SIP versions expose in/out QgsAuthMethodConfig as a tuple or mutate it.
    if isinstance(result, tuple):
        return result[0], result[1]
    return bool(result), config


def save_connection(url, token, manager=None, config_factory=None, settings=None):
    url, token = clean_url(url), validate_token(token)
    if manager is None:
        manager, config_factory, settings = _qgis()
    _unlock(manager)
    config = config_factory()
    config.setName("GISDeploy")
    config.setMethod("Basic")
    config.setUri(url)
    config.setConfig("username", "token")
    config.setConfig("password", token)
    ok, config = _result(manager.storeAuthenticationConfig(config), config)
    if not ok or not config.id():
        raise AuthenticationError("Could not save credentials in the QGIS authentication database.")
    # Existing authcfg entries are kept because already-saved projects may use
    # them. Rotation/revocation is performed in GISDeploy and QGIS Auth settings.
    settings.setValue(SETTINGS_URL, url)
    settings.setValue(SETTINGS_AUTHCFG, config.id())
    settings.remove(LEGACY_TOKEN)
    settings.sync()
    return config.id()


def connection_token(authcfg, manager=None, config_factory=None):
    if not isinstance(authcfg, str) or not re.fullmatch(r"[A-Za-z0-9]{7}", authcfg):
        raise AuthenticationError("A valid QGIS authentication configuration is required.")
    if manager is None:
        manager, config_factory, _settings = _qgis()
    _unlock(manager)
    config = config_factory()
    ok, config = _result(manager.loadAuthenticationConfig(authcfg, config, True), config)
    if not ok or config.method() != "Basic" or config.config("username") != "token":
        raise AuthenticationError("Reconnect to GISDeploy with a valid authentication configuration.")
    return validate_token(config.config("password"))

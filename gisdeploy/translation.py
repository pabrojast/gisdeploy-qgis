"""UI translations: loads i18n/gisdeploy_<lang>.qm for the QGIS user locale.

Widgets use ``self.tr()`` (context = their class name). Code outside QObjects
uses :func:`tr` (context ``GisDeploy``). Fixed exception messages raised by the
plugin are translated where they are shown, through :func:`error_text`.
``scripts/update_translations.py`` extracts all three kinds into the .ts files.
"""

from __future__ import annotations

import os
from typing import Optional

from qgis.PyQt.QtCore import QCoreApplication, QLocale, QTranslator

from .core.security import safe_message

CONTEXT = "GisDeploy"
I18N_DIR = os.path.join(os.path.dirname(__file__), "i18n")


def tr(text: str) -> str:
    return QCoreApplication.translate(CONTEXT, text)


def tr_noop(text: str) -> str:
    """Mark a string for extraction; translate it where it is shown with tr()."""
    return text


def error_text(error) -> str:
    """A redacted error message, translated when it is one of the plugin's own."""
    return tr(safe_message(error))


def user_language() -> str:
    from qgis.core import QgsSettings

    value = QgsSettings().value("locale/userLocale", "") or QLocale().name()
    return str(value)[:2].lower()


def install_translator(language: Optional[str] = None) -> Optional[QTranslator]:
    """Install the translator for `language` (default: the QGIS locale), if shipped."""
    language = (language or user_language()).lower()
    if not language.isalpha():
        return None
    path = os.path.join(I18N_DIR, f"gisdeploy_{language}.qm")
    if not os.path.isfile(path):
        return None
    translator = QTranslator()
    if not translator.load(path):
        return None
    QCoreApplication.installTranslator(translator)
    return translator


def remove_translator(translator: Optional[QTranslator]) -> None:
    if translator is not None:
        QCoreApplication.removeTranslator(translator)

import os
import sys
import textwrap

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import update_translations  # noqa: E402


def test_spanish_ts_is_current_and_complete(capsys):
    assert update_translations.main(["--check"]) == 0, capsys.readouterr().out


def test_extractor_rules(tmp_path):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "mod.py").write_text(textwrap.dedent('''
        from .translation import tr, tr_noop

        LABEL = tr_noop("Marked label")

        class Dock:
            def build(self):
                self.tr("Dock text")
                def work():
                    return self.tr("Nested {0}").format(1)
                raise ValueError(self.tr("Already translated"))

        def helper(status, value):
            tr("Module text")
            tr(value)
            self_tr = f"not {value}"
            raise RuntimeError(status or "Fallback message")

        def other(code):
            raise KeyError("internal", f"Dynamic {code}")
            raise ValueError("Plain message")
    '''), encoding="utf-8")
    assert update_translations.extract(package) == [
        ("GisDeploy", "Marked label"),
        ("Dock", "Dock text"),
        ("Dock", "Nested {0}"),
        ("Dock", "Already translated"),
        ("GisDeploy", "Module text"),
        ("GisDeploy", "Fallback message"),
        ("GisDeploy", "Plain message"),
    ]


def test_check_reports_missing_translations_and_placeholder_drift():
    messages = [("Dock", "Uploading {0}…"), ("Dock", "Refresh")]
    translations = {("Dock", "Uploading {0}…"): "Subiendo…"}
    assert update_translations.problems(messages, translations) == [
        "placeholders differ [Dock] 'Uploading {0}…' -> 'Subiendo…'",
        "untranslated [Dock] 'Refresh'",
    ]


def test_rendered_ts_round_trips_translations(tmp_path):
    messages = [("Dock", "Test && Save"), ("GisDeploy", "● untracked")]
    translations = {("Dock", "Test && Save"): "Probar y guardar"}
    rendered = update_translations.render_ts("es", messages, translations)
    path = tmp_path / "roundtrip.ts"
    path.write_text(rendered, encoding="utf-8")
    assert update_translations.read_ts(path) == {
        ("Dock", "Test && Save"): "Probar y guardar",
        ("GisDeploy", "● untracked"): "",
    }
    assert 'type="unfinished"' in rendered


def test_compiled_spanish_translation_loads():
    qt_core = pytest.importorskip("qgis.PyQt.QtCore")
    app = qt_core.QCoreApplication.instance() or qt_core.QCoreApplication([])
    translator = qt_core.QTranslator()
    assert translator.load(os.path.join(ROOT, "gisdeploy", "i18n", "gisdeploy_es.qm"))

    class ConnectDialog(qt_core.QObject):  # self.tr() uses the class name as context
        def texts(self):
            return self.tr("Server URL"), self.tr("Testing connection…")

    qt_core.QCoreApplication.installTranslator(translator)
    try:
        translate = qt_core.QCoreApplication.translate
        assert ConnectDialog().texts() == ("URL del servidor", "Probando la conexión…")
        assert translate("GisDeploy", "● untracked") == "● sin subir"
        assert translate("GisDeploy", "A valid HTTPS server URL is required.") == (
            "Se requiere una URL de servidor HTTPS válida."
        )
    finally:
        qt_core.QCoreApplication.removeTranslator(translator)
    assert app is not None

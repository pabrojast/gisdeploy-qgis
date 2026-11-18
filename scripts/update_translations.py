#!/usr/bin/env python3
"""Extract the plugin's UI strings into gisdeploy/i18n/gisdeploy_<lang>.ts.

Stdlib-only replacement for pylupdate. It collects:
  * ``self.tr("…")`` calls, with the enclosing class name as context (as PyQt does);
  * ``tr("…")`` and ``tr_noop("…")`` calls, with the ``GisDeploy`` context;
  * the message of ``raise SomeError("…")`` (last positional argument, when it is a
    literal or ``value or "literal"``), with the ``GisDeploy`` context, because
    ``translation.error_text`` translates those where they are shown.

Existing translations are kept; new strings are added as unfinished and strings no
longer in the code are dropped.

Usage:
  python3 scripts/update_translations.py            # update every .ts, compile .qm with lrelease
  python3 scripts/update_translations.py --check    # exit 1 if a .ts is stale or incomplete
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "gisdeploy"
I18N = PACKAGE / "i18n"
LANGUAGES = ("es",)
GLOBAL_CONTEXT = "GisDeploy"
PLACEHOLDER = re.compile(r"\{\d+\}")

Message = Tuple[str, str]  # (context, source)


def _literals(node: ast.AST) -> List[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.BoolOp):
        return [v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
    return []


class _Collector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.classes: List[str] = []
        self.found: List[Message] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.classes.append(node.name)
        self.generic_visit(node)
        self.classes.pop()

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if node.args:
            first = _literals(node.args[0]) if isinstance(node.args[0], ast.Constant) else []
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "tr"
                and isinstance(func.value, ast.Name)
                and func.value.id == "self"
                and self.classes
            ):
                self.found += [(self.classes[-1], text) for text in first]
            elif isinstance(func, ast.Name) and func.id in ("tr", "tr_noop"):
                self.found += [(GLOBAL_CONTEXT, text) for text in first]
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        if isinstance(node.exc, ast.Call) and node.exc.args:
            self.found += [(GLOBAL_CONTEXT, text) for text in _literals(node.exc.args[-1])]
        self.generic_visit(node)


def extract(package: Path = PACKAGE) -> List[Message]:
    """Unique messages in source order (files sorted by path)."""
    seen: Dict[Message, None] = {}
    for path in sorted(package.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        collector = _Collector()
        collector.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for message in collector.found:
            if message[1].strip():
                seen.setdefault(message, None)
    return list(seen)


def read_ts(path: Path) -> Dict[Message, str]:
    if not path.exists():
        return {}
    translations: Dict[Message, str] = {}
    for context in ET.parse(path).getroot().iter("context"):
        name = context.findtext("name") or ""
        for message in context.iter("message"):
            translation = message.find("translation")
            text = translation.text if translation is not None and translation.text else ""
            translations[(name, message.findtext("source") or "")] = text
    return translations


def render_ts(language: str, messages: Iterable[Message], translations: Dict[Message, str]) -> str:
    root = ET.Element("TS", version="2.1", language=language)
    by_context: Dict[str, List[str]] = {}
    for context, source in messages:
        by_context.setdefault(context, []).append(source)
    for context in sorted(by_context):
        node = ET.SubElement(root, "context")
        ET.SubElement(node, "name").text = context
        for source in by_context[context]:
            message = ET.SubElement(node, "message")
            ET.SubElement(message, "source").text = source
            translated = translations.get((context, source), "")
            element = ET.SubElement(message, "translation")
            if translated:
                element.text = translated
            else:
                element.set("type", "unfinished")
    ET.indent(root, space="    ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS>\n' + body + "\n"


def problems(messages: List[Message], translations: Dict[Message, str]) -> List[str]:
    found = []
    for context, source in messages:
        translated = translations.get((context, source), "")
        if not translated:
            found.append(f"untranslated [{context}] {source!r}")
        elif sorted(PLACEHOLDER.findall(source)) != sorted(PLACEHOLDER.findall(translated)):
            found.append(f"placeholders differ [{context}] {source!r} -> {translated!r}")
    return found


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if a .ts file is stale or incomplete")
    args = parser.parse_args(argv)

    messages = extract()
    status = 0
    for language in LANGUAGES:
        ts_path = I18N / f"gisdeploy_{language}.ts"
        translations = read_ts(ts_path)
        rendered = render_ts(language, messages, translations)
        issues = problems(messages, translations)
        if args.check:
            current = ts_path.read_text(encoding="utf-8") if ts_path.exists() else ""
            if current != rendered:
                print(f"{ts_path.relative_to(ROOT)} is out of date: run scripts/update_translations.py")
                status = 1
            for issue in issues:
                print(f"{ts_path.relative_to(ROOT)}: {issue}")
                status = 1
            continue
        I18N.mkdir(exist_ok=True)
        ts_path.write_text(rendered, encoding="utf-8")
        print(f"{ts_path.relative_to(ROOT)}: {len(messages)} strings, {len(issues)} pending")
        lrelease = shutil.which("lrelease") or shutil.which("lrelease-qt5") or shutil.which("lrelease-qt6")
        if lrelease is None:
            print("lrelease not found: install the Qt linguist tools to compile the .qm file", file=sys.stderr)
            status = 1
            continue
        qm_path = ts_path.with_suffix(".qm")
        subprocess.run([lrelease, "-silent", str(ts_path), "-qm", str(qm_path)], check=True)
        print(f"{qm_path.relative_to(ROOT)} compiled")
    return status


if __name__ == "__main__":
    sys.exit(main())

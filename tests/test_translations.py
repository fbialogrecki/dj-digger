import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QTranslator  # noqa: E402

from dj_digger.gui.bridge import LANGUAGES  # noqa: E402

FOLDER = Path('dj_digger/gui/translations')
PLACEHOLDERS = re.compile(r'%n|%\d|\{\d*\}|<[^>]+>')


def messages(code):
    return [(context.findtext('name'), message.findtext('source'), message.find('translation'))
            for context in ET.parse(FOLDER / f'{code}.ts').getroot().iter('context')
            for message in context.iter('message')]


@pytest.mark.parametrize('code', LANGUAGES)
def test_catalog_mirrors_polish_and_is_complete(code):
    entries = messages(code)
    assert [(context, source) for context, source, _ in entries] == \
        [(context, source) for context, source, _ in messages('pl')]
    for _, source, translation in entries:
        assert translation.text and translation.get('type') is None, source
        assert sorted(PLACEHOLDERS.findall(translation.text)) == sorted(PLACEHOLDERS.findall(source)), source


@pytest.mark.parametrize('code', LANGUAGES)
def test_compiled_catalog_loads(code):
    translator = QTranslator()
    assert translator.load(str(FOLDER / f'{code}.qm'))
    assert translator.translate('Main', 'Cancel') not in ('', 'Cancel')

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QTranslator  # noqa: E402

from dj_digger.gui.bridge import LANGUAGES  # noqa: E402
from dj_digger.gui.model import HEADERS, STATUS_LABELS  # noqa: E402

FOLDER = Path('dj_digger/gui/translations')
PLACEHOLDERS = re.compile(r'%n|%\d|\{\d*\}|<[^>]+>')
# Qt's plural rules: Polish and Russian have one / few / many, the others one / other.
PLURAL_FORMS = {'pl': 3, 'ru': 3}
QS_TR = re.compile(r'qsTr\(\s*"((?:[^"\\]|\\.)*)"')


def messages(code):
    return [(context.findtext('name'), message.findtext('source'), message.find('translation'), message.get('numerus') == 'yes')
            for context in ET.parse(FOLDER / f'{code}.ts').getroot().iter('context')
            for message in context.iter('message')]


@pytest.mark.parametrize('code', LANGUAGES)
def test_catalog_mirrors_polish_and_is_complete(code):
    entries = messages(code)
    assert [(context, source, numerus) for context, source, _, numerus in entries] == \
        [(context, source, numerus) for context, source, _, numerus in messages('pl')]
    for _, source, translation, numerus in entries:
        assert translation.get('type') is None, source
        texts = [form.text for form in translation.findall('numerusform')] if numerus else [translation.text]
        if numerus:
            assert len(texts) == PLURAL_FORMS.get(code, 2), source
        for text in texts:
            assert text, source
            assert sorted(PLACEHOLDERS.findall(text)) == sorted(PLACEHOLDERS.findall(source)), source


def test_every_qml_string_is_in_the_catalog():
    """A qsTr literal added to Main.qml without a catalog entry would show in English."""
    qml = Path('dj_digger/gui/qml/Main.qml').read_text(encoding='utf-8')
    used = {re.sub(r'\\(.)', r'\1', text) for text in QS_TR.findall(qml)}
    catalog = {source for context, source, _, _ in messages('pl') if context == 'Main'}
    assert used - catalog == set()
    assert catalog - used == set(), 'obsolete Main entries'
    model = {source for context, source, _, _ in messages('pl') if context == 'TrackModel'}
    assert {*HEADERS, *STATUS_LABELS.values(), '✗ Failed'} <= model


@pytest.mark.parametrize('code', LANGUAGES)
def test_compiled_catalog_loads(code):
    translator = QTranslator()
    assert translator.load(str(FOLDER / f'{code}.qm'))
    assert translator.translate('Main', 'Cancel') not in ('', 'Cancel')
    assert translator.translate('Main', '%n tracks', '', 5) not in ('', '%n tracks')

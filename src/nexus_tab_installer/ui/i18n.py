"""The language of the screen.

The program is written in Russian, and English or Ukrainian is laid over it right where text reaches the screen.
So messages from any part of the program get translated too, even those built from pieces, and the
logic works with one set of texts no matter what the person reads. What has no translation stays in
Russian rather than disappearing, and tests/test_language_v450.py finds such texts before they ship.
"""
from __future__ import annotations

import ctypes
import os
import re
from functools import lru_cache

from nexus_tab_installer.ui.lang_en import EN
from nexus_tab_installer.ui.lang_uk import UK

LANGUAGES = ('ru', 'en', 'uk')
_CYRILLIC = re.compile('[А-Яа-яЁё]')
_LETTER = 'А-Яа-яЁёІіЇїЄєҐґ'
_RUSSIAN_MONTHS = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря')
_DATE = re.compile(rf'(\d{{1,2}}) ({"|".join(_RUSSIAN_MONTHS)}) (\d{{4}})')
_SIZE = re.compile(r'(\d+)(?:,(\d+))? (КБ|МБ|ГБ)')
_STRONG_PATTERN = 12  # fixed letters that make a pattern with holes specific enough
_BETWEEN = re.compile(r'(\n+|\s+·\s+|;\s+|(?<=[.!?…])\s+)')


def _counted(forms):
    """A number and the word after it, which plural() of views.py writes in Russian."""
    return re.compile(rf'(\d+) ({"|".join(sorted(map(re.escape, forms), key=len, reverse=True))})(?![{_LETTER}])')


# ---------------------------------------------------------------- English
_EN_MONTHS = dict(zip(_RUSSIAN_MONTHS, ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
                                        'October', 'November', 'December')))
_EN_UNITS = {'КБ': 'KB', 'МБ': 'MB', 'ГБ': 'GB'}
_EN_COUNTED = {'файл ждёт': ('file is waiting', 'files are waiting'), 'файла ждут': ('file is waiting', 'files are waiting'),
               'файлов ждут': ('file is waiting', 'files are waiting'), 'файл': ('file', 'files'), 'файла': ('file', 'files'),
               'файлов': ('file', 'files'), 'сессия': ('session', 'sessions'), 'сессии': ('session', 'sessions'),
               'сессий': ('session', 'sessions'), 'мод': ('mod', 'mods'), 'мода': ('mod', 'mods'), 'модов': ('mod', 'mods')}
_EN_COUNT = _counted(_EN_COUNTED)
_ONE_OF_MANY = re.compile(r'\b1 (mod|file|session)s\b')


def _finish_en(text):
    text = _DATE.sub(lambda m: f'{_EN_MONTHS[m[2]]} {int(m[1])}, {m[3]}', text)
    text = _SIZE.sub(lambda m: f"{m[1]}{'.' + m[2] if m[2] else ''} {_EN_UNITS[m[3]]}", text)
    text = _EN_COUNT.sub(lambda m: f'{m[1]} {_EN_COUNTED[m[2]][0 if m[1] == "1" else 1]}', text)
    return _ONE_OF_MANY.sub(lambda m: f'1 {m[1]}', text)


# ---------------------------------------------------------------- Ukrainian: 1 мод, 2 моди, 5 модів, as in Russian
_UK_MONTHS = dict(zip(_RUSSIAN_MONTHS, ('січня', 'лютого', 'березня', 'квітня', 'травня', 'червня', 'липня', 'серпня', 'вересня',
                                        'жовтня', 'листопада', 'грудня')))
_UK_WORDS = (('файл', 'файли', 'файлів'), ('мод', 'моди', 'модів'), ('сесія', 'сесії', 'сесій'),
             ('файл чекає', 'файли чекають', 'файлів чекають'))
_UK_RUSSIAN = {'файл': 0, 'файла': 0, 'файлов': 0, 'мод': 1, 'мода': 1, 'модов': 1, 'сессия': 2, 'сессии': 2, 'сессий': 2,
               'файл ждёт': 3, 'файла ждут': 3, 'файлов ждут': 3}
_UK_FORMS = {**_UK_RUSSIAN, **{form: index for index, forms in enumerate(_UK_WORDS) for form in forms}}
_UK_COUNT = re.compile(rf'(?:([{_LETTER}]+) )?(\d+) ({"|".join(sorted(map(re.escape, _UK_FORMS), key=len, reverse=True))})(?![{_LETTER}])')
# After these words the count takes the genitive whatever the number: "з 3 файлів", "не більше 2 файлів".
_UK_GENITIVE = {'з', 'із', 'зі', 'більше', 'менше', 'після', 'до', 'понад'}


def _slavic_form(number):
    tens, units = number % 100, number % 10
    return 2 if 11 <= tens <= 14 else 0 if units == 1 else 1 if 2 <= units <= 4 else 2


def _finish_uk(text):
    text = _DATE.sub(lambda m: f'{int(m[1])} {_UK_MONTHS[m[2]]} {m[3]}', text)
    def agree(m):
        before = f'{m[1]} ' if m[1] else ''
        forms = _UK_WORDS[_UK_FORMS[m[3]]]
        if m[1] and m[1].casefold() in _UK_GENITIVE:
            return f'{before}{m[2]} {forms[2] if m[2] != "1" else forms[0]}'
        return f'{before}{m[2]} {forms[_slavic_form(int(m[2]))]}'
    return _UK_COUNT.sub(agree, text)


class _Catalog:
    def __init__(self, entries, finish):
        exact, holed = {}, []
        for source, target in entries.items():
            source, target = source.strip(), target.strip()
            if '{}' in source:
                parts = source.split('{}')
                holed.append((len(''.join(parts)), re.compile('(.*?)'.join(map(re.escape, parts))), target))
            else:
                exact[source] = target
        holed.sort(key=lambda row: row[0], reverse=True)
        # Pieces of longer messages: phrases only, so that one word in the name of a mod stays as it is.
        pieces = sorted((key for key in exact if ' ' in key), key=len, reverse=True)
        self.inside = re.compile(rf'(?<![{_LETTER}])(?:{"|".join(map(re.escape, pieces))})(?![{_LETTER}])') if pieces else None
        # Few fixed words match nearly anything: "{} из {}" would swallow a whole message made of sentences.
        self.strong = [(pattern, target) for size, pattern, target in holed if size >= _STRONG_PATTERN]
        self.loose = [(pattern, target) for size, pattern, target in holed if size < _STRONG_PATTERN]
        self.exact, self.finish = exact, finish


_CATALOGS = {'en': _Catalog(EN, _finish_en), 'uk': _Catalog(UK, _finish_uk)}


def _matching(text, patterns, language):
    stripped = text.strip()
    for pattern, target in patterns:
        match = pattern.fullmatch(stripped)
        if match:
            return text.replace(stripped, target.format(*(translate(group, language) for group in match.groups())))
    return None


def _whole(text, catalog, language):
    stripped = text.strip()
    if stripped in catalog.exact:
        return text.replace(stripped, catalog.exact[stripped])
    return _matching(text, catalog.strong, language)


def _in_parts(text, catalog, language):
    parts = _BETWEEN.split(text)
    if len(parts) > 1:
        return ''.join(part if index % 2 else translate(part, language) for index, part in enumerate(parts))
    result = _matching(text, catalog.loose, language)
    if result is not None:
        return result
    return catalog.inside.sub(lambda match: catalog.exact[match[0]], text) if catalog.inside else text


@lru_cache(maxsize=16384)
def translate(text, language='en'):
    """A text of the program in another language. A mod's own name passes through untouched."""
    catalog = _CATALOGS.get(language)
    if catalog is None or not _CYRILLIC.search(text):
        return text
    result = _whole(text, catalog, language)
    return catalog.finish(result if result is not None else _in_parts(text, catalog, language))


def system_language():
    """For the first start only: what Windows speaks. Later the choice of the person counts."""
    if os.name != 'nt':
        code = (os.environ.get('LANG') or '')[:2]
        return code if code in ('ru', 'uk') else 'en'
    try:
        # The low byte is the language: 0x19 is Russian, 0x22 Ukrainian.
        return {0x19: 'ru', 0x22: 'uk'}.get(ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0xFF, 'en')
    except (AttributeError, OSError):
        return 'ru'


_TEXT_ATTRIBUTES = ('label', 'hint_text', 'helper_text', 'error_text', 'tooltip', 'text')
_CHILD_ATTRIBUTES = ('controls', 'content', 'options', 'actions', 'leading', 'trailing', 'title', 'subtitle')
_MEMORY = '_screen_text'


def source_text(control, name='value'):
    """The text the program itself put here. Logic reads this, whatever language the person sees."""
    current = getattr(control, name, None)
    source, shown = control.__dict__.get(_MEMORY, {}).get(name, (None, None))
    return source if source is not None and current == shown else current


class ScreenLanguage:
    """Puts the texts of a tree of controls into the chosen language, and back."""

    def __init__(self, language='ru'):
        self.language = language if language in LANGUAGES else 'ru'
        self.touched = self.language != 'ru'

    def set(self, language):
        self.language = language if language in LANGUAGES else 'ru'
        self.touched = self.touched or self.language != 'ru'

    def text(self, value):
        return translate(value, self.language) if self.language != 'ru' else value

    def _put(self, control, name):
        current = getattr(control, name, None)
        if not isinstance(current, str) or not current:
            return
        memory = control.__dict__.setdefault(_MEMORY, {})
        source, shown = memory.get(name, (None, None))
        if current != shown:  # the program has put a new text here
            source = current
        wanted = self.text(source)
        memory[name] = (source, wanted)
        if wanted != current:
            setattr(control, name, wanted)

    def _walk(self, control, seen):
        if control is None or isinstance(control, (str, bytes, int, float, bool)) or id(control) in seen:
            return
        seen.add(id(control))
        if type(control).__name__ == 'Text':  # not the value of a field: the person typed that
            self._put(control, 'value')
        for name in _TEXT_ATTRIBUTES:
            self._put(control, name)
        for name in _CHILD_ATTRIBUTES:
            child = getattr(control, name, None)
            if isinstance(child, str):
                if name == 'content':
                    self._put(control, name)
            elif isinstance(child, (list, tuple)):
                for entry in child:
                    self._walk(entry, seen)
            elif child is not None:
                self._walk(child, seen)

    def apply(self, *roots):
        if not self.touched:
            return  # always the case for those who never left Russian: nothing is walked
        seen = set()
        for root in roots:
            self._walk(root, seen)

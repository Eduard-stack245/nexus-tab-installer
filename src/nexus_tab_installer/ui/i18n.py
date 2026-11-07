"""The language of the screen.

The program is written in Russian, and English is laid over it right where text reaches the screen.
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

LANGUAGES = ('ru', 'en')
_CYRILLIC = re.compile('[А-Яа-яЁё]')
_LETTER = 'А-Яа-яЁё'
_MONTHS = {'января': 'January', 'февраля': 'February', 'марта': 'March', 'апреля': 'April', 'мая': 'May', 'июня': 'June',
           'июля': 'July', 'августа': 'August', 'сентября': 'September', 'октября': 'October', 'ноября': 'November', 'декабря': 'December'}
_DATE = re.compile(rf'(\d{{1,2}}) ({"|".join(_MONTHS)}) (\d{{4}})')
_SIZE = re.compile(r'(\d+)(?:,(\d+))? (КБ|МБ|ГБ)')
_UNITS = {'КБ': 'KB', 'МБ': 'MB', 'ГБ': 'GB'}
# plural() of views.py: the numbers come first and the rest of the phrase follows.
_COUNTED = {'файл ждёт': ('file is waiting', 'files are waiting'), 'файла ждут': ('file is waiting', 'files are waiting'),
            'файлов ждут': ('file is waiting', 'files are waiting'), 'файл': ('file', 'files'), 'файла': ('file', 'files'),
            'файлов': ('file', 'files'), 'сессия': ('session', 'sessions'), 'сессии': ('session', 'sessions'),
            'сессий': ('session', 'sessions'), 'мод': ('mod', 'mods'), 'мода': ('mod', 'mods'), 'модов': ('mod', 'mods')}
_COUNT = re.compile(rf'(\d+) ({"|".join(sorted(map(re.escape, _COUNTED), key=len, reverse=True))})(?![{_LETTER}])')
_ONE_OF_MANY = re.compile(r'\b1 (mod|file|session)s\b')
_STRONG_PATTERN = 12  # fixed letters that make a pattern with holes specific enough
_BETWEEN = re.compile(r'(\n+|\s+·\s+|;\s+|(?<=[.!?…])\s+)')


def _prepare():
    exact, holed = {}, []
    for source, target in EN.items():
        source, target = source.strip(), target.strip()
        if '{}' in source:
            parts = source.split('{}')
            holed.append((len(''.join(parts)), re.compile('(.*?)'.join(map(re.escape, parts))), target))
        else:
            exact[source] = target
    holed.sort(key=lambda row: row[0], reverse=True)
    # Pieces of longer messages: phrases only, so that one word in the name of a mod stays as it is.
    pieces = sorted((key for key in exact if ' ' in key), key=len, reverse=True)
    inside = re.compile(rf'(?<![{_LETTER}])(?:{"|".join(map(re.escape, pieces))})(?![{_LETTER}])') if pieces else None
    # Few fixed words match nearly anything: "{} из {}" would swallow a whole message made of sentences.
    strong = [(pattern, target) for size, pattern, target in holed if size >= _STRONG_PATTERN]
    loose = [(pattern, target) for size, pattern, target in holed if size < _STRONG_PATTERN]
    return exact, strong, loose, inside


_EXACT, _STRONG, _LOOSE, _INSIDE = _prepare()


def _matching(text, patterns):
    stripped = text.strip()
    for pattern, target in patterns:
        match = pattern.fullmatch(stripped)
        if match:
            return text.replace(stripped, target.format(*(translate(group) for group in match.groups())))
    return None


def _whole(text):
    stripped = text.strip()
    if stripped in _EXACT:
        return text.replace(stripped, _EXACT[stripped])
    return _matching(text, _STRONG)


def _in_parts(text):
    parts = _BETWEEN.split(text)
    if len(parts) > 1:
        return ''.join(part if index % 2 else translate(part) for index, part in enumerate(parts))
    result = _matching(text, _LOOSE)
    if result is not None:
        return result
    return _INSIDE.sub(lambda match: _EXACT[match[0]], text) if _INSIDE else text


def _finish(text):
    text = _DATE.sub(lambda m: f'{_MONTHS[m[2]]} {int(m[1])}, {m[3]}', text)
    text = _SIZE.sub(lambda m: f"{m[1]}{'.' + m[2] if m[2] else ''} {_UNITS[m[3]]}", text)
    text = _COUNT.sub(lambda m: f'{m[1]} {_COUNTED[m[2]][0 if m[1] == "1" else 1]}', text)
    return _ONE_OF_MANY.sub(lambda m: f'1 {m[1]}', text)


@lru_cache(maxsize=8192)
def translate(text):
    """English for a text of the program. A mod's own name passes through untouched."""
    if not _CYRILLIC.search(text):
        return text
    result = _whole(text)
    return _finish(result if result is not None else _in_parts(text))


def system_language():
    """For the first start only: what Windows speaks. Later the choice of the person counts."""
    if os.name != 'nt':
        return 'ru' if (os.environ.get('LANG') or '').startswith('ru') else 'en'
    try:
        # The low byte is the language: 0x19 is Russian.
        return 'ru' if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0xFF == 0x19 else 'en'
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
        return translate(value) if self.language == 'en' else value

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

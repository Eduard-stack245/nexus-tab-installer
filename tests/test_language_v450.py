"""The language of the screen. Goes through every text of the source, so that a new text cannot stay untranslated."""
import ast
import re
from pathlib import Path

import flet as ft
import pytest

from nexus_tab_installer.core.settings import load_settings
from nexus_tab_installer.ui.i18n import ScreenLanguage, source_text, translate
from nexus_tab_installer.ui.lang_en import EN
import nexus_tab_installer.ui.flet_app as module
from nexus_tab_installer.nexus.graphql import ModInfo, Requirement
from test_ui_end_to_end_v400 import CDN, GAME, MOD_A, launch  # noqa: F401 (fixture)

SOURCE = Path(__file__).resolve().parents[1] / 'src' / 'nexus_tab_installer'
CYRILLIC = re.compile('[А-Яа-яЁё]')
NOT_FOR_THE_SCREEN = {
    # What authors write and what pages say: recognised, never shown.
    ('display.py', 'в'), ('display.py', 'Nexus'), ('requirements.py', 'необязательн'),
    # Told by date rules, see _finish of i18n.py.
    *(('views.py', month) for month in ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря')),
}
ALLOWED_RUSSIAN = {'Русский', 'Язык / Language', 'Українська'}  # the name of the language itself
# Arguments of plural(): they reach the screen with a number, and the number has its own rule in i18n.py.
COUNTED_WORDS = {'файл', 'файла', 'файлов', 'мод', 'мода', 'модов', 'сессия', 'сессии', 'сессий', 'файл ждёт', 'файла ждут', 'файлов ждут'}


def shown_texts():
    """(file, text) of everything Russian in the source, with places to fill in as {}."""
    for path in sorted(SOURCE.rglob('*.py')):
        if path.name in ('lang_en.py', 'lang_uk.py', 'i18n.py'):
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        skipped = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    skipped.add(id(first.value))
            if isinstance(node, ast.JoinedStr):
                skipped.update(id(part) for part in node.values)
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                yield path.name, ''.join(part.value if isinstance(part, ast.Constant) else '{}' for part in node.values)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skipped:
                yield path.name, node.value


def untranslated():
    missing = []
    for name, text in shown_texts():
        if not CYRILLIC.search(text) or text in ALLOWED_RUSSIAN or text in COUNTED_WORDS:
            continue
        if any(name == place and piece in text for place, piece in NOT_FOR_THE_SCREEN):
            continue
        # A text that ends with ": " or a space gets the rest added by the program.
        filled = re.sub(r'\{\}', 'X1', text)
        filled = filled + 'X1' if filled.endswith((': ', ' ')) else filled
        if CYRILLIC.search(translate(filled)):
            missing.append((name, text))
    return sorted(set(missing))


def test_every_text_of_the_program_has_english():
    assert untranslated() == []


def test_catalog_is_well_formed():
    for source, target in EN.items():
        if source in ALLOWED_RUSSIAN:
            continue
        assert not CYRILLIC.search(target), source
        holes = source.count('{}')
        assert len(re.findall(r'\{\d*\}', target)) == holes, source


@pytest.mark.parametrize('russian, english', [
    ('Готово: скачано и обработано 2 из 3 файлов.', 'Done: 2 of 3 files downloaded and processed.'),
    ('Готово: скачано и обработано 2 из 3 файлов. Ошибок: 1 — подробности в строках модов и во вкладке «Загрузки».',
     'Done: 2 of 3 files downloaded and processed. Errors: 1, see the rows of the mods and the "Downloads" tab for details.'),
    ('Браузер скачает 3 файла по очереди, примерно 5 мин без учёта самих загрузок. Не закрывайте браузер: между файлами идут паузы.',
     'The browser will download 3 files one by one, about 5 min not counting the downloads themselves. Keep the browser open: there are pauses between files.'),
    ('Подключено: edge — 1 проф., 3 модов', 'Connected: edge: 1 profile(s), 3 mods'),
    ('2 сессии · 5 файлов · 1,25 ГБ', '2 sessions · 5 files · 1.25 GB'),
    ('1 сессия · 1 файл · 12 КБ', '1 session · 1 file · 12 KB'),
    ('Сегодня, 12:30', 'Today, 12:30'),
    ('5 ноября 2026, 12:30', 'November 5, 2026, 12:30'),
    ('Новый список: 1 модов. История и скачанные файлы сохранены.', 'New list: 1 mod. The history and downloaded files are kept.'),
    ('Браузер: Ожидаем отсчёт сайта перед скачиванием', 'Browser: Waiting for the countdown of the site'),
    ('Осторожный: файл раз в 60 секунд', 'Careful: one file every 60 seconds'),
    ('Файл раз в 7 секунд, после 10 файлов пауза 5 минут, не больше 200 файлов в сутки. 100 файлов займут примерно 1 ч без учёта самих загрузок. Nexus не публикует лимит скачивания с сайта: готовые варианты выбраны программой и не гарантируют отсутствие блокировки. Пауза сохраняется и после перезапуска программы.',
     'One file every 7 seconds, a pause of 5 minutes after 10 files, at most 200 files a day. 100 files take about 1 h not counting the downloads themselves. Nexus does not publish a limit for site downloads: the presets are the choice of this program and do not guarantee that there will be no block. A pause survives a restart of the program.'),
    ('Мод #196600', 'Mod #196600'),
])
def test_messages_built_from_pieces(russian, english):
    assert translate(russian) == english


def test_name_of_a_mod_passes_through():
    assert translate('Лук Ветра') == 'Лук Ветра'
    assert translate('SkyUI') == 'SkyUI'
    assert translate('Мод «Лук Ветра» включён в профиле Default.') == 'Mod "Лук Ветра" is enabled in profile Default.'


def test_a_word_inside_a_name_is_not_translated():
    assert translate('Загрузки Драконов') == 'Загрузки Драконов'


def test_language_switches_there_and_back():
    text = ft.Text('Найти моды')
    button = ft.Button('Скачать выбранные', tooltip='Отмена')
    field = ft.TextField(label='Куда установить', value='Введено человеком', hint_text='Вставьте ключ через Ctrl+V')
    choice = ft.Dropdown(label='Показать', options=[ft.dropdown.Option('a', 'Только скачать')])
    root = ft.Column([text, ft.Container(content=button), field, choice])
    screen = ScreenLanguage('en')
    screen.apply(root)
    assert (text.value, button.content, button.tooltip) == ('Find mods', 'Download selected', 'Cancel')
    assert (field.label, field.value, field.hint_text) == ('Install to', 'Введено человеком', 'Paste the key with Ctrl+V')
    assert (choice.label, choice.options[0].text, choice.options[0].key) == ('Show', 'Download only', 'a')
    assert source_text(text) == 'Найти моды'
    text.value = 'Готово'  # the program puts a new text
    screen.apply(root)
    assert text.value == 'Done' and source_text(text) == 'Готово'
    screen.set('ru')
    screen.apply(root)
    assert (text.value, button.content, button.tooltip, field.label, field.value) == ('Готово', 'Скачать выбранные', 'Отмена', 'Куда установить', 'Введено человеком')


def test_russian_is_not_walked_at_all():
    class Touchy(ft.Text):
        def __getattribute__(self, name):
            if name == '_screen_text':
                raise AssertionError('Nothing to do for Russian')
            return super().__getattribute__(name)
    ScreenLanguage('ru').apply(Touchy('Найти моды'))


# ------------------------------------------------------------------ in the program
def buttons(h):
    return [c.content for c in h.tree() if isinstance(c, (ft.Button, ft.TextButton)) and isinstance(c.content, str)]


def all_text(h):
    found = []
    for c in h.tree():
        if isinstance(c, ft.Text) and isinstance(c.value, str):
            found.append(c.value)
        if isinstance(c, (ft.Button, ft.TextButton)) and isinstance(c.content, str):
            found.append(c.content)
        for name in ('label', 'hint_text', 'tooltip'):
            value = getattr(c, name, None)
            if isinstance(value, str):
                found.append(value)
        for option in getattr(c, 'options', None) or []:
            found.append(option.text or '')
    return found


def english(h):
    return [text for text in all_text(h) if CYRILLIC.search(text) and text not in ALLOWED_RUSSIAN]


def nav(h, label):
    next(c for c in h.tree() if isinstance(c, ft.Container) and isinstance(c.content, ft.Row)
         and any(isinstance(x, ft.Text) and x.value == label for x in c.content.controls)).on_click(None)


def press_language(h, code):
    next(c for c in h.tree() if isinstance(c, ft.Container) and isinstance(c.content, ft.Text) and c.content.value == code).on_click(None)


def test_every_screen_is_all_english(launch, monkeypatch):
    info = ModInfo(GAME, 196600, 'Real Name', '1.0', size_kb=2048, game_number=1704, game_name='Skyrim Special Edition',
                   requirements=[Requirement('Library', 'Only for VR', 1, GAME), Requirement('SKSE', url='https://skse.silverlock.org', external=True)])
    monkeypatch.setattr(module, 'mod_infos', lambda ids: {key: info for key in ids})
    h = launch(config={'language': 'en', 'browser_paired': False})
    h.seed_archive(GAME, 196600, 10, 'old.7z')

    async def scenario():
        field = next(c for c in h.tree() if isinstance(c, ft.TextField) and c.multiline)
        field.value = MOD_A
        next(c for c in h.tree() if isinstance(c, ft.Button) and c.content == 'Add links').on_click(None)
        await h.until(lambda: any(isinstance(c, ft.Text) and c.value == 'Real Name' for c in h.tree()))
        next(c for c in h.tree() if isinstance(c, ft.Container) and c.tooltip == 'Show the requirements of the mod').on_click(None)
        assert english(h) == []
        nav(h, 'Downloads')
        assert english(h) == []
        nav(h, 'Settings')
        assert english(h) == []
        nav(h, 'Log')
        await h.until(lambda: not english(h))

    h.run(scenario)


def test_switching_keeps_the_list_and_is_remembered(launch, tmp_path):
    h = launch(config={'browser_paired': True})
    h.paste_links(MOD_A)
    press_language(h, 'EN')
    assert 'Download selected' in buttons(h) and 'Скачать выбранные' not in buttons(h)
    assert load_settings(tmp_path / 'data' / 'config' / 'config.json')['language'] == 'en'
    assert any(isinstance(c, ft.Dropdown) and c.label == 'Install to' for c in h.tree()), 'The list is still there'
    press_language(h, 'RU')
    assert 'Скачать выбранные' in buttons(h)
    assert load_settings(tmp_path / 'data' / 'config' / 'config.json')['language'] == 'ru'


def test_logic_works_in_english(launch):
    """The logic reads what it wrote itself, not what the person sees."""
    h = launch(config={'language': 'en', 'browser_paired': True})
    h.home.joinpath('Downloads').mkdir()

    async def scenario():
        field = next(c for c in h.tree() if isinstance(c, ft.TextField) and c.multiline)
        field.value = MOD_A
        next(c for c in h.tree() if isinstance(c, ft.Button) and c.content == 'Add links').on_click(None)
        next(c for c in h.tree() if isinstance(c, ft.Button) and c.content == 'Download selected').on_click(None)
        job = await h.next_job()
        await h.report(job, status='url', url=CDN, file_id=1)
        await h.until(lambda: any(isinstance(c, ft.Text) and c.value == 'Done: 1 of 1 file downloaded and processed.' for c in h.tree()))

    h.run(scenario)


def test_messages_from_windows_speak_the_chosen_language(launch, monkeypatch):
    told = []
    monkeypatch.setattr(module, 'notify', lambda *a, **k: told.append(a[:2]))
    h = launch(config={'language': 'en', 'browser_paired': True})

    async def scenario():
        field = next(c for c in h.tree() if isinstance(c, ft.TextField) and c.multiline)
        field.value = MOD_A
        next(c for c in h.tree() if isinstance(c, ft.Button) and c.content == 'Add links').on_click(None)
        next(c for c in h.tree() if isinstance(c, ft.Button) and c.content == 'Download selected').on_click(None)
        job = await h.next_job()
        await h.report(job, status='url', url=CDN, file_id=1)
        await h.until(lambda: told)

    h.run(scenario)
    assert told[-1] == ('Download finished', 'Done: 1 of 1 file downloaded and processed.')


@pytest.mark.parametrize('windows, expected', [('en', 'English'), ('ru', 'Русский')])
def test_first_start_follows_windows(launch, monkeypatch, windows, expected):
    monkeypatch.setattr(module, 'system_language', lambda: windows)
    h = launch()
    nav(h, 'Settings' if windows == 'en' else 'Настройки')
    choice = next(c for c in h.tree() if isinstance(c, ft.Dropdown) and c.label == 'Язык / Language')
    assert choice.value == windows


def test_who_has_used_the_program_stays_in_russian_whatever_windows_speaks(launch, monkeypatch):
    monkeypatch.setattr(module, 'system_language', lambda: 'en')
    h = launch(config={'browser_paired': True})
    assert 'Скачать выбранные' in buttons(h)


# ------------------------------------------------------------------ Ukrainian, the third language
from nexus_tab_installer.ui.lang_uk import UK  # noqa: E402


def test_ukrainian_has_every_text_the_english_one_has():
    """English is checked against the whole source above; Ukrainian must cover the same texts."""
    assert sorted(set(EN) - set(UK)) == [] and sorted(set(UK) - set(EN)) == []


def test_ukrainian_catalog_is_well_formed():
    for source, target in UK.items():
        assert len(re.findall(r'\{\d*\}', target)) == source.count('{}'), source
        if source not in ALLOWED_RUSSIAN:
            assert not re.search('[ыэъё]', target), (source, target)


@pytest.mark.parametrize('russian, ukrainian', [
    ('Готово: скачано и обработано 2 из 3 файлов.', 'Готово: завантажено й оброблено 2 з 3 файлів.'),
    ('Новый список: 1 модов. История и скачанные файлы сохранены.', 'Новий список: 1 мод. Історію й завантажені файли збережено.'),
    ('Новый список: 3 модов. История и скачанные файлы сохранены.', 'Новий список: 3 моди. Історію й завантажені файли збережено.'),
    ('2 сессии · 5 файлов · 1,25 ГБ', '2 сесії · 5 файлів · 1,25 ГБ'),
    ('1 сессия · 21 файл · 12 КБ', '1 сесія · 21 файл · 12 КБ'),
    ('5 ноября 2026, 12:30', '5 листопада 2026, 12:30'),
    ('Сегодня, 12:30', 'Сьогодні, 12:30'),
    ('Мод «Лук Ветра» включён в профиле Default.', 'Мод «Лук Ветра» увімкнено в профілі Default.'),
    ('Браузер скачает 3 файла по очереди, примерно 5 мин без учёта самих загрузок. Не закрывайте браузер: между файлами идут паузы.',
     'Браузер завантажить 3 файли по черзі, приблизно 5 хв без урахування самих завантажень. Не закривайте браузер: між файлами є паузи.'),
])
def test_ukrainian_messages_built_from_pieces(russian, ukrainian):
    assert translate(russian, 'uk') == ukrainian


def test_every_screen_is_all_ukrainian(launch, monkeypatch):
    info = ModInfo(GAME, 196600, 'Real Name', '1.0', size_kb=2048, game_number=1704, game_name='Skyrim Special Edition',
                   requirements=[Requirement('Library', 'Only for VR', 1, GAME), Requirement('SKSE', url='https://skse.silverlock.org', external=True)])
    monkeypatch.setattr(module, 'mod_infos', lambda ids: {key: info for key in ids})
    h = launch(config={'language': 'uk', 'browser_paired': False})
    h.seed_archive(GAME, 196600, 10, 'old.7z')

    def russian_left():
        sources = {source.strip() for source in EN} | {source.strip() for source in UK}
        return [text for text in all_text(h) if text.strip() in sources and UK.get(text.strip(), text) != text
                and text not in ALLOWED_RUSSIAN] + [text for text in all_text(h) if re.search('[ыэъё]', text) and text not in ALLOWED_RUSSIAN]

    async def scenario():
        field = next(c for c in h.tree() if isinstance(c, ft.TextField) and c.multiline)
        field.value = MOD_A
        next(c for c in h.tree() if isinstance(c, ft.Button) and c.content == 'Додати посилання').on_click(None)
        await h.until(lambda: any(isinstance(c, ft.Text) and c.value == 'Real Name' for c in h.tree()))
        assert russian_left() == []
        nav(h, 'Завантаження')
        assert russian_left() == []
        nav(h, 'Налаштування')
        assert russian_left() == []

    h.run(scenario)


def test_switching_to_ukrainian_and_back(launch, tmp_path):
    h = launch(config={'browser_paired': True})
    press_language(h, 'UA')
    assert 'Завантажити обрані' in buttons(h)
    assert load_settings(tmp_path / 'data' / 'config' / 'config.json')['language'] == 'uk'
    press_language(h, 'RU')
    assert 'Скачать выбранные' in buttons(h)


def test_first_start_on_ukrainian_windows_is_ukrainian(launch, monkeypatch):
    monkeypatch.setattr(module, 'system_language', lambda: 'uk')
    h = launch()
    assert 'Завантажити обрані' in buttons(h)

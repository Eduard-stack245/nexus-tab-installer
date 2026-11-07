"""Data-only onboarding for the Nexus Tab Collector browser extension.

These are developer/preview installation instructions, NOT store listings.  A
store-published extension will replace these steps for regular users.
The names of the browsers' own buttons were checked on their real pages.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserSetup:
    key: str
    name: str
    management_url: str
    extension_dir: str
    steps: tuple[str, ...]
    note: str = ""


def _open_page(url):
    return f'Нажмите «Открыть расширения» ниже. Если страница не открылась, введите в адресной строке браузера: {url}'


_ALLOW = 'Вернитесь в эту программу: вверху окна появится запрос. Нажмите «Разрешить браузер».'
_DONE = 'Через несколько секунд в этом блоке появится зелёная отметка: браузер подключён, инструкция закроется сама.'

BROWSERS = {
    'edge': BrowserSetup(
        'edge', 'Microsoft Edge', 'edge://extensions', 'chromium',
        (_open_page('edge://extensions'),
         'Включите «Режим разработчика» на этой странице.',
         'Нажмите «Загрузить распакованное» и выберите папку chromium: её путь указан ниже, «Показать папку» откроет её.',
         _ALLOW, _DONE)),
    'chrome': BrowserSetup(
        'chrome', 'Google Chrome', 'chrome://extensions', 'chromium',
        (_open_page('chrome://extensions'),
         'Включите «Режим разработчика» в правом верхнем углу.',
         'Нажмите «Загрузить распакованное расширение» и выберите папку chromium: её путь указан ниже.',
         _ALLOW, _DONE)),
    'opera-gx': BrowserSetup(
        'opera-gx', 'Opera GX', 'opera://extensions', 'chromium',
        (_open_page('opera://extensions'),
         'Включите «Режим разработчика» на этой странице.',
         'Нажмите «Загрузить распакованное расширение» и выберите папку chromium: её путь указан ниже.',
         _ALLOW, _DONE),
        note='Если в GX Control включён RAM Limiter с жёстким лимитом, он может выгружать вкладку скачивания. Отключите его на время большой загрузки.'),
    'firefox': BrowserSetup(
        'firefox', 'Mozilla Firefox', 'about:debugging#/runtime/this-firefox', 'firefox',
        (_open_page('about:debugging#/runtime/this-firefox'),
         'Нажмите «Загрузить временное дополнение».',
         'Выберите файл manifest.json из папки firefox: её путь указан ниже.',
         _ALLOW,
         'Нажмите значок расширения. Если появится кнопка «Разрешить доступ к Nexus», нажмите её.',
         _DONE),
        note='Временное дополнение Firefox удаляется при перезапуске браузера, и шаги придётся повторить. Для постоянной установки расширение нужно подписать на addons.mozilla.org.'),
}


def get_browser_setup(key):
    return BROWSERS[key]

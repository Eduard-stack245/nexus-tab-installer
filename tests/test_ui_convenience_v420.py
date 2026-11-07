"""What the person sees: warnings for CAPTCHA and Premium, and a screen that gets out of the way."""
import asyncio
import threading

import flet as ft

from datetime import datetime, timezone

from nexus_tab_installer.core.trace import describe_event, is_main_event, local_clock
from nexus_tab_installer.ui.views import PREMIUM_WARNING
from test_ui_end_to_end_v400 import CDN, GAME, KEY, MOD_A, MOD_B, account, launch, nexus_file  # noqa: F401 (fixture)

SETUP_TITLE = 'Подключение браузера — один раз'


def holds_text(control, text, seen=None):
    seen = set() if seen is None else seen
    if control is None or id(control) in seen:
        return False
    seen.add(id(control))
    if isinstance(control, ft.Text) and control.value == text:
        return True
    children = list(getattr(control, 'controls', None) or [])
    content = getattr(control, 'content', None)
    if isinstance(content, ft.Control):
        children.append(content)
    return any(holds_text(child, text, seen) for child in children)


def panel_holding(h, text):
    """The innermost card with this text: its "visible" is what the person sees."""
    return [c for c in h.tree() if isinstance(c, ft.Container) and c.bgcolor and holds_text(c, text)][-1]


def button(h, label):
    return next(c for c in h.tree() if isinstance(c, ft.Button) and c.content == label)


def open_tab(h, label):
    tab = next(c for c in h.tree() if isinstance(c, ft.Container) and isinstance(c.content, ft.Row)
               and any(isinstance(x, ft.Text) and x.value == label for x in c.content.controls))
    tab.on_click(None)


class Client:
    premium = False
    names = {}

    def __init__(self, _key):
        pass

    def validate(self):
        return {'is_premium': self.premium}

    def files(self, game, mod_id):
        return [nexus_file(mod_id * 10)]

    def mod(self, game, mod_id):
        return {'name': self.names[mod_id]}

    def download_links(self, *args):
        return CDN


class PremiumClient(Client):
    premium = True


# ------------------------------------------------------------------ CAPTCHA and login
def test_captcha_is_only_a_warning_while_the_person_solves_it(launch):
    h = launch()

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1')
        h.click('Скачать выбранные')
        job = await h.next_job()
        await h.report(job, status='progress', step='verify')
        await h.until(lambda: h.text_starting('Nexus просит пройти проверку (CAPTCHA). Программа её не проходит'))
        assert not button(h, 'Продолжить загрузку').visible, 'The page goes on by itself after the person solves it'
        # The person solved it in the browser: the warning leaves, the same file goes on.
        await h.report(job, status='progress', step='clicked')
        await h.until(lambda: not h.text_starting('Nexus просит пройти проверку'))
        await h.report(job, status='url', url=CDN, file_id=1)
        await h.until(h.finished)

    h.run(scenario)
    assert h.finished() == 'Готово: скачано и обработано 1 из 1 файлов.'


def test_unsolved_captcha_stops_the_queue_instead_of_burning_every_file(launch):
    h = launch()

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1\n{MOD_B}?tab=files&file_id=2')
        h.click('Скачать выбранные')
        job = await h.next_job()
        await h.report(job, status='failed', reason='verification')
        await h.until(lambda: button(h, 'Продолжить загрузку').visible)
        await h.until(lambda: h.text_starting('Nexus просит пройти проверку (CAPTCHA). Программа её не проходит и не обходит'))
        assert (await h.get('/auto/next'))['job'] is None
        h.click('Продолжить загрузку')
        again = await h.next_job()
        assert again['id'] == job['id'], 'The same file gets another chance'
        await h.until(lambda: not button(h, 'Продолжить загрузку').visible)

    h.run(scenario)


# ------------------------------------------------------------------ Premium
def test_free_account_sees_no_premium_warning(launch):
    h = launch(config=KEY, client=Client)

    async def scenario():
        h.paste_links(MOD_A)
        h.click('Скачать выбранные')
        await h.next_job()
        await h.until(lambda: h.status_lines() is not None)

    h.run(scenario)
    assert not panel_holding(h, PREMIUM_WARNING).visible


def test_premium_account_is_warned_that_it_was_not_tested(launch):
    h = launch(config=KEY, client=PremiumClient)

    async def scenario():
        h.paste_links(MOD_A)
        h.click('Скачать выбранные')
        await h.until(h.finished)
        await h.until(lambda: panel_holding(h, PREMIUM_WARNING).visible)

    h.run(scenario)


def test_premium_seen_on_the_page_without_an_api_key_is_warned_too(launch):
    h = launch()

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1')
        h.click('Скачать выбранные')
        job = await h.next_job()
        await h.report(job, status='progress', step='clicked', info={'premium': True})
        await h.until(lambda: panel_holding(h, PREMIUM_WARNING).visible)

    h.run(scenario)


def test_premium_warning_is_also_written_in_the_settings(launch):
    h = launch()
    open_tab(h, 'Настройки')
    assert h.text_starting('Premium-аккаунт: эта часть программы на Premium не проверялась')


# ------------------------------------------------------------------ getting out of the way
def test_first_start_shows_the_browser_steps_and_hides_them_once_connected(launch):
    h = launch()
    assert panel_holding(h, SETUP_TITLE).visible

    async def scenario():
        await h.snapshot([])
        await h.until(lambda: not panel_holding(h, SETUP_TITLE).visible)

    h.run(scenario)


def test_hidden_browser_steps_reach_the_window(launch):
    """Changing a control is not enough: the change has to be sent to the window."""
    h = launch()
    panel = panel_holding(h, SETUP_TITLE)
    drawn = []
    h.page.update.side_effect = lambda *_: drawn.append(panel.visible)

    async def scenario():
        await h.snapshot([])
        await h.until(lambda: not panel.visible)
        await asyncio.sleep(0.2)

    h.run(scenario)
    assert drawn and drawn[-1] is False


def test_connected_browser_does_not_show_the_steps_again(launch):
    h = launch(config={'browser_paired': True})
    assert not panel_holding(h, SETUP_TITLE).visible


def test_steps_opened_by_the_person_stay_open(launch):
    h = launch(config={'browser_paired': True})
    next(c for c in h.tree() if isinstance(c, ft.TextButton) and c.content == 'Подключить браузер (инструкция)').on_click(None)
    assert panel_holding(h, SETUP_TITLE).visible

    async def scenario():
        await h.snapshot([])
        await h.until(lambda: h.text_starting('✓ Microsoft Edge'))

    h.run(scenario)
    assert panel_holding(h, SETUP_TITLE).visible


def test_rarely_needed_things_are_folded_and_the_list_comes_first(launch):
    h = launch(config={'browser_paired': True})
    assert not any(isinstance(c, ft.Button) and c.content == 'Открыть журнал работы' for c in h.tree())
    assert not h.text_starting('Без изменений файлов игры')


def test_pasted_links_close_the_field_and_get_their_names_from_nexus(launch):
    Client.names = {196600: 'SkyUI', 7: 'Other mod'}
    h = launch(config={**KEY, 'browser_paired': True}, client=Client)

    async def scenario():
        h.click('Вставить ссылки или открыть JSON')
        assert panel_holding(h, 'Добавить моды вручную').visible
        h.paste_links(f'{MOD_A}\n{MOD_B}')
        assert not panel_holding(h, 'Добавить моды вручную').visible
        await h.until(lambda: h.text_starting('SkyUI') and h.text_starting('Other mod'))

    h.run(scenario)


def test_no_name_is_no_problem(launch):
    Client.names = {}
    h = launch(config={**KEY, 'browser_paired': True}, client=Client)

    async def scenario():
        h.paste_links(MOD_A)
        await h.until(lambda: h.text_starting('Добавлено модов: 1'))
        await h.until(lambda: h.text_starting('Новый список'))

    h.run(scenario)
    log = (h.home / 'data' / 'logs' / 'download-trace.jsonl').read_text(encoding='utf-8')
    assert 'exception' not in log


def test_downloads_screen_speaks_russian_and_shows_files_first(launch):
    h = launch()
    h.seed_archive(GAME, 196600, 5, 'mod.7z')

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=5')
        h.click('Скачать выбранные')
        await h.until(h.finished)

    h.run(scenario)
    open_tab(h, 'Загрузки')
    assert h.text_starting('mod.7z')
    assert h.text_starting('1 сессия · 1 файл')
    assert any(isinstance(c, ft.Column) and c.visible is False and holds_text(c, 'Ссылка NXM') for c in h.tree()), \
        'The tools for special cases stay folded'


def test_slow_file_lookup_is_not_taken_for_a_finished_batch(launch):
    """Found on the real site: "ready" after 0 files, then the batch ran with no way to cancel it."""
    release = threading.Event()

    class SlowClient(account(files={(GAME, 196600): [nexus_file(5)]})):
        def files(self, game, mod_id):
            release.wait(20)
            return super().files(game, mod_id)

    h = launch(config={**KEY, 'browser_paired': True}, client=SlowClient)

    async def scenario():
        h.paste_links(MOD_A)
        h.click('Скачать выбранные')
        await asyncio.sleep(3.5)  # the loop checks for a finished batch every 3 seconds
        assert not h.finished()
        assert not next(c for c in h.tree() if isinstance(c, ft.Button) and c.content == 'Отмена').disabled
        release.set()
        job = await h.next_job()
        await h.report(job, status='url', url=CDN, file_id=5)
        await h.until(h.finished)

    h.run(scenario)
    assert h.finished() == 'Готово: скачано и обработано 1 из 1 файлов.'


def test_waiting_for_the_browser_during_a_search_is_not_a_finished_batch(launch):
    h = launch(config={'browser_paired': True})

    async def scenario():
        h.click('Найти моды')
        await asyncio.sleep(3.5)
        assert not h.finished()

    h.run(scenario)


def test_idle_program_does_not_redraw_the_window(launch):
    """A list of 100 mods makes each redraw expensive, and nothing here has changed."""
    h = launch(config={'browser_paired': True})

    async def scenario():
        await asyncio.sleep(1)
        h.page.update.reset_mock()
        await asyncio.sleep(2)

    h.run(scenario)
    assert h.page.update.call_count == 0


def test_closed_window_is_not_an_error(launch):
    h = launch(config={'browser_paired': True})
    log = h.home / 'data' / 'logs' / 'download-trace.jsonl'

    async def scenario():
        h.page.update.reset_mock()
        h.page.update.side_effect = RuntimeError('An attempt to fetch destroyed session.')
        await h.snapshot([])
        await asyncio.sleep(1)

    h.run(scenario)
    assert h.page.update.called, 'The program tried to draw after the window had closed'
    assert 'app.exception' not in log.read_text(encoding='utf-8')


def test_a_real_failure_in_the_loop_is_still_recorded(launch):
    h = launch(config={'browser_paired': True})
    log = h.home / 'data' / 'logs' / 'download-trace.jsonl'

    async def scenario():
        h.page.update.side_effect = RuntimeError('Something else broke')
        await h.snapshot([])
        await asyncio.sleep(1)

    h.run(scenario)
    assert 'app.exception' in log.read_text(encoding='utf-8')


def test_log_shows_the_time_of_this_computer():
    moment = 1793959733
    stored = datetime.fromtimestamp(moment, timezone.utc).isoformat(timespec='milliseconds')
    assert local_clock(stored) == datetime.fromtimestamp(moment).strftime('%H:%M:%S')
    assert local_clock('') == local_clock(None) == '--:--:--'


def test_log_shows_the_main_things_in_plain_words():
    for service in ('ui.action', 'ui.heartbeat', 'ui.log_opened', 'server.response', 'extension.helper.poll_ok',
                    'extension.page.cancel_received'):
        assert not is_main_event(service), service
    for main in ('app.started', 'job.leased', 'ui.transfer_finished', 'batch.paused', 'extension.page.challenge_detected'):
        assert is_main_event(main), main
    assert is_main_event('extension.helper.poll_failed'), 'Errors always stay visible'
    for event in ('batch.paused', 'batch.cooldown', 'extension.page.challenge_detected', 'extension.download.ignored', 'ui.log_opened'):
        assert describe_event({'event': event, 'data': {}}) != event, event

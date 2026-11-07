"""The pace, the site's signals and a restart, through the real controller without a window."""
import json
import os
from pathlib import Path

import flet as ft
import pytest

from nexus_tab_installer.browser import pacing
from nexus_tab_installer.browser.auto_free import REQUIRED_EXTENSION
from nexus_tab_installer.core.paths import storage_paths
from nexus_tab_installer.core.settings import load_settings
from nexus_tab_installer.downloads.transfer import RateLimited
from nexus_tab_installer.nexus.api import NexusRateLimited
from test_ui_end_to_end_v400 import CDN, GAME, KEY, MOD_A, MOD_B, launch  # noqa: F401 (fixture)

ROOT = Path(__file__).resolve().parents[1]
SLOW = pacing.PacePreset('test', gap=300, batch=100, rest=600, daily=1000, api_gap=0)


def test_pause_between_files_is_shown_and_holds_the_next_file(launch, monkeypatch):
    monkeypatch.setitem(pacing.PRESETS, 'careful', SLOW)
    h = launch()

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1\n{MOD_B}?tab=files&file_id=2')
        h.click('Скачать выбранные')
        first = await h.next_job()
        await h.report(first, status='url', url=CDN, file_id=1)
        await h.until(lambda: h.text_starting('Пауза перед следующим файлом'))
        reply = await h.get('/auto/next')
        assert reply['job'] is None and reply['active']
        assert 200 < reply['wait'] <= 300

    h.run(scenario)
    assert 'Осталось файлов: 1' in h.text_starting('Пауза перед следующим файлом')


def test_saved_pace_is_used_and_kept(launch):
    h = launch(config={'download_pace': 'moderate'})

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1\n{MOD_B}?tab=files&file_id=2')
        h.click('Скачать выбранные')
        first = await h.next_job()
        await h.report(first, status='url', url=CDN, file_id=1)
        reply = await h.get('/auto/next')
        assert reply['job'] is None
        assert 0 < reply['wait'] <= pacing.PRESETS['moderate'].gap

    h.run(scenario)
    assert load_settings(storage_paths()['config'])['download_pace'] == 'moderate'


def test_unknown_saved_pace_falls_back_to_the_careful_one(launch):
    # The fixture makes "careful" instant, so a next file at once proves the fallback.
    h = launch(config={'download_pace': 'turbo'})

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1\n{MOD_B}?tab=files&file_id=2')
        h.click('Скачать выбранные')
        first = await h.next_job()
        await h.report(first, status='url', url=CDN, file_id=1)
        assert (await h.next_job(timeout=5))['file_id'] == 2

    h.run(scenario)


def test_unfinished_batch_comes_back_after_a_restart(launch):
    first = launch()
    unfinished = storage_paths()['unfinished']

    async def before_restart():
        first.paste_links(f'{MOD_A}?tab=files&file_id=1\n{MOD_B}?tab=files&file_id=2')
        first.click('Скачать выбранные')
        job = await first.next_job()
        await first.report(job, status='url', url=CDN, file_id=1)
        await first.until(lambda: first.archive_of(GAME, 196600, 1))

    first.run(before_restart)
    assert unfinished.exists()

    second = launch()

    async def after_restart():
        await second.until(lambda: second.text_starting('Прошлая загрузка не закончилась'))
        second.click('Скачать выбранные')
        # The finished file is reused: the site is asked only for the other one.
        job = await second.next_job()
        assert (job['mod_id'], job['file_id']) == (7, 2)
        await second.report(job, status='url', url=CDN, file_id=2)
        await second.until(second.finished)

    second.run(after_restart)
    assert second.finished() == 'Готово: скачано и обработано 2 из 2 файлов.'
    assert not unfinished.exists()


def test_cancelled_batch_does_not_come_back(launch):
    h = launch()

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1')
        h.click('Скачать выбранные')
        await h.next_job()
        assert storage_paths()['unfinished'].exists()
        h.click('Отмена')
        assert not storage_paths()['unfinished'].exists()

    h.run(scenario)


def archive(h, age_days=0):
    h.seed_archive('skyrim', 7, 99, 'main.7z', age_days=age_days)


def test_rerun_without_a_key_does_not_ask_the_site_for_a_fresh_archive(launch):
    h = launch()
    archive(h)

    async def scenario():
        h.paste_links(MOD_B)
        h.click('Скачать выбранные')
        await h.until(h.finished)
        assert (await h.get('/auto/next'))['job'] is None

    h.run(scenario)
    assert h.finished() == 'Готово: скачано и обработано 1 из 1 файлов.'
    log = (h.home / 'data' / 'logs' / 'download-trace.jsonl').read_text(encoding='utf-8')
    assert '"file_id":99' in log


def test_old_archive_does_not_stop_a_new_download(launch):
    h = launch()
    archive(h, age_days=30)

    async def scenario():
        h.paste_links(MOD_B)
        h.click('Скачать выбранные')
        assert (await h.next_job())['file_id'] == 0

    h.run(scenario)


def test_site_block_is_explained_and_waits_for_the_user(launch):
    h = launch()

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1')
        h.click('Скачать выбранные')
        job = await h.next_job()
        await h.report(job, status='failed', reason='blocked')
        await h.until(lambda: h.text_starting('Nexus ограничил доступ'))
        reply = await h.get('/auto/next')
        assert reply['job'] is None and reply['wait'] > 90
        h.click('Продолжить загрузку')
        again = await h.next_job()
        assert again['id'] == job['id']
        await h.report(again, status='url', url=CDN, file_id=1)
        await h.until(h.finished)

    h.run(scenario)
    assert h.finished() == 'Готово: скачано и обработано 1 из 1 файлов.'


def test_firefox_save_dialog_stops_the_queue_but_keeps_the_file(launch):
    h = launch()

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1\n{MOD_B}?tab=files&file_id=2')
        h.click('Скачать выбранные')
        job = await h.next_job()
        await h.report(job, status='url', url=CDN, file_id=1, warning='ask_where_to_save')
        await h.until(lambda: h.text_starting('Firefox спрашивает, куда сохранять'))
        await h.until(lambda: h.archive_of(GAME, 196600, 1))
        assert (await h.get('/auto/next'))['job'] is None
        h.click('Продолжить загрузку')
        assert (await h.next_job())['file_id'] == 2

    h.run(scenario)


def test_limit_answer_of_the_site_holds_everything_and_survives_a_restart(launch):
    h = launch()

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1')
        h.click('Скачать выбранные')
        job = await h.next_job()
        await h.report(job, status='failed', reason='rate_limited')
        await h.until(lambda: h.text_starting('Nexus просит подождать, пауза'))
        assert (await h.get('/auto/next'))['wait'] > 1700

    h.run(scenario)
    saved = json.loads(storage_paths()['pace'].read_text(encoding='utf-8'))
    assert saved['cooldown_until'] > 0

    again = launch()

    async def after_restart():
        await again.until(lambda: again.text_starting('Прошлая загрузка не закончилась'))
        again.click('Скачать выбранные')
        await again.until(lambda: again.text_starting('Nexus просит подождать, пауза'))
        assert (await again.get('/auto/next'))['job'] is None

    again.run(after_restart)


def test_cdn_limit_holds_the_queue_then_the_browser_takes_the_file(launch):
    def limited(*_args, **_kwargs):
        raise RateLimited()
    h = launch(download=limited)

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1')
        h.click('Скачать выбранные')
        job = await h.next_job()
        await h.report(job, status='url', url=CDN, file_id=1)
        await h.until(lambda: h.text_starting('Nexus просит подождать, пауза'))
        assert (await h.get('/auto/next'))['job'] is None

    h.run(scenario)
    assert json.loads(storage_paths()['pace'].read_text(encoding='utf-8'))['cooldown_until'] > 0


def test_spent_api_budget_stops_the_questions_and_uses_the_site(launch):
    calls = []

    class Client:
        def __init__(self, _key):
            pass

        def validate(self):
            return {'is_premium': False}

        def files(self, game, mod_id):
            calls.append(mod_id)
            raise NexusRateLimited('limit')

    h = launch(config=KEY, client=Client)

    async def scenario():
        h.paste_links(f'{MOD_A}\n{MOD_B}')
        h.click('Скачать выбранные')
        first = await h.next_job()
        assert first['file_id'] == 0
        await h.report(first, status='failed', reason='file_unavailable')
        assert (await h.next_job())['mod_id'] == 7

    h.run(scenario)
    assert calls == [196600]


def test_old_extension_is_reported_to_the_user(launch):
    h = launch()

    async def scenario():
        await h.get('/auto/next')  # this poll carries no version: an extension before 2.1
        await h.until(lambda: h.text_starting('Расширение браузера устарело'), timeout=12)

    h.run(scenario)
    warning = h.text_starting('Расширение браузера устарело')
    assert REQUIRED_EXTENSION in warning and 'более старая' in warning
    assert 'круглую стрелку' in warning, 'It must say which button reloads the extension'


def test_windows_stays_awake_during_a_batch(launch, monkeypatch):
    h = launch()
    calls = []
    monkeypatch.setattr(h.module, 'set_keep_awake', calls.append)

    async def scenario():
        h.paste_links(f'{MOD_A}?tab=files&file_id=1')
        h.click('Скачать выбранные')
        job = await h.next_job()
        await h.until(lambda: calls == [True])
        await h.report(job, status='url', url=CDN, file_id=1)
        await h.until(h.finished)
        await h.until(lambda: calls == [True, False])

    h.run(scenario)


def test_pace_is_chosen_on_the_settings_screen_and_saved(launch):
    h = launch()
    tab = next(c for c in h.tree() if isinstance(c, ft.Container) and isinstance(c.content, ft.Row)
               and any(isinstance(x, ft.Text) and x.value == 'Настройки' for x in c.content.controls))
    tab.on_click(None)
    choice = next(c for c in h.tree() if isinstance(c, ft.Dropdown) and (c.label or '').startswith('Темп'))
    assert {option.key for option in choice.options} == {*pacing.PRESETS, pacing.CUSTOM}
    choice.value = 'moderate'
    choice.on_select(None)
    assert load_settings(storage_paths()['config'])['download_pace'] == 'moderate'
    assert h.text_starting(f"Файл раз в {pacing.PRESETS['moderate'].gap} секунд, после {pacing.PRESETS['moderate'].batch} файлов пауза")


def test_all_browsers_share_one_extension_code():
    for name in ('background.js', 'auto_download.js', 'auto_free_content.js', 'popup.js', 'popup.html'):
        chromium = (ROOT / 'extensions/chromium' / name).read_bytes()
        assert chromium == (ROOT / 'extensions/firefox' / name).read_bytes(), name
    for browser in ('chromium', 'firefox'):
        manifest = json.loads((ROOT / 'extensions' / browser / 'manifest.json').read_text(encoding='utf-8'))
        assert manifest['version'] == REQUIRED_EXTENSION
    firefox = json.loads((ROOT / 'extensions/firefox/manifest.json').read_text(encoding='utf-8'))
    assert firefox['background']['scripts'] == ['background.js', 'auto_download.js']
    assert firefox['browser_specific_settings']['gecko']['data_collection_permissions'] == {'required': ['none']}


@pytest.mark.parametrize('name', ['careful', 'moderate'])
def test_presets_leave_room_for_the_site(name):
    preset = pacing.PRESETS[name]
    assert preset.gap >= 20 and preset.rest >= 300 and preset.daily <= 600

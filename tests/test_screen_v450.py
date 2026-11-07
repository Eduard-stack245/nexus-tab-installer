"""On the real controller without a window: requirements, updates, deleting, room on the disk, messages, language."""
import json
import os
import time

import flet as ft
import pytest

from nexus_tab_installer.core.requirements import Present, assess
from nexus_tab_installer.core.settings import load_settings
from nexus_tab_installer.core.updates import save_found, Update
from nexus_tab_installer.core.install_plan import ModPlan
from nexus_tab_installer.nexus.api import NexusError
from nexus_tab_installer.nexus.graphql import ModInfo, Requirement
import nexus_tab_installer.ui.flet_app as module
from test_ui_end_to_end_v400 import CDN, GAME, MOD_A, launch  # noqa: F401 (fixture)

SESSION_MOD = 196600
ADDRESS = 32444
SKSE = Requirement(name='SKSE', url='https://skse.silverlock.org/', external=True)
LIBRARY = Requirement(name='Address Library', mod_id=ADDRESS, game=GAME)
VR_ONLY = Requirement(name='VR Library', note='Only for VR users', mod_id=58101, game=GAME)
OLD_TIME = time.time() - 30 * 86400


def tell_nexus(monkeypatch, *requirements, name='Real Name'):
    """Nexus says that the mod needs these."""
    info = ModInfo(GAME, SESSION_MOD, name=name, version='1.0', size_kb=1024, game_number=1704, game_name='Skyrim Special Edition',
                   updated_at=time.time(), requirements=list(requirements))
    asked = []

    def infos(ids):
        asked.append(list(ids))
        return {key: info for key in ids if key == (GAME, SESSION_MOD)}
    monkeypatch.setattr(module, 'mod_infos', infos)
    return asked


def texts(h):
    return [c.value for c in h.tree() if isinstance(c, ft.Text) and isinstance(c.value, str)]


def visible_buttons(h):
    return [c.content for c in h.tree() if isinstance(c, (ft.Button, ft.TextButton)) and c.visible is not False]


def text_button(h, label):
    return next(c for c in h.tree() if isinstance(c, (ft.TextButton, ft.Button)) and c.content == label)


def icon_button(h, tooltip):
    return [c for c in h.tree() if isinstance(c, ft.IconButton) and c.tooltip == tooltip]


def open_tab(h, label):
    next(c for c in h.tree() if isinstance(c, ft.Container) and isinstance(c.content, ft.Row)
         and any(isinstance(x, ft.Text) and x.value == label for x in c.content.controls)).on_click(None)


def holder_of(h, start):
    """The notice that holds a text, to see whether the person sees it."""
    def holds(control):
        if isinstance(control, ft.Text):
            return isinstance(control.value, str) and control.value.startswith(start)
        children = list(getattr(control, 'controls', None) or [])
        content = getattr(control, 'content', None)
        if isinstance(content, ft.Control):
            children.append(content)
        return any(holds(child) for child in children)
    return [c for c in h.tree() if isinstance(c, ft.Container) and c.bgcolor and holds(c)][-1]


# ------------------------------------------------------------------ requirements
def test_requirements_are_a_warning_and_a_download_goes_on_without_them(launch, monkeypatch):
    tell_nexus(monkeypatch, LIBRARY, SKSE)
    h = launch()

    async def scenario():
        h.paste_links(MOD_A)
        await h.until(lambda: h.text_starting('Моды из списка требуют других модов, которых у вас нет (1): Address Library.'))
        banner = holder_of(h, 'Моды из списка требуют')
        assert banner.visible
        text = h.text_starting('Моды из списка требуют')
        assert 'Вне Nexus, проверьте сами: SKSE.' in text and 'скачать можно и без них' in text
        h.click('Скачать выбранные')  # nothing is added and nothing is refused
        button = text_button(h, 'Скачать выбранные')
        assert button.disabled and button.bgcolor is None, 'A button that is off must look off'
        job = await h.next_job()
        assert job['mod_id'] == SESSION_MOD
        await h.report(job, status='url', url=CDN, file_id=1)
        await h.until(h.finished)

    h.run(scenario)
    assert 'Готово: скачано и обработано 1 из 1 файлов.' == h.finished()


def test_name_size_and_game_come_from_nexus_without_a_key(launch, monkeypatch):
    tell_nexus(monkeypatch)
    h = launch()

    async def scenario():
        h.paste_links(MOD_A)
        await h.until(lambda: h.text_starting('Real Name'))
    h.run(scenario)
    assert h.text_starting('Skyrim Special Edition · #196600 · версия 1.0 · 1,0 МБ')


def test_mod_without_requirements_shows_no_warning(launch, monkeypatch):
    tell_nexus(monkeypatch)
    h = launch()

    async def scenario():
        h.paste_links(MOD_A)
        await h.until(lambda: h.text_starting('Real Name'))
    h.run(scenario)
    assert not h.text_starting('Требования есть') and not h.text_starting('Моды из списка требуют')


def test_adding_a_requirement_keeps_the_list_and_its_choices(launch, monkeypatch):
    tell_nexus(monkeypatch, LIBRARY)
    h = launch()

    async def scenario():
        h.paste_links(MOD_A)
        await h.until(lambda: h.text_starting('Моды из списка требуют'))
        target = next(c for c in h.tree() if isinstance(c, ft.Dropdown) and c.label == 'Куда установить')
        target.value = 'vortex'
        target.on_select(type('Event', (), {'control': target})())
        h.click('Добавить в список')
        await h.until(lambda: h.text_starting('2 найденных модов · 2 выбрано'))

    h.run(scenario)
    assert h.text_starting('Real Name') and h.text_starting('Address Library')
    assert not h.text_starting('Моды из списка требуют'), 'It is in the list now: nothing is missing'
    targets = [c.value for c in h.tree() if isinstance(c, ft.Dropdown) and c.label == 'Куда установить']
    assert targets == ['vortex', 'vortex'], 'A requirement goes where the mod that needs it goes'


def test_fetching_requirements_downloads_only_them_after_the_run(launch, monkeypatch):
    tell_nexus(monkeypatch, LIBRARY, VR_ONLY)
    h = launch()

    async def scenario():
        h.paste_links(MOD_A)
        await h.until(lambda: h.text_starting('Моды из списка требуют'))
        h.click('Скачать выбранные')
        await h.until(lambda: text_button(h, 'Докачать сейчас').disabled)
        job = await h.next_job()
        await h.report(job, status='url', url=CDN, file_id=1)
        await h.until(h.finished)
        await h.until(lambda: not text_button(h, 'Докачать сейчас').disabled)
        h.click('Докачать сейчас')
        second = await h.next_job()
        assert second['mod_id'] == ADDRESS, 'What the note says is only for some people is not fetched without asking'
        await h.report(second, status='url', url=CDN.replace('196600', str(ADDRESS)), file_id=2)
        await h.until(lambda: h.text_starting('Готово: скачано и обработано 1 из 1 файлов.'))

    h.run(scenario)
    assert h.archive_of(GAME, ADDRESS, 2)
    assert not h.text_starting('Моды из списка требуют')
    assert 'Готово' in texts(h), 'The state of the first mod stays as it was'


def test_nexus_not_answering_is_a_quiet_warning_and_can_be_retried(launch, monkeypatch):
    def broken(ids):
        raise NexusError('Nexus не ответил: no network')
    monkeypatch.setattr(module, 'mod_infos', broken)
    h = launch()

    async def scenario():
        h.paste_links(MOD_A)
        await h.until(lambda: h.text_starting('Не удалось узнать требования модов'))
        assert 'Скачивать можно и так' in h.text_starting('Не удалось узнать требования модов')
        tell_nexus(monkeypatch, LIBRARY)
        text_button(h, 'Повторить').on_click(None)
        await h.until(lambda: h.text_starting('Моды из списка требуют'))
        h.click('Скачать выбранные')
        await h.next_job()

    h.run(scenario)


def test_hidden_warning_stays_hidden_until_something_changes(launch, monkeypatch):
    tell_nexus(monkeypatch, LIBRARY)
    h = launch()

    async def scenario():
        h.paste_links(MOD_A)
        await h.until(lambda: h.text_starting('Моды из списка требуют'))
        # There are two of these: the warning about the disk has its own.
        next(c for c in h.tree() if isinstance(c, ft.TextButton) and c.content == 'Скрыть' and c.on_click.__name__ == 'hide_requirements').on_click(None)

    h.run(scenario)
    assert not h.text_starting('Моды из списка требуют')


def test_reminder_about_what_lies_outside_nexus_does_not_come_back(launch, monkeypatch, tmp_path):
    tell_nexus(monkeypatch, SKSE)
    h = launch()

    async def scenario():
        h.paste_links(MOD_A)
        await h.until(lambda: h.text_starting('Вне Nexus, проверьте сами: SKSE.'))
        next(c for c in h.tree() if isinstance(c, ft.TextButton) and c.content == 'Скрыть' and c.on_click.__name__ == 'hide_requirements').on_click(None)
    h.run(scenario)
    assert not h.text_starting('Вне Nexus, проверьте сами')
    assert load_settings(tmp_path / 'data' / 'config' / 'config.json')['known_elsewhere'] == 'skse'
    again = launch()

    async def second():
        again.paste_links(MOD_A)
        await again.until(lambda: again.text_starting('Real Name'))
    again.run(second)
    assert not again.text_starting('Вне Nexus, проверьте сами')
    assert again.text_starting('Вне Nexus: 1'), 'The row of the mod still says so, quietly'


def test_automatic_mode_adds_what_is_needed_by_itself(launch, monkeypatch):
    tell_nexus(monkeypatch, LIBRARY, VR_ONLY)
    h = launch(config={'requirements_mode': 'auto', 'browser_paired': True})

    async def scenario():
        h.paste_links(MOD_A)
        await h.until(lambda: h.text_starting('Address Library'))
    h.run(scenario)
    assert h.text_starting('2 найденных модов')
    assert not h.text_starting('VR Library'), 'Only what everyone needs'


def test_mode_that_shows_nothing(launch, monkeypatch):
    tell_nexus(monkeypatch, LIBRARY)
    h = launch(config={'requirements_mode': 'off', 'browser_paired': True})

    async def scenario():
        h.paste_links(MOD_A)
        await h.until(lambda: h.text_starting('Real Name'))
    h.run(scenario)
    assert not h.text_starting('Моды из списка требуют') and not h.text_starting('Не хватает')


def test_what_mo2_or_vortex_already_has_is_not_asked_for(launch, monkeypatch):
    tell_nexus(monkeypatch, LIBRARY)
    monkeypatch.setattr(module, 'installed_mods', lambda *a: {(GAME, ADDRESS): 'Vortex'})
    h = launch()

    async def scenario():
        h.paste_links(MOD_A)
        await h.until(lambda: h.text_starting('Требования есть'))
        next(c for c in h.tree() if isinstance(c, ft.Container) and c.tooltip == 'Показать требования мода').on_click(None)
        await h.until(lambda: h.text_starting('Есть в Vortex'))
    h.run(scenario)
    assert not h.text_starting('Моды из списка требуют')


def test_opened_requirements_offer_adding_one_by_one(launch, monkeypatch):
    tell_nexus(monkeypatch, LIBRARY, VR_ONLY, SKSE)
    h = launch()

    async def scenario():
        h.paste_links(MOD_A)
        await h.until(lambda: h.text_starting('Не хватает: 1'))
        next(c for c in h.tree() if isinstance(c, ft.Container) and c.tooltip == 'Показать требования мода').on_click(None)
        await h.until(lambda: h.text_starting('Only for VR users'))
        assert h.text_starting('Вне Nexus: проверьте сами')
        adds = [c for c in h.tree() if isinstance(c, ft.TextButton) and c.content == 'Добавить']
        assert len(adds) == 2, 'SKSE is not on Nexus: nothing to add'
        adds[0].on_click(None)
        await h.until(lambda: h.text_starting('2 найденных модов'))
    h.run(scenario)


def test_plan_append_changes_nothing_else():
    plan = ModPlan()
    first = plan.replace([{'url': MOD_A}])
    plan.update(first[0], selected=False, target='mo2')
    fresh = plan.append([{'url': MOD_A}, {'url': f'https://www.nexusmods.com/{GAME}/mods/5', 'title': 'New'}], {(GAME, 5): 'vortex'})
    assert [item['mod_id'] for item in fresh] == [5]
    assert [item['mod_id'] for item in plan.items] == [SESSION_MOD, 5]
    assert plan.items[0] is first[0] and not plan.choices[(GAME, SESSION_MOD)].selected
    assert plan.choices[(GAME, SESSION_MOD)].target == 'mo2' and plan.choices[(GAME, 5)].target == 'vortex'


def test_rows_that_did_not_change_are_not_built_again(launch, monkeypatch):
    tell_nexus(monkeypatch)
    h = launch()

    async def scenario():
        h.paste_links(f'{MOD_A}\nhttps://www.nexusmods.com/{GAME}/mods/5')
        await h.until(lambda: h.text_starting('Real Name'))
        rows = lambda: [c for c in h.tree() if isinstance(c, ft.Container) and c.bgcolor in ('#1B2233', '#171B29')]
        before = rows()
        toggle = next(c for c in before[1].content.controls[0].controls if isinstance(c, ft.Checkbox))
        toggle.value = False
        toggle.on_change(type('E', (), {'control': toggle})())
        after = rows()
        assert after[0] is before[0], 'Nothing changed in this one'
        assert after[1] is not before[1], 'This one was changed'
    h.run(scenario)


# ------------------------------------------------------------------ updates
def seeded_update_names(monkeypatch, mod_id=SESSION_MOD, file_id=11):
    monkeypatch.setattr(module, 'mod_infos', lambda ids: {(GAME, mod_id): ModInfo(GAME, mod_id, 'Real Name', updated_at=time.time(), game_number=1704)
                                                          for _ in [1] if (GAME, mod_id) in ids})
    monkeypatch.setattr(module, 'mod_files', lambda *_: [
        {'file_id': 10, 'category': 'OLD_VERSION', 'date': 1, 'name': 'Main', 'version': '1.0', 'size': 5},
        {'file_id': file_id, 'category': 'MAIN', 'date': 2, 'name': 'Main', 'version': '2.0', 'size': 2 * 1024 ** 2}])


def test_new_version_is_offered_and_downloaded_on_request(launch, monkeypatch):
    seeded_update_names(monkeypatch)
    h = launch(config={'updates_auto': False, 'browser_paired': True})
    h.seed_archive(GAME, SESSION_MOD, 10, 'old.7z')

    async def scenario():
        open_tab(h, 'Загрузки')
        h.click('Проверить обновления')
        await h.until(lambda: h.text_starting('Вышли новые версии: 1'))
        assert h.text_starting('версия 2.0 · 2,0 МБ')
        assert not (await h.get('/auto/next')).get('job'), 'Nothing starts by itself'
        next(c for c in h.tree() if isinstance(c, ft.TextButton) and c.content == 'Скачать').on_click(None)
        job = await h.next_job()
        assert (job['mod_id'], job['file_id']) == (SESSION_MOD, 11), 'Exactly the new file, not a fresh copy of the old one'
        await h.report(job, status='url', url=CDN.replace('Mod-196600-1-0', 'new'), file_id=11)
        await h.until(lambda: h.archive_of(GAME, SESSION_MOD, 11))
        await h.until(lambda: not h.text_starting('Вышли новые версии'))
    h.run(scenario)


def test_nothing_new_says_so_only_when_asked(launch, monkeypatch):
    h = launch(config={'updates_auto': False, 'browser_paired': True})

    async def scenario():
        open_tab(h, 'Загрузки')
        h.click('Проверить обновления')
        await h.until(lambda: h.text_starting('Новых версий нет'))
    h.run(scenario)


def test_failed_check_tells_only_when_asked(launch, monkeypatch):
    def broken(ids):
        raise NexusError('Nexus не ответил')
    monkeypatch.setattr(module, 'mod_infos', broken)
    h = launch(config={'updates_auto': False, 'browser_paired': True})
    h.seed_archive(GAME, SESSION_MOD, 10, 'old.7z')

    async def scenario():
        open_tab(h, 'Загрузки')
        h.click('Проверить обновления')
        await h.until(lambda: h.text_starting('Не удалось проверить новые версии'))
    h.run(scenario)


@pytest.mark.parametrize('auto, stale, expected', [(True, True, 1), (True, False, 0), (False, True, 0)])
def test_automatic_check_at_most_once_a_day_and_only_if_wanted(launch, tmp_path, monkeypatch, auto, stale, expected):
    asked = []
    monkeypatch.setattr(module, 'find_updates', lambda *a: asked.append(1) or [])
    save_found(tmp_path / 'data' / 'queue' / 'updates.json', [], now=OLD_TIME if stale else time.time())
    h = launch(config={'updates_auto': auto, 'browser_paired': True})

    async def scenario():
        await h.until(lambda: len(asked) == expected, timeout=2) if expected else None
    h.run(scenario)
    time.sleep(0.1)
    assert len(asked) == expected


def test_offer_from_the_last_check_is_there_after_a_restart(launch, tmp_path):
    launch(config={'browser_paired': True}).seed_archive(GAME, SESSION_MOD, 10, 'old.7z')
    save_found(tmp_path / 'data' / 'queue' / 'updates.json', [Update(GAME, SESSION_MOD, 'Real Name', 11, '2.0')], now=time.time())
    h = launch()
    open_tab(h, 'Загрузки')
    assert h.text_starting('Вышли новые версии: 1')


def test_offer_goes_with_the_mod_and_does_not_come_back(launch, tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'to_recycle_bin', lambda path: __import__('shutil').rmtree(path))
    place = tmp_path / 'data' / 'queue' / 'updates.json'
    h = launch(config={'browser_paired': True})
    h.seed_archive(GAME, SESSION_MOD, 10, 'old.7z')
    save_found(place, [Update(GAME, SESSION_MOD, 'Real Name', 11, '2.0')], now=time.time())
    again = launch()
    open_tab(again, 'Загрузки')
    assert again.text_starting('Вышли новые версии: 1')
    icon_button(again, 'Удалить сессию')[0].on_click(None)
    text_button(again, 'В Корзину').on_click(None)
    assert not again.text_starting('Вышли новые версии')
    assert json.loads(place.read_text(encoding='utf-8'))['found'] == []


# ------------------------------------------------------------------ deleting
def test_file_is_deleted_only_after_confirmation_and_to_the_bin(launch, monkeypatch):
    discarded = []

    def to_bin(path):
        discarded.append(path)
        path.unlink()
    monkeypatch.setattr(module, 'to_recycle_bin', to_bin)
    h = launch(config={'browser_paired': True})
    path = h.seed_archive(GAME, SESSION_MOD, 10, 'one.7z')
    h.seed_archive(GAME, SESSION_MOD, 11, 'two.7z')
    open_tab(h, 'Загрузки')
    for row in [c for c in h.tree() if isinstance(c, ft.Container) and c.on_click and isinstance(c.content, ft.Row)]:
        if any(isinstance(x, ft.Icon) and x.icon == ft.Icons.CHEVRON_RIGHT for x in row.content.controls):
            row.on_click(None)
            break
    button = next(c for c in icon_button(h, 'Удалить файл') if c.on_click.__closure__ is not None and 'one.7z' in str(c.on_click.__defaults__))
    button.on_click(None)
    assert path.exists() and h.text_starting('Удалить этот файл?')
    text_button(h, 'Оставить').on_click(None)
    assert path.exists() and not h.text_starting('Удалить этот файл?')
    button = next(c for c in icon_button(h, 'Удалить файл') if 'one.7z' in str(c.on_click.__defaults__))
    button.on_click(None)
    text_button(h, 'В Корзину').on_click(None)
    assert discarded == [path] and not path.exists() and h.text_starting('Удалено в Корзину')
    assert 'two.7z' in texts(h)


def test_whole_session_goes_at_once(launch, monkeypatch):
    discarded = []
    monkeypatch.setattr(module, 'to_recycle_bin', lambda path: (discarded.append(path), __import__('shutil').rmtree(path)))
    h = launch(config={'browser_paired': True})
    path = h.seed_archive(GAME, SESSION_MOD, 10, 'one.7z')
    open_tab(h, 'Загрузки')
    icon_button(h, 'Удалить сессию')[0].on_click(None)
    assert h.text_starting('Удалить всю сессию: 1 файл')
    text_button(h, 'В Корзину').on_click(None)
    assert discarded == [path.parent] and not path.parent.exists()
    assert h.text_starting('Здесь появятся скачанные файлы')


def test_folder_of_a_running_download_is_not_deleted(launch, monkeypatch):
    monkeypatch.setattr(module, 'to_recycle_bin', lambda path: pytest.fail('Files are still arriving there'))
    h = launch(config={'browser_paired': True})
    source = h.home / 'Downloads' / 'done.7z'
    source.parent.mkdir()
    source.write_bytes(b'7z')

    async def scenario():
        h.paste_links(f'{MOD_A}\nhttps://www.nexusmods.com/{GAME}/mods/5')
        h.click('Скачать выбранные')
        first = await h.next_job()
        await h.report(first, status='file', path=str(source), file_id=1, filename='done.7z')
        await h.until(lambda: h.archive_named('done.7z'))
        open_tab(h, 'Загрузки')
        await h.until(lambda: icon_button(h, 'Удалить сессию'))
        icon_button(h, 'Удалить сессию')[0].on_click(None)
        text_button(h, 'В Корзину').on_click(None)
        assert h.text_starting('Эта папка сейчас используется загрузкой')
    h.run(scenario)
    assert h.archive_named('done.7z')


def test_refused_deletion_is_reported(launch, monkeypatch):
    def refuses(_):
        raise OSError('in use')
    monkeypatch.setattr(module, 'to_recycle_bin', refuses)
    h = launch(config={'browser_paired': True})
    path = h.seed_archive(GAME, SESSION_MOD, 10, 'one.7z')
    open_tab(h, 'Загрузки')
    icon_button(h, 'Удалить файл')[0].on_click(None)
    text_button(h, 'В Корзину').on_click(None)
    assert path.exists() and h.text_starting('Не удалось удалить: in use')


# ------------------------------------------------------------------ room on the disk and messages
def test_short_of_room_warns_and_the_download_goes_on(launch, monkeypatch, tmp_path):
    tell_nexus(monkeypatch)
    told, needed = [], []
    monkeypatch.setattr(module, 'notify', lambda *a, **k: told.append(a[:2]))

    def short(folders, amount=0):
        needed.append(amount)
        return [(tmp_path, 100 * 1024 ** 2)]
    monkeypatch.setattr(module, 'shortages', short)
    h = launch()

    async def scenario():
        h.paste_links(MOD_A)
        await h.until(lambda: h.text_starting('Real Name'))
        h.click('Скачать выбранные')
        await h.until(lambda: h.text_starting('Мало места на диске'))
        assert holder_of(h, 'Мало места на диске').visible
        job = await h.next_job()
        assert job['mod_id'] == SESSION_MOD
    h.run(scenario)
    assert needed[0] == 1024 * 1024, 'The size told by Nexus counts even without a key'
    assert 'файлы займут примерно 1,0 МБ' in h.text_starting('Мало места на диске')
    assert [title for title, _ in told].count('Мало места на диске') == 1


def test_finished_download_and_a_needed_action_send_a_message(launch, monkeypatch):
    told = []
    monkeypatch.setattr(module, 'notify', lambda *a, **k: told.append(a[:2]))
    h = launch()

    async def scenario():
        h.paste_links(MOD_A)
        h.click('Скачать выбранные')
        job = await h.next_job()
        await h.report(job, status='url', url=CDN, file_id=1)
        await h.until(h.finished)
    h.run(scenario)
    assert ('Загрузка закончена', 'Готово: скачано и обработано 1 из 1 файлов.') in told


def test_messages_can_be_turned_off(launch, monkeypatch):
    told = []
    monkeypatch.setattr(module, 'notify', lambda *a, **k: told.append(a))
    h = launch(config={'notify_enabled': False, 'browser_paired': True})

    async def scenario():
        h.paste_links(MOD_A)
        h.click('Скачать выбранные')
        job = await h.next_job()
        await h.report(job, status='url', url=CDN, file_id=1)
        await h.until(h.finished)
    h.run(scenario)
    assert told == []


# ------------------------------------------------------------------ automatic list and settings
def test_open_tabs_become_the_list_once_when_a_browser_connects(launch, monkeypatch):
    monkeypatch.setattr(module, 'AUTO_SCAN', True)
    h = launch()

    async def scenario():
        await h.snapshot([{'url': MOD_A, 'title': 'From a tab'}])
        await h.until(lambda: h.text_starting('From a tab'))
        await h.snapshot([{'url': f'https://www.nexusmods.com/{GAME}/mods/5', 'title': 'Opened later'}])
        await asyncio_pause()
    h.run(scenario)
    assert h.text_starting('From a tab') and not h.text_starting('Opened later'), 'Later the person decides when the list changes'


async def asyncio_pause():
    import asyncio
    await asyncio.sleep(0.7)


def test_automatic_list_can_be_turned_off(launch, monkeypatch):
    monkeypatch.setattr(module, 'AUTO_SCAN', True)
    h = launch(config={'auto_scan': False})

    async def scenario():
        await h.snapshot([{'url': MOD_A, 'title': 'From a tab'}])
        await asyncio_pause()
    h.run(scenario)
    assert not h.text_starting('From a tab')


def test_new_settings_are_kept(launch, tmp_path):
    h = launch()
    open_tab(h, 'Настройки')
    for control in h.tree():
        if isinstance(control, ft.Checkbox) and control.label and control.label.startswith(('Уведомлять', 'Сразу показывать', 'Проверять новые')):
            control.value = False
            control.on_change(None)
    choice = next(c for c in h.tree() if isinstance(c, ft.Dropdown) and c.label == 'Требования модов')
    choice.value = 'auto'
    choice.on_select(None)
    saved = load_settings(tmp_path / 'data' / 'config' / 'config.json')
    assert (saved['notify_enabled'], saved['auto_scan'], saved['updates_auto'], saved['requirements_mode']) == (False, False, False, 'auto')


def test_settings_hold_everything_once(launch):
    """The same control twice in one screen breaks the window."""
    h = launch()
    open_tab(h, 'Настройки')
    labels = [c.label for c in h.tree() if isinstance(c, (ft.TextField, ft.Dropdown)) and c.label]
    assert len(labels) == len(set(labels)), labels
    for wanted in ('Профиль MO2', 'Vortex.exe', 'Папка MO2 или путь к ModOrganizer.ini', 'Папка Data игры (для простой прямой установки)',
                   'Подготовленная папка staging', 'ID установки из журнала', 'Путь к сборке JSON', 'Язык / Language'):
        assert wanted in labels, wanted
    for wanted in ('Обнаружить MO2 / Vortex', 'Установить с резервными копиями', 'Удалить свою установку и восстановить файлы', 'Подключить Mod Manager Download к Windows'):
        assert text_button(h, wanted)


def test_nothing_is_lost_with_the_installs_tab(launch):
    h = launch()
    labels = [x.value for c in h.tree() if isinstance(c, ft.Container) and isinstance(c.content, ft.Row) and c.on_click
              for x in c.content.controls if isinstance(x, ft.Text)]
    assert [label for label in labels if label in ('Мои моды', 'Загрузки', 'Настройки', 'Журнал', 'Установки')] == ['Мои моды', 'Загрузки', 'Настройки', 'Журнал']


def test_assess_is_cheap_to_repeat():
    info = ModInfo(GAME, 1, requirements=[LIBRARY])
    assert assess(info, Present()) == assess(info, Present())


def test_paths_of_the_new_file_are_inside_the_data_folder(launch, tmp_path):
    launch()
    assert module.PATHS['updates'] == tmp_path / 'data' / 'queue' / 'updates.json'
    assert os.path.commonpath([module.PATHS['updates'], module.PATHS['base']]) == str(module.PATHS['base'])


def test_config_has_no_unknown_fields_left_behind(launch, tmp_path):
    launch()
    assert isinstance(json.loads((tmp_path / 'data' / 'config' / 'config.json').read_text(encoding='utf-8')), dict)

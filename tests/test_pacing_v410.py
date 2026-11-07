"""The pace of website downloads and the reactions to signals from the site."""
import json
from io import BytesIO
from threading import Event
from urllib.error import HTTPError

import pytest

from nexus_tab_installer.browser.auto_free import AutoFreeBatch, PAUSE_TEXT, REQUIRED_EXTENSION
from nexus_tab_installer.browser.pacing import (COOLDOWN_BASE, COOLDOWN_CAP, DAY, MAX_FAILURES, PRESETS,
                                                DownloadPacer, clock_text, duration_text)
from nexus_tab_installer.downloads import transfer
from nexus_tab_installer.nexus.api import NexusClient, NexusRateLimited

SOURCE = 'a' * 32


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def skip(self, seconds):
        self.now += seconds


def pace(clock, path=None, preset='careful'):
    return DownloadPacer(path, preset=preset, clock=clock)


def batch_with(pacer, clock, count=3):
    batch = AutoFreeBatch(clock=clock)
    batch.pacer = pacer
    batch.start([{'game': 'skyrim', 'mod_id': n, 'file_id': n} for n in range(1, count + 1)])
    return batch


# ------------------------------------------------------------------ the pace itself
def test_first_file_starts_at_once_and_the_next_waits_the_gap():
    clock = Clock()
    pacer = pace(clock)
    assert pacer.wait() == (0, '')
    pacer.record_start()
    assert pacer.wait() == (PRESETS['careful'].gap, 'gap')
    clock.skip(PRESETS['careful'].gap)
    assert pacer.wait() == (0, '')


def test_long_rest_after_a_run_of_files_then_a_new_run():
    clock = Clock()
    pacer = pace(clock)
    careful = PRESETS['careful']
    for _ in range(careful.batch):
        pacer.record_start()
        clock.skip(careful.gap)
    assert pacer.wait() == (careful.rest - careful.gap, 'rest')
    clock.skip(careful.rest)
    pacer.record_start()
    assert pacer.wait() == (careful.gap, 'gap')


def test_daily_ceiling_opens_when_the_oldest_file_leaves_the_window():
    clock = Clock()
    pacer = pace(clock)
    careful = PRESETS['careful']
    first = clock.now
    for _ in range(careful.daily):
        pacer.record_start()
        clock.skip(careful.gap)
    seconds, reason = pacer.wait()
    assert reason == 'daily'
    assert seconds == pytest.approx(first + DAY - clock.now)
    clock.skip(seconds)
    assert pacer.wait()[1] != 'daily'


def test_verification_page_slows_the_following_files_for_hours():
    clock = Clock()
    pacer = pace(clock)
    pacer.record_warning()
    pacer.record_start()
    assert pacer.wait() == (PRESETS['careful'].gap * 2, 'gap')
    clock.skip(6 * 3600 + 1)
    pacer.record_start()
    assert pacer.wait() == (PRESETS['careful'].gap, 'gap')


def test_failures_back_off_and_success_clears_them():
    clock = Clock()
    pacer = pace(clock)
    pacer.record_start()
    pacer.record_failure()
    assert pacer.wait() == (PRESETS['careful'].gap * 2, 'failures')
    pacer.record_failure()
    assert pacer.wait() == (PRESETS['careful'].gap * 4, 'failures')
    pacer.record_success()
    assert pacer.wait() == (PRESETS['careful'].gap, 'gap')


def test_cooldown_doubles_for_repeated_answers_and_has_a_limit():
    clock = Clock()
    pacer = pace(clock)
    assert pacer.cool_down() == COOLDOWN_BASE
    assert pacer.wait() == (COOLDOWN_BASE, 'cooldown')
    assert pacer.cool_down() == COOLDOWN_BASE * 2
    for _ in range(6):
        last = pacer.cool_down()
    assert last == COOLDOWN_CAP


def test_restart_does_not_reset_the_pace(tmp_path):
    clock = Clock()
    path = tmp_path / 'pace.json'
    first = pace(clock, path)
    first.record_start()
    first.cool_down()
    first.record_failure()
    second = pace(clock, path)
    assert second.wait() == (COOLDOWN_BASE, 'cooldown')
    clock.skip(COOLDOWN_BASE)
    assert second.wait() == (0, '')
    assert second.record_failure() == 2


@pytest.mark.parametrize('content', ['not json', '[]', json.dumps({'starts': 'x', 'cooldown_until': 'y', 'failures': -1}),
                                     json.dumps({'starts': [1e18, True, 'x'], 'cooldown_until': 1e18})])
def test_damaged_or_impossible_saved_values_are_ignored(tmp_path, content):
    path = tmp_path / 'pace.json'
    path.write_text(content, encoding='utf-8')
    assert pace(Clock(), path).wait() == (0, '')


def test_estimate_for_one_hundred_files_includes_the_rests():
    careful = PRESETS['careful']
    pacer = pace(Clock())
    rests = 99 // careful.batch
    assert pacer.estimate(100) == careful.gap * (99 - rests) + careful.rest * rests
    assert pacer.estimate(0) == 0


def test_unknown_preset_falls_back_to_careful():
    assert pace(Clock(), preset='unknown').preset is PRESETS['careful']
    assert PRESETS['moderate'].gap < PRESETS['careful'].gap


def test_human_texts():
    assert clock_text(42) == '0:42'
    assert clock_text(3725) == '1:02:05'
    assert duration_text(7560) == '2 ч 6 мин'
    assert duration_text(3600) == '1 ч'
    assert duration_text(10) == '1 мин'


def test_api_links_take_turns_without_waiting_for_the_first():
    clock = Clock()
    pacer = pace(clock)
    assert pacer.api_delay() == 0
    assert pacer.api_delay() == PRESETS['careful'].api_gap
    assert pacer.api_delay() == PRESETS['careful'].api_gap * 2


# ------------------------------------------------------------------ the queue obeys it
def test_queue_hands_out_files_only_at_the_pace():
    clock = Clock()
    batch = batch_with(pace(clock), clock)
    first = batch.claim(SOURCE)
    assert first
    batch.complete(SOURCE, first['id'], 'url', url='https://cdn.example.com/a.zip')
    assert batch.claim(SOURCE) is None
    assert batch.next_wait() == PRESETS['careful'].gap
    clock.skip(PRESETS['careful'].gap)
    assert batch.claim(SOURCE)


def test_a_lost_job_is_redelivered_without_counting_as_a_new_page():
    clock = Clock()
    batch = batch_with(pace(clock), clock)
    first = batch.claim(SOURCE)
    assert batch.claim(SOURCE)['id'] == first['id']


def test_rate_limit_answer_holds_the_queue_and_keeps_the_file():
    clock = Clock()
    batch = batch_with(pace(clock), clock)
    job = batch.claim(SOURCE)
    outcome = batch.complete(SOURCE, job['id'], 'failed', reason='rate_limited')
    assert outcome['kind'] == 'cooldown'
    assert batch.job_state(job['id']) == 'retry'
    assert batch.wait_status() == (COOLDOWN_BASE, 'cooldown')
    assert batch.claim(SOURCE) is None
    clock.skip(COOLDOWN_BASE)
    again = batch.claim(SOURCE)
    assert again['id'] == job['id']
    assert batch.state()['jobs'][0]['attempts'] == 1


def test_block_stops_the_queue_until_the_user_says_so():
    clock = Clock()
    batch = batch_with(pace(clock), clock)
    job = batch.claim(SOURCE)
    assert batch.complete(SOURCE, job['id'], 'failed', reason='blocked')['kind'] == 'paused'
    state = batch.state()
    assert state['paused'] and state['pause_reason'] == 'blocked'
    assert 'blocked' in PAUSE_TEXT
    clock.skip(DAY)
    assert batch.claim(SOURCE) is None
    assert batch.next_wait() > 90
    assert batch.resume()
    assert batch.claim(SOURCE)['id'] == job['id']


def test_missing_site_access_stops_the_queue():
    clock = Clock()
    batch = batch_with(pace(clock), clock)
    job = batch.claim(SOURCE)
    assert batch.complete(SOURCE, job['id'], 'failed', reason='no_site_access')['kind'] == 'paused'
    assert batch.state()['pause_reason'] == 'no_site_access'


def test_only_the_known_warning_can_stop_the_queue_after_a_good_link():
    clock = Clock()
    batch = batch_with(pace(clock), clock)
    job = batch.claim(SOURCE)
    assert batch.complete(SOURCE, job['id'], 'url', url='https://cdn.example.com/a.zip', warning='anything')['kind'] == 'url'
    assert not batch.state()['paused']
    clock.skip(DAY)
    job = batch.claim(SOURCE)
    assert batch.complete(SOURCE, job['id'], 'url', url='https://cdn.example.com/b.zip',
                          warning='ask_where_to_save')['kind'] == 'url'
    state = batch.state()
    assert state['paused'] and state['pause_reason'] == 'ask_where_to_save'
    assert 'ask_where_to_save' in PAUSE_TEXT


def test_a_run_of_site_failures_stops_the_queue():
    clock = Clock()
    pacer = pace(clock)
    batch = batch_with(pacer, clock, count=MAX_FAILURES)
    kinds = []
    for _ in range(MAX_FAILURES):
        clock.skip(DAY)
        job = batch.claim(SOURCE)
        kinds.append(batch.complete(SOURCE, job['id'], 'failed', reason='site_error')['kind'])
    assert set(kinds[:-1]) <= {'retry', 'failed'}
    assert batch.state()['pause_reason'] == 'site_failures'
    clock.skip(DAY)
    assert batch.claim(SOURCE) is None
    assert batch.resume()
    assert pacer.wait()[1] != 'failures'


def test_a_removed_file_is_not_a_site_failure():
    clock = Clock()
    pacer = pace(clock)
    batch = batch_with(pacer, clock)
    job = batch.claim(SOURCE)
    assert batch.complete(SOURCE, job['id'], 'failed', reason='file_unavailable')['kind'] == 'failed'
    assert pacer.wait()[1] == 'gap'


def test_success_clears_the_run_of_failures():
    clock = Clock()
    pacer = pace(clock)
    batch = batch_with(pacer, clock)
    job = batch.claim(SOURCE)
    batch.complete(SOURCE, job['id'], 'failed', reason='site_error')
    clock.skip(DAY)
    job = batch.claim(SOURCE)
    batch.complete(SOURCE, job['id'], 'url', url='https://cdn.example.com/a.zip')
    assert pacer.wait()[1] == 'gap'


def test_verification_is_counted_once_per_file():
    clock = Clock()
    pacer = pace(clock)
    batch = batch_with(pacer, clock)
    job = batch.claim(SOURCE)
    for _ in range(5):
        assert batch.progress(SOURCE, job['id'], 'verify')
    assert pacer.wait() == (PRESETS['careful'].gap * 2, 'gap')


def test_browser_busy_with_a_long_file_is_still_connected():
    """While it works on a file the extension does not ask for new ones: its heartbeats must count."""
    clock = Clock()
    batch = batch_with(None, clock, count=1)
    job = batch.claim(SOURCE, REQUIRED_EXTENSION)
    for _ in range(5):
        clock.skip(30)
        assert batch.progress(SOURCE, job['id'], 'heartbeat')
    assert batch.has_live_helper()
    assert not batch.outdated_helper()
    clock.skip(91)
    assert not batch.has_live_helper()


def test_old_extension_is_noticed():
    batch = AutoFreeBatch()
    batch.claim(SOURCE)
    assert batch.outdated_helper()
    batch.claim(SOURCE, '0.9.0')
    assert batch.outdated_helper() == '0.9.0'
    batch.claim(SOURCE, '<script>')
    assert batch.outdated_helper()
    batch.claim(SOURCE, REQUIRED_EXTENSION)
    assert not batch.outdated_helper()
    batch.claim(SOURCE, '10.0.0')
    assert not batch.outdated_helper()


# ------------------------------------------------------------------ HTTP 429
def too_many(retry_after=None):
    headers = {'Retry-After': retry_after} if retry_after else {}
    return HTTPError('https://cdn.example.com/a.zip', 429, 'Too Many', headers, BytesIO(b''))


class Reply(BytesIO):
    status = 200
    headers = {'Content-Length': '3', 'Content-Type': 'application/zip'}

    def geturl(self):
        return 'https://cdn.example.com/a.zip'

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def test_cdn_429_waits_as_long_as_the_server_asks(tmp_path):
    answers = [too_many('120'), Reply(b'abc')]
    def opener(_request, timeout):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    waits = []
    saved = transfer.download('https://cdn.example.com/a.zip', tmp_path, 'a.zip', opener=opener, sleeper=waits.append)
    assert saved.read_bytes() == b'abc'
    assert waits == [120]


def test_cdn_429_without_a_hint_waits_longer_each_time_then_gives_up(tmp_path):
    def opener(_request, timeout):
        raise too_many()
    waits = []
    with pytest.raises(transfer.RateLimited):
        transfer.download('https://cdn.example.com/a.zip', tmp_path, 'a.zip', opener=opener, sleeper=waits.append)
    assert waits == [30, 60]


def test_waiting_for_the_cdn_can_be_cancelled(tmp_path):
    cancel = Event()
    cancel.set()
    def opener(_request, timeout):
        raise too_many('900')
    with pytest.raises(transfer.DownloadCancelled):
        transfer.download('https://cdn.example.com/a.zip', tmp_path, 'a.zip', cancel=cancel, opener=opener)


def test_unreasonable_retry_after_is_limited():
    assert transfer.retry_after_seconds({'Retry-After': '99999'}, 5) == transfer.MAX_RETRY_AFTER
    assert transfer.retry_after_seconds({'Retry-After': 'soon'}, 5) == 5


def test_api_429_is_a_separate_error(monkeypatch):
    def refuse(_request, timeout):
        raise too_many()
    monkeypatch.setattr('nexus_tab_installer.nexus.api.urlopen', refuse)
    monkeypatch.setattr(NexusClient, 'min_interval', 0)
    with pytest.raises(NexusRateLimited):
        NexusClient('key').files('skyrim', 1)


def test_api_requests_take_turns(monkeypatch):
    slept = []
    now = {'value': 1000.0}
    monkeypatch.setattr('nexus_tab_installer.nexus.api.time.monotonic', lambda: now['value'])
    monkeypatch.setattr('nexus_tab_installer.nexus.api.time.sleep', slept.append)
    monkeypatch.setattr(NexusClient, '_next_turn', 0.0)
    client = NexusClient('key')
    client._wait_for_turn()
    client._wait_for_turn()
    client._wait_for_turn()
    assert slept == [NexusClient.min_interval, NexusClient.min_interval * 2]

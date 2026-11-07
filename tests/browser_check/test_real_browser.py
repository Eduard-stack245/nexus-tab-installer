"""The real extension in a real browser against a local imitation of the site.

Started only on request, because it opens a browser:

    set NEXUS_BROWSER=edge        (chrome, opera-gx, firefox)
    py -3 -m pytest tests/browser_check -v

NEXUS_BROWSER_PATH=<path to exe> uses a browser that is not installed.
NEXUS_BROWSER_VISIBLE=1 shows real windows instead of running headless.
NEXUS_BROWSER_PROMPT=1 turns on "always ask where to save" in the temporary profile.

The browser gets a temporary profile and the Nexus addresses are mapped to the local
imitation, so neither the real site nor your browser profile is touched. The desktop
side is real: the same HTTP server and job queue as in the program.
"""
import json
import os
import queue
import threading
import time
from pathlib import Path

import pytest

from fake_nexus import ARCHIVE, CDN_HOST, FakeNexus
from launchers import close_browser, launch_browser
from nexus_tab_installer.browser.auto_free import AutoFreeBatch, REQUIRED_EXTENSION
from nexus_tab_installer.browser.import_server import ImportServer, server_factory
from nexus_tab_installer.browser.pacing import DownloadPacer, PacePreset, PRESETS
from nexus_tab_installer.browser.pairing import PairingRequests
from nexus_tab_installer.browser.tab_snapshots import TabSnapshots
from nexus_tab_installer.core.trace import safe_event

ROOT = Path(__file__).resolve().parents[2]
BROWSER = os.environ.get('NEXUS_BROWSER', '')
GAME = 'skyrim'
OPEN_TAB = 321

pytestmark = pytest.mark.skipif(not BROWSER, reason='Set NEXUS_BROWSER=edge to run in a real browser')


class AlwaysBusy(AutoFreeBatch):
    """The extension polls every 10 s when idle and every 1.5 s when busy: keep tests quick."""
    def is_active(self):
        return True


class RecordingTrace:
    def __init__(self):
        self.events = []
        self.rejected = []

    def emit(self, event, **data):
        try:
            safe_event(event, data)
        except ValueError:
            self.rejected.append(event)
        self.events.append((event, data))

    def steps(self, job_id):
        return [d.get('step') for e, d in self.events if e == 'job.progress' and d.get('job_id') == job_id]

    def count(self, event):
        return sum(1 for e, _ in self.events if e == event)


class Rig:
    def __init__(self, base):
        self.base = Path(base)
        self.site = FakeNexus()
        self.incoming = queue.Queue()
        self.trace = RecordingTrace()
        self.batch = AlwaysBusy(trace=self.trace)
        self.snapshots = TabSnapshots()
        self.pairing = PairingRequests(token='browser-check-token')
        self.outcomes = []
        self.server = None
        self.browser = None
        self.stopping = threading.Event()
        self.downloads = self.base / 'downloads'
        self.downloads.mkdir()

    def listen(self, token, port, pairing, batch):
        handler = server_factory(token, self.incoming, self.snapshots, pairing, batch, self.trace)
        server = ImportServer(('127.0.0.1', port), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def start(self):
        site_port = self.site.start()
        self.server = self.listen('browser-check-token', 0, self.pairing, self.batch)
        threading.Thread(target=self._approve_pairing, daemon=True).start()
        tab = f'https://www.nexusmods.com/{GAME}/mods/{OPEN_TAB}?tab=files&file_id=77'
        self.browser = launch_browser(BROWSER, self.base, site_port, self.server.server_address[1],
                                      self.downloads, tab)
        # Opera GX loads its own pages first on a clean profile, which takes a while.
        self.wait(self.batch.has_live_helper, 150, 'The extension never asked the program for work')

    def other_copy_holds_the_port(self, seconds):
        """Another copy of the program, with its own key, answers on the same port."""
        port = self.server.server_address[1]
        self.server.shutdown()
        self.server.server_close()
        pairing = PairingRequests(token='key-of-another-copy')
        other = self.listen('key-of-another-copy', port, pairing, AlwaysBusy())
        requested = []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            requested = requested or pairing.pending()
            time.sleep(0.2)
        other.shutdown()
        other.server_close()
        self.server = self.listen('browser-check-token', port, self.pairing, self.batch)
        return requested

    def seconds_to_connect_after_approval(self):
        """A person presses "Allow" in another copy: how long until the extension works with it?"""
        port = self.server.server_address[1]
        self.server.shutdown()
        self.server.server_close()
        pairing = PairingRequests(token='key-of-a-new-copy')
        batch = AlwaysBusy()
        other = self.listen('key-of-a-new-copy', port, pairing, batch)
        try:
            self.wait(pairing.pending, 45, 'The extension did not ask the new copy for access')
            approved_at = time.monotonic()
            pairing.approve(pairing.pending()[0]['request_id'])
            self.wait(batch.has_live_helper, 45, 'The extension did not start working after the approval')
            return time.monotonic() - approved_at
        finally:
            other.shutdown()
            other.server_close()
            self.server = self.listen('browser-check-token', port, self.pairing, self.batch)

    def stop(self):
        self.stopping.set()
        close_browser(self.base / 'profile')
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.site.stop()

    def _approve_pairing(self):
        while not self.stopping.is_set():
            for request in self.pairing.pending():
                self.pairing.approve(request['request_id'])
            time.sleep(0.2)

    def wait(self, condition, timeout, message):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(0.1)
        raise AssertionError(message + '. Events: ' + str([e for e, _ in self.trace.events[-25:]]))

    def drain(self):
        while True:
            try:
                value = self.incoming.get_nowait()
            except queue.Empty:
                return
            if isinstance(value, tuple) and value[0] == 'auto_outcome':
                self.outcomes.append(value[1])

    def outcome(self, mod_id, timeout=75):
        """The next result of a job of this mod."""
        def ready():
            self.drain()
            for entry in self.outcomes:
                if entry['job']['mod_id'] == mod_id:
                    self.outcomes.remove(entry)
                    return entry
            return None
        return self.wait(ready, timeout, f'No result for mod {mod_id}')

    def run(self, mod_id, scenario, file_id=None, transfer='app'):
        self.site.scenarios[mod_id] = scenario
        self.batch.add([{'game': GAME, 'mod_id': mod_id,
                         'file_id': mod_id * 10 if file_id is None else file_id, 'transfer': transfer}])
        return self.outcome(mod_id)

    def reset(self):
        self.batch.resume()
        self.batch.stop()
        self.batch.pacer = None
        self.drain()
        self.outcomes.clear()
        for path in self.downloads.iterdir():
            path.unlink(missing_ok=True)


@pytest.fixture(scope='module')
def rig(tmp_path_factory):
    rig = Rig(tmp_path_factory.mktemp('browser'))
    try:
        rig.start()
        yield rig
    finally:
        rig.stop()
    assert not rig.trace.rejected, f'Events missing from the allowlist: {set(rig.trace.rejected)}'


@pytest.fixture
def site(rig):
    rig.reset()
    yield rig
    rig.reset()


def link_of(outcome):
    assert outcome['kind'] == 'url', outcome
    return outcome['url']


def no_copy_left(rig):
    """The program downloads the file: the browser must not keep its own copy."""
    time.sleep(1)
    assert sorted(p.name for p in rig.downloads.iterdir()) == []


# ------------------------------------------------------------------ connection
def test_extension_reports_its_version(rig):
    assert not rig.batch.outdated_helper()
    assert REQUIRED_EXTENSION == json.loads((ROOT / 'extensions/chromium/manifest.json').read_text(encoding='utf-8'))['version']


def test_open_tab_with_its_file_reaches_the_program(rig):
    def seen():
        return [m for m in rig.snapshots.collect() if m.get('mod_id') == OPEN_TAB]
    mods = rig.wait(seen, 45, 'The open tab was not reported')
    assert mods[0].get('file_ids') == [77]
    assert list(rig.snapshots.summary()) == [BROWSER], 'The extension must name its browser correctly'


def test_another_copy_of_the_program_does_not_break_the_connection(site):
    # The alarm syncs tabs at least every 30 seconds: that is when the extension asks for access.
    requested = site.other_copy_holds_the_port(35)
    assert requested, 'The other copy should have been asked for access'
    link_of(site.run(1050, 'free'))
    assert not site.pairing.pending(), 'The real program should not have to approve anything again'


def test_extension_starts_working_soon_after_the_approval(site):
    assert site.seconds_to_connect_after_approval() < 15, 'It should not wait for the 30 second alarm'
    # The extension now holds the key of that copy: the real program approves it again.
    site.wait(lambda: site.batch.live_helpers(max_age=5), 60, 'The extension did not come back')


# ------------------------------------------------------------------ the normal path
def test_free_file_slow_download(site):
    url = link_of(site.run(1001, 'free'))
    assert url.startswith(f'https://{CDN_HOST}/d7/97/69/1001/')
    assert site.site.clicks[1001] == 1
    no_copy_left(site)


def test_file_over_500_mb_chooses_standard_download(site):
    link_of(site.run(1002, 'large'))
    assert site.site.clicks[1002] == 1
    no_copy_left(site)


def test_premium_account_start_download(site):
    link_of(site.run(1003, 'premium'))
    assert site.site.clicks[1003] == 1


def test_no_api_key_takes_the_newest_main_file(site):
    outcome = site.run(1004, 'free', file_id=0)
    assert '/1004/' in link_of(outcome)
    assert outcome['job']['file_id'] == 502


def test_file_name_without_the_mod_id_is_trusted_after_our_click(site):
    link_of(site.run(1005, 'name_without_id'))
    no_copy_left(site)


@pytest.mark.skipif(bool(os.environ.get('NEXUS_BROWSER_PROMPT')), reason='The browser waits for a person in its dialog')
def test_browser_saves_the_file_when_asked_to(site):
    outcome = site.run(1006, 'free', transfer='browser')
    assert outcome['kind'] == 'file', outcome
    saved = Path(outcome['path'])
    assert saved.parent == site.downloads
    assert saved.read_bytes() == ARCHIVE


def test_only_the_site_link_when_no_download_starts(site):
    link_of(site.run(1007, 'manual_only'))
    assert site.site.clicks[1007] == 1
    # Firefox cannot tell this from an open "save as" dialog, so it asks the user to check.
    assert site.batch.state()['paused'] == (BROWSER == 'firefox')


@pytest.mark.skipif(not (BROWSER == 'firefox' and os.environ.get('NEXUS_BROWSER_PROMPT')),
                    reason='Only Firefox hides downloads behind its save dialog')
def test_firefox_save_dialog_stops_the_queue_after_the_first_file(site):
    link_of(site.run(1009, 'free'))
    assert site.batch.state()['pause_reason'] == 'ask_where_to_save'
    site.site.scenarios[1010] = 'free'
    site.batch.add([{'game': GAME, 'mod_id': 1010, 'file_id': 10100}])
    time.sleep(8)
    assert 1010 not in site.site.visits, 'A second dialog must not open'


def test_mod_text_about_limits_is_not_a_refusal(site):
    """Every page of the imitation carries such a description; this one has no panel yet."""
    outcome = site.run(1008, 'free', file_id=0)
    assert outcome['kind'] == 'url', outcome


def test_mod_text_on_a_page_without_a_panel_is_not_a_refusal(site):
    """A real file page has little text, so "short page" must not mean "error page"."""
    site.batch.pacer = DownloadPacer(None)
    outcome = site.run(1011, 'no_panel')
    assert outcome['kind'] == 'retry' and outcome['job']['reason'] == 'no_button', outcome
    assert site.batch.wait_status()[1] != 'cooldown'


# ------------------------------------------------------------------ waiting for the user
def test_login_page_then_download_continues(site):
    site.site.logged_in = False
    url = link_of(site.run(1101, 'logged_out'))
    assert '/1101/' in url
    assert site.site.visits[1101] >= 2


def test_verification_page_then_download_continues_slower(site):
    site.batch.pacer = DownloadPacer(None, preset='moderate')
    outcome = site.run(1102, 'verify')
    link_of(outcome)
    assert 'verify' in site.trace.steps(outcome['job']['id'])
    assert site.batch.pacer.wait()[0] > PRESETS['moderate'].gap, 'A verification page must slow the next files'


# ------------------------------------------------------------------ the site refuses
def test_rate_limit_page_cools_down_and_keeps_the_file(site):
    site.batch.pacer = DownloadPacer(None)
    outcome = site.run(1201, 'rate_limited')
    assert outcome['kind'] == 'cooldown'
    assert site.batch.wait_status()[1] == 'cooldown'
    assert site.site.visits[1201] == 1, 'The site must not be asked again during the pause'
    time.sleep(4)
    assert site.site.visits[1201] == 1


def test_limit_message_in_the_panel_cools_down(site):
    site.batch.pacer = DownloadPacer(None)
    assert site.run(1202, 'panel_limit')['kind'] == 'cooldown'
    assert site.batch.wait_status()[1] == 'cooldown'


def test_block_page_stops_the_queue(site):
    outcome = site.run(1203, 'blocked')
    assert outcome['kind'] == 'paused'
    assert site.batch.state()['pause_reason'] == 'blocked'
    time.sleep(4)
    assert site.site.visits[1203] == 1


def test_site_error_is_retried_in_a_new_tab(site):
    assert site.run(1301, 'error_once')['kind'] == 'retry'
    link_of(site.outcome(1301))
    assert site.site.visits[1301] == 2


def test_removed_file_fails_without_a_retry(site):
    outcome = site.run(1302, 'removed')
    assert outcome['kind'] == 'failed'
    assert outcome['job']['reason'] == 'file_unavailable'
    assert site.site.visits[1302] == 1


# ------------------------------------------------------------------ the pace
def test_files_follow_each_other_at_the_set_pace(site, monkeypatch):
    monkeypatch.setitem(PRESETS, 'moderate', PacePreset('test', gap=6, batch=100, rest=600, daily=1000, api_gap=0))
    site.batch.pacer = DownloadPacer(None, preset='moderate')
    mods = (1401, 1402, 1403)
    for mod in mods:
        site.site.scenarios[mod] = 'free'
    site.batch.add([{'game': GAME, 'mod_id': mod, 'file_id': mod * 10} for mod in mods])
    for mod in mods:
        link_of(site.outcome(mod))
    opened = [moment for moment, mod in site.site.page_times if mod in mods]
    gaps = [later - earlier for earlier, later in zip(opened, opened[1:])]
    assert len(gaps) == 2
    assert all(gap >= 5.5 for gap in gaps), gaps


def test_the_job_tab_is_found_behind_a_browser_start_page(site):
    """Opera GX puts its start page first in every new window. This scenario passes there only
    if the extension finds the page of the file by its address and brings it to the front."""
    link_of(site.run(1502, 'free'))
    assert site.site.clicks[1502] == 1


def test_long_pause_closes_the_helper_window(site, monkeypatch):
    monkeypatch.setitem(PRESETS, 'moderate', PacePreset('test', gap=200, batch=100, rest=600, daily=1000, api_gap=0))
    site.batch.pacer = DownloadPacer(None, preset='moderate')
    before = site.trace.count('extension.window.closed')
    link_of(site.run(1501, 'free'))
    site.wait(lambda: site.trace.count('extension.window.closed') > before, 30,
              'The helper window stayed open during a long pause')

"""Start a browser with the real extension, a temporary profile and the imitation of the site.

Chromium browsers map the Nexus addresses with a command line flag. Firefox has no such
flag, so it goes through a small local proxy that lets only those addresses through, and
it gets the extension over Marionette, its own remote control protocol.
"""
import itertools
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import psutil
import pytest

from fake_nexus import HOSTS
from nexus_tab_installer.platform.browser_setup import candidate_executables

ROOT = Path(__file__).resolve().parents[2]
VISIBLE = bool(os.environ.get('NEXUS_BROWSER_VISIBLE'))
ASK_WHERE_TO_SAVE = bool(os.environ.get('NEXUS_BROWSER_PROMPT'))


def executable(browser):
    """NEXUS_BROWSER_PATH points to a browser that is not installed, such as Chrome for Testing."""
    path = os.environ.get('NEXUS_BROWSER_PATH')
    if path:
        return path
    found = candidate_executables(browser)
    if not found:
        pytest.skip(f'{browser} is not installed')
    return found[0]


def prepared_extension(base, folder, app_port):
    """The same extension, except the port: a running copy of the program may hold the real one."""
    extension = base / 'extension'
    shutil.copytree(ROOT / 'extensions' / folder, extension)
    for name in ('manifest.json', 'background.js'):
        path = extension / name
        path.write_text(path.read_text(encoding='utf-8').replace('38471', str(app_port)), encoding='utf-8')
    return extension


def close_browser(profile):
    """Only this temporary profile. The started process may hand over to another one and
    exit, so its number proves nothing: the profile path in the command line does."""
    profile = str(profile)
    for process in psutil.process_iter(['cmdline']):
        try:
            if any(profile in part for part in (process.info['cmdline'] or [])):
                process.kill()
        except psutil.Error:
            pass


def free_port():
    with socket.create_server(('127.0.0.1', 0)) as probe:
        return probe.getsockname()[1]


def launch_browser(browser, base, site_port, app_port, downloads, tab_url):
    launch = launch_firefox if browser == 'firefox' else launch_chromium
    return launch(browser, base, site_port, app_port, downloads, tab_url)


# ------------------------------------------------------------------ Chrome, Edge, Opera GX
def open_tab_later(control_port, url):
    """Open a tab the way a person does: Chrome for Testing never loads one from its command line."""
    address = f'http://127.0.0.1:{control_port}/json/new?' + urllib.parse.quote(url, safe=':/?=')
    deadline = time.monotonic() + 60
    while True:
        try:
            urllib.request.urlopen(urllib.request.Request(address, method='PUT'), timeout=10).close()
            return
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


def launch_chromium(browser, base, site_port, app_port, downloads, tab_url):
    program = executable(browser)
    extension = prepared_extension(base, 'chromium', app_port)
    profile = base / 'profile'
    (profile / 'Default').mkdir(parents=True)
    (profile / 'Default' / 'Preferences').write_text(json.dumps(
        {'download': {'default_directory': str(downloads), 'prompt_for_download': ASK_WHERE_TO_SAVE}}), encoding='utf-8')
    rules = ', '.join(f'MAP {host} 127.0.0.1:{site_port}' for host in HOSTS) + ', EXCLUDE 127.0.0.1'
    control_port = free_port()
    arguments = [program, f'--remote-debugging-port={control_port}', f'--user-data-dir={profile}', f'--load-extension={extension}',
                 f'--disable-extensions-except={extension}', f'--host-resolver-rules={rules}',
                 '--ignore-certificate-errors', '--no-first-run', '--no-default-browser-check',
                 '--disable-sync', '--disable-features=DisableLoadExtensionCommandLineSwitch',
                 '--window-size=1280,900']
    if not VISIBLE:
        arguments.append('--headless=new')
    subprocess.Popen(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    open_tab_later(control_port, tab_url)


# ------------------------------------------------------------------ Firefox
class Tunnel:
    """An HTTPS proxy that reaches only the imitation: nothing leaves this computer."""

    def __init__(self, target_port):
        self.target_port = target_port
        self.listener = socket.create_server(('127.0.0.1', 0))
        self.port = self.listener.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client):
        try:
            request = b''
            while b'\r\n\r\n' not in request:
                chunk = client.recv(4096)
                if not chunk:
                    return
                request += chunk
            method, where = request.split(b'\r\n', 1)[0].decode('latin-1').split()[:2]
            if method != 'CONNECT' or where.rsplit(':', 1)[0] not in HOSTS:
                client.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n')
                return
            upstream = socket.create_connection(('127.0.0.1', self.target_port))
            client.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
            threading.Thread(target=self._pump, args=(upstream, client), daemon=True).start()
            self._pump(client, upstream)
        except OSError:
            pass
        finally:
            client.close()

    @staticmethod
    def _pump(source, target):
        try:
            while chunk := source.recv(65536):
                target.sendall(chunk)
        except OSError:
            pass
        finally:
            for side in (source, target):
                try:
                    side.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


class Marionette:
    """Messages are "<length>:<json>"; a command is [0, id, name, params]."""

    def __init__(self, port, timeout=60):
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.socket = socket.create_connection(('127.0.0.1', port), timeout=5)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        self.socket.settimeout(90)
        self.buffer = b''
        self.ids = itertools.count(1)
        self._read()  # the greeting

    def _read(self):
        while b':' not in self.buffer:
            self.buffer += self.socket.recv(65536)
        length, self.buffer = self.buffer.split(b':', 1)
        while len(self.buffer) < int(length):
            self.buffer += self.socket.recv(65536)
        body, self.buffer = self.buffer[:int(length)], self.buffer[int(length):]
        return json.loads(body)

    def call(self, name, params=None):
        message = json.dumps([0, next(self.ids), name, params or {}]).encode()
        self.socket.sendall(str(len(message)).encode() + b':' + message)
        _kind, _id, error, result = self._read()
        if error:
            raise RuntimeError(f'{name}: {error}')
        return result


def launch_firefox(browser, base, site_port, app_port, downloads, tab_url):
    program = executable(browser)
    extension = prepared_extension(base, 'firefox', app_port)
    tunnel = Tunnel(site_port)
    control_port = free_port()
    preferences = {
        'marionette.port': control_port,
        'network.proxy.type': 1,
        'network.proxy.ssl': '127.0.0.1', 'network.proxy.ssl_port': tunnel.port,
        'network.proxy.http': '127.0.0.1', 'network.proxy.http_port': tunnel.port,
        'network.proxy.no_proxies_on': '127.0.0.1',
        'browser.download.folderList': 2,
        'browser.download.dir': str(downloads),
        'browser.download.useDownloadDir': not ASK_WHERE_TO_SAVE,
        'browser.download.alwaysOpenPanel': False,
        'browser.download.always_ask_before_handling_new_types': False,
        'browser.shell.checkDefaultBrowser': False,
        'browser.aboutwelcome.enabled': False,
        'datareporting.policy.dataSubmissionEnabled': False,
        'toolkit.telemetry.enabled': False,
        'app.update.enabled': False,
        'browser.safebrowsing.malware.enabled': False,
        'browser.safebrowsing.phishing.enabled': False,
        'browser.safebrowsing.downloads.enabled': False,
        'network.captive-portal-service.enabled': False,
        'network.connectivity-service.enabled': False,
        'network.stricttransportsecurity.preloadlist': False,
        'dom.security.https_first': False,
    }
    profile = base / 'profile'
    profile.mkdir()
    (profile / 'user.js').write_text(
        ''.join(f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in preferences.items()), encoding='utf-8')
    arguments = [program, '--marionette', '--no-remote', '--profile', str(profile)]
    if not VISIBLE:
        arguments.append('--headless')
    subprocess.Popen(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    control = Marionette(control_port)
    control.call('WebDriver:NewSession', {'acceptInsecureCerts': True})
    control.call('Addon:Install', {'path': str(extension), 'temporary': True})
    control.call('WebDriver:Navigate', {'url': tab_url})
    return control, tunnel  # the session must stay open for the certificate exception to last

"""A local imitation of the Nexus download flow for tests in a real browser.

The real site must never be used for automated tests. This one copies what was
observed on nexusmods.com in November 2026: the panel lives in the shadow root of
<mod-file-download>, its <style> blocks are inside that root, files over 500 MB open
a choice in <mod-download-modal>, a countdown is followed by a POST that returns a
signed link, and the page then navigates to that link.

If Nexus changes its pages, this copy has to follow; the tests here prove the logic
of the extension, not that the real site still looks like this.
"""
from __future__ import annotations

import json
import ssl
import threading
import time
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
CDN_HOST = 'cdn.fake-nexus.test'
HOSTS = ('www.nexusmods.com', 'nexusmods.com', 'users.nexusmods.com', CDN_HOST)
ARCHIVE = b'PK\x03\x04' + b'\0' * 65536

# Words that must not trigger anything: they are in places the extension must not read.
TRAP_STYLE = '.x::after{content:"too many requests. you are being rate limited. sorry, you have been blocked"}'
TRAP_TEXT = ('This mod removes the frame rate limit. Have you reached the carry weight limit? '
             'Too many requests from players made me write it. Error 1015 is a Papyrus message. '
             'Sorry, you have been blocked by a guard in the game.')

PANEL = r"""
const CFG = JSON.parse(document.getElementById('scenario').textContent);
const STYLE = '<style>' + CFG.trapStyle + '</style>';
class ModFileDownload extends HTMLElement {
  connectedCallback() {
    this.root = this.attachShadow({mode: 'open'});
    this.initial();
  }
  draw(html) { this.root.innerHTML = STYLE + html; }
  initial() {
    if (CFG.scenario === 'logged_out' && !CFG.loggedIn) {
      const back = encodeURIComponent(location.href);
      this.draw('<p>You need an account to download</p><a href="https://users.nexusmods.com/auth/sign_in?redirect_url=' +
                back + '">Slow download</a>');
      return;
    }
    if (this.getAttribute('user-is-premium') === 'true') {
      this.draw('<button id="go">Start download</button>');
    } else {
      this.draw('<button id="go">Slow download</button><button>Mod Manager download</button>');
    }
    this.root.getElementById('go').addEventListener('click', () => this.clicked());
  }
  clicked() {
    fetch('/fake/clicked', {method: 'POST'});
    if (CFG.scenario === 'large') return this.modal();
    if (CFG.scenario === 'removed') return this.draw('<p>This file could no longer be accessed.</p>');
    if (CFG.scenario === 'panel_limit') return this.draw('<p>You have reached your daily download limit.</p>');
    if (CFG.scenario === 'error_once' && CFG.visit === 1) {
      return this.draw('<p>Your download could not be retrieved. Please try again.</p>');
    }
    this.premium = this.getAttribute('user-is-premium') === 'true';
    this.premium ? this.link() : this.countdown(Number(this.getAttribute('timeout-seconds')));
  }
  modal() {
    const modal = document.createElement('mod-download-modal');
    document.body.append(modal);
    const shadow = modal.attachShadow({mode: 'open'});
    shadow.innerHTML = STYLE + '<button id="std">Standard download</button><button>Resumable download</button>';
    shadow.getElementById('std').addEventListener('click', () => {
      modal.remove();
      this.countdown(Number(this.getAttribute('timeout-seconds')));
    });
  }
  countdown(left) {
    if (left <= 0) return this.link();
    this.draw('<p>Your download will start in ' + left + ' seconds...</p>');
    setTimeout(() => this.countdown(left - 1), 1000);
  }
  async link() {
    const reply = await fetch('/Core/Libs/Common/Managers/Downloads?GenerateDownloadUrl', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mod_id: CFG.modId, file_id: Number(this.getAttribute('file-id'))})});
    const {url} = await reply.json();
    this.draw('<p>Your download is starting</p><a id="manual">Start download manually</a>');
    this.root.getElementById('manual').href = url;
    if (CFG.scenario !== 'manual_only') window.location.href = url;
  }
}
customElements.define('mod-file-download', ModFileDownload);
"""


class FakeNexus:
    def __init__(self):
        self.scenarios = {}        # mod_id -> scenario name
        self.visits = {}           # mod_id -> how many times its page was opened
        self.clicks = {}           # mod_id -> POSTs of a generated link
        self.pressed = 0           # presses of the site's first download button
        self.logged_in = False
        self.page_times = []       # (time, mod_id) of every opened mod page
        self.fetched = []          # paths requested from the CDN
        self._server = None

    # ----- pages ----------------------------------------------------------
    def bare_page(self, status, title, body):
        return status, f'<!doctype html><title>{escape(title)}</title><body>{escape(body)}</body>'

    def mod_page(self, mod_id, file_id):
        scenario = self.scenarios.get(mod_id, 'free')
        visit = self.visits[mod_id] = self.visits.get(mod_id, 0) + 1
        self.page_times.append((time.monotonic(), mod_id))
        if scenario == 'verify' and visit == 1:
            return 200, ('<!doctype html><title>Just a moment...</title><div id="challenge-form"></div>'
                         '<script>setTimeout(() => location.reload(), 2500)</script>')
        if scenario == 'blocked':
            return self.bare_page(403, 'Attention Required! | Cloudflare', 'Sorry, you have been blocked')
        if scenario == 'rate_limited':
            return self.bare_page(429, 'Access denied', 'Error 1015. You are being rate limited.')
        config = json.dumps({'scenario': scenario, 'modId': mod_id, 'visit': visit, 'loggedIn': self.logged_in,
                             'trapStyle': TRAP_STYLE})
        panel = ''
        if file_id:
            premium = 'true' if scenario == 'premium' else 'false'
            panel = (f'<mod-file-download file-id="{file_id}" game-id="110" timeout-seconds="2" '
                     f'user-is-premium="{premium}"></mod-file-download>')
        listing = ('<div id="mod_files"><div id="file-container-main-files"><dl>'
                   '<dt data-id="501" data-date="100" data-size="10">Old</dt>'
                   '<dt data-id="502" data-date="200" data-size="20">New</dt>'
                   '</dl></div><div id="file-container-old-files"><dl>'
                   '<dt data-id="999" data-date="900" data-size="5">Older but later date</dt></dl></div></div>')
        # The layout of a real page: short text, #mainContent, and on a file page no #mod_files.
        listing = '' if file_id else listing
        panel = '' if scenario == 'no_panel' else panel
        return 200, (f'<!doctype html><title>Mod {mod_id}</title><body><div id="mainContent"><p>{TRAP_TEXT}</p>'
                     f'{listing}{panel}</div>'
                     f'<script type="application/json" id="scenario">{config}</script>'
                     f'<script>{PANEL}</script></body>')

    def link(self, mod_id, file_id):
        scenario = self.scenarios.get(mod_id, 'free')
        self.clicks[mod_id] = self.clicks.get(mod_id, 0) + 1
        # CDN folders are hex bytes of a file UUID, whatever digits they contain.
        name = f'Plain Name {file_id}.zip' if scenario == 'name_without_id' else f'Mod {mod_id} 1.0 2026.zip'
        return f'https://{CDN_HOST}/d7/97/69/{mod_id}/{name.replace(" ", "%20")}?token=abc&expires=1'

    # ----- server ---------------------------------------------------------
    def start(self):
        site = self

        class Handler(BaseHTTPRequestHandler):
            def reply(self, status, body, content_type='text/html; charset=utf-8', extra=()):
                data = body.encode('utf-8') if isinstance(body, str) else body
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(data)))
                for name, value in extra:
                    self.send_header(name, value)
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(data)

            def do_GET(self):
                host = (self.headers.get('Host') or '').split(':')[0]
                parts = urlsplit(self.path)
                query = parse_qs(parts.query)
                if host == CDN_HOST:
                    site.fetched.append(parts.path)
                    return self.reply(200, ARCHIVE, 'application/zip', [
                        ('Content-Disposition', 'attachment; filename="' + parts.path.rsplit('/', 1)[-1].replace('%20', ' ') + '"')])
                if host == 'users.nexusmods.com':
                    site.logged_in = True
                    back = json.dumps(query.get('redirect_url', [''])[0])
                    return self.reply(200, f'<!doctype html><title>Sign in</title><p>Sign in</p>'
                                           f'<script>setTimeout(() => {{ location.href = {back}; }}, 1500)</script>')
                bits = parts.path.strip('/').split('/')
                if len(bits) == 3 and bits[1] == 'mods' and bits[2].isdigit():
                    file_id = int(query.get('file_id', ['0'])[0] or 0)
                    return self.reply(*site.mod_page(int(bits[2]), file_id))
                return self.reply(404, '<!doctype html><title>Not found</title>')

            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length) if length else b''
                if self.path.startswith('/fake/clicked'):
                    site.pressed += 1
                    return self.reply(200, '{}', 'application/json')
                data = json.loads(body or b'{}')
                url = site.link(int(data.get('mod_id', 0)), int(data.get('file_id', 0)))
                return self.reply(200, json.dumps({'url': url}), 'application/json')

            def log_message(self, *_):
                pass

        class QuietServer(ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):
                pass  # the browser drops connections when a test closes it

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(*self._certificate())
        self._server = QuietServer(('127.0.0.1', 0), Handler)
        # The handshake runs in the request thread: a stuck client cannot hold the others.
        self._server.socket = context.wrap_socket(self._server.socket, server_side=True,
                                                  do_handshake_on_connect=False)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self._server.server_address[1]

    def _certificate(self):
        """A certificate made for this run only, so that no private key lies in the repository.
        Browsers here ignore certificate errors: it only has to exist for HTTPS to work at all."""
        import shutil
        import subprocess
        import tempfile
        git = shutil.which('git')
        openssl = shutil.which('openssl') or next((str(path) for path in (
            [Path(git).resolve().parents[1] / 'usr' / 'bin' / 'openssl.exe'] if git else []) if path.is_file()), None)
        if not openssl:
            raise RuntimeError('The browser check needs openssl: install Git for Windows or OpenSSL.')
        folder = Path(tempfile.mkdtemp(prefix='fake-nexus-'))
        self._certificate_folder = folder
        key, certificate = folder / 'key.pem', folder / 'certificate.pem'
        subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2', '-subj', '/CN=fake-nexus.test',
                        '-keyout', str(key), '-out', str(certificate), '-addext',
                        'subjectAltName=DNS:www.nexusmods.com,DNS:nexusmods.com,DNS:users.nexusmods.com,DNS:cdn.fake-nexus.test'],
                       check=True, capture_output=True, timeout=60)
        return str(certificate), str(key)

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        folder = getattr(self, '_certificate_folder', None)
        if folder:
            __import__('shutil').rmtree(folder, ignore_errors=True)

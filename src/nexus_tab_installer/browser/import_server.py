"""Loopback importer and explicitly approved extension pairing."""
import json
import secrets
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from nexus_tab_installer.core.links import normalize_entries
from nexus_tab_installer.browser.pairing import is_extension_origin
from nexus_tab_installer.nexus.nxm_inbox import submit_nxm

PORT = 38471
LIMIT = 512 * 1024


class ImportServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def server_factory(token, incoming, snapshots=None, pairing=None, auto_free=None, trace=None, language=None):
    class Handler(BaseHTTPRequestHandler):
        def do_OPTIONS(self):
            if self.path.startswith('/pair/') and not is_extension_origin(self.headers.get('Origin')):
                return self.reply(403, {'error':'Extension origin required'})
            self.send_response(204)
            self._cors()
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Nexus-Token, X-Nexus-Source, X-Nexus-Pairing, X-Nexus-Extension')
            self.end_headers()

        def _cors(self):
            origin = self.headers.get('Origin', '')
            if self.path.startswith('/pair/') or self.path.startswith('/health'):
                if is_extension_origin(origin):
                    self.send_header('Access-Control-Allow-Origin', origin)
                    self.send_header('Vary', 'Origin')
            else:
                self.send_header('Access-Control-Allow-Origin', '*')

        def read_payload(self):
            if self._body is None:
                raise ValueError('Invalid request size')
            return json.loads(self._body)

        def _drain_body(self):
            # Always consume the request body before answering: replying early
            # (e.g. 403) makes Windows abort the connection mid-request.
            self._body = None
            try:
                length = int(self.headers.get('Content-Length', '-1'))
            except ValueError:
                return
            if 0 <= length <= LIMIT:
                self._body = self.rfile.read(length)

        def do_POST(self):
            self._drain_body()
            if self.path == '/diagnostics/event':
                if trace is None or not secrets.compare_digest(self.headers.get('X-Nexus-Token', ''), token):
                    return self.reply(403, {'ok': False})
                try:
                    data = self.read_payload()
                    if not isinstance(data, dict) or not isinstance(data.get('event'), str):
                        raise ValueError('Bad telemetry')
                    details = data.get('data', {})
                    if not isinstance(details, dict):
                        raise ValueError('Bad telemetry')
                    trace.emit('extension.' + data['event'], **details)
                    return self.reply(200, {'ok': True})
                except (ValueError, TypeError, json.JSONDecodeError):
                    return self.reply(400, {'ok': False})
            if trace and self.path in ('/pair/request','/auto/complete','/snapshot'):
                trace.emit('server.request', endpoint=self.path.strip('/').replace('/', '.'), method='post')
            if self.path == '/pair/request':
                if pairing is None or not is_extension_origin(self.headers.get('Origin')):
                    return self.reply(403, {'error': 'Only installed browser extensions may request access'})
                try:
                    data = self.read_payload()
                    if not isinstance(data, dict):
                        raise ValueError('Invalid pairing request')
                    rid = pairing.request(data.get('browser'), self.headers['Origin'])
                    incoming.put(('pair_request', rid))
                    return self.reply(200, {'request_id': rid, 'state':'pending'})
                except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                    return self.reply(400, {'error': 'Invalid pairing request'})

            if self.path == '/auto/complete':
                if auto_free is None or not secrets.compare_digest(self.headers.get('X-Nexus-Token', ''), token):
                    return self.reply(403, {'error': 'Invalid token'})
                try:
                    data = self.read_payload()
                    if not isinstance(data, dict):
                        raise ValueError('Bad payload')
                    status = data.get('status')
                    if status == 'progress':
                        outcome = auto_free.progress(data.get('source_id'), data.get('job_id'),
                                                     data.get('step'), data.get('info'))
                    else:
                        outcome = auto_free.complete(
                            data.get('source_id'), data.get('job_id'), status,
                            nxm=data.get('nxm', ''), note=data.get('note', ''), submit=submit_nxm,
                            url=data.get('url', ''), path=data.get('path', ''),
                            reason=data.get('reason', ''), file_id=data.get('file_id', 0),
                            filename=data.get('filename', ''), warning=data.get('warning', ''))
                    ok = bool(outcome)
                    if trace: trace.emit('server.auto_result', result='accepted' if ok else 'rejected',
                                         stage=status if status in ('progress', 'attention', 'nxm', 'url', 'file', 'failed') else 'unknown')
                    if ok:
                        if status != 'progress' or data.get('step') != 'heartbeat':
                            incoming.put(('auto_free_update',))
                        if isinstance(outcome, dict):
                            incoming.put(('auto_outcome', outcome))
                    return self.reply(200 if ok else 409, {'ok':ok})
                except (ValueError, TypeError, json.JSONDecodeError):
                    return self.reply(400, {'error':'Invalid confirmation'})

            if self.path not in ('/import', '/snapshot') or not secrets.compare_digest(self.headers.get('X-Nexus-Token', ''), token):
                return self.reply(403, {'error': 'Invalid token or path'})
            try:
                data = self.read_payload()
                if not isinstance(data, dict) or not isinstance(data.get('tabs'), list) or len(data['tabs']) > 400:
                    return self.reply(400, {'error': 'Invalid tabs'})
                items = normalize_entries(data['tabs'])
                if self.path == '/snapshot':
                    if snapshots is None:
                        return self.reply(404, {'error': 'Snapshot store unavailable'})
                    size = snapshots.update(data.get('browser', 'browser'), items, data.get('source_id'))
                    if trace: trace.emit('server.snapshot_received', count=size, source_id=data.get('source_id'))
                    incoming.put(('browser_sync', data.get('browser', 'browser'), size))
                    return self.reply(200, {'accepted': size, 'mode': 'snapshot'})
                incoming.put(items)
                return self.reply(200, {'accepted': len(items), 'mode': 'import'})
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                return self.reply(400, {'error': 'Invalid JSON'})

        def do_GET(self):
            parts = urlsplit(self.path)
            if parts.path == '/health':
                # A browser extension service worker may omit Origin on a
                # simple GET even with host permissions. Only health (which
                # exposes no tokens or private state) permits no Origin.
                # All authenticated endpoints continue requiring a token and
                # pairing requests still require an extension Origin.
                origin = self.headers.get('Origin')
                if origin and not is_extension_origin(origin):
                    return self.reply(403, {'error':'Extension origin required'})
                # No credentials or private mod URLs are returned. Allows an
                # extension popup to distinguish an offline app from pairing.
                return self.reply(200, {'ok': True, 'service': 'nexus-tab-installer', 'protocol': 1, 'language': language() if language else 'ru'})
            if parts.path == '/auto/status':
                if auto_free is None or not secrets.compare_digest(self.headers.get('X-Nexus-Token', ''), token):
                    return self.reply(403, {'error':'Invalid token'})
                job_id = parse_qs(parts.query).get('job_id', [''])[0]
                return self.reply(200, {'state':auto_free.job_state(job_id),
                                        'active': auto_free.is_active()})
            if parts.path == '/auto/next':
                if auto_free is None or not secrets.compare_digest(self.headers.get('X-Nexus-Token', ''), token):
                    return self.reply(403, {'error':'Invalid token'})
                source = self.headers.get('X-Nexus-Source', '')
                claimed = auto_free.claim(source, self.headers.get('X-Nexus-Extension', ''))
                if trace and claimed: trace.emit('server.job_claimed', source_id=source, job_id=claimed['id'], mod_id=claimed['mod_id'], file_id=claimed['file_id'])
                if claimed:
                    incoming.put(('auto_free_update',))
                # 'active' lets the extension poll quickly only while work exists;
                # 'wait' tells it that no job can appear for that many seconds.
                return self.reply(200, {'job': claimed, 'active': auto_free.is_active(),
                                        'wait': 0 if claimed else auto_free.next_wait(),
                                        'scan_marker': snapshots.refresh_marker() if snapshots else ''})
            if parts.path == '/pair/status' and pairing is not None:
                origin = self.headers.get('Origin', '')
                if origin and not is_extension_origin(origin):
                    return self.reply(403, {'error':'Extension origin required'})
                rid = parse_qs(parts.query).get('request_id', [''])[0]
                # Some privileged browser-extension service workers omit Origin
                # on a simple GET. The 192-bit unpredictable request ID is the
                # one-time bearer proof; approval is still explicit and status
                # is bound to its original extension origin when supplied.
                # Never emit ACAO for a request with no Origin.
                return self.reply(200, pairing.status(rid, origin or None))
            return self.reply(404, {'error': 'Not found'})

        def reply(self, status, data):
            if trace:
                # Log only endpoint name / status. Never requests, response bodies,
                # auth headers, query parameters, or tokens.
                path = urlsplit(self.path).path.strip('/').replace('/', '.')
                authorized = secrets.compare_digest(self.headers.get('X-Nexus-Token', ''), token)
                trusted_pair = is_extension_origin(self.headers.get('Origin')) and path.startswith('pair.')
                # Unauthenticated probes must not fill the diagnostic disk log.
                if (authorized or trusted_pair) and (path in ('pair.request','pair.status','auto.complete','snapshot','diagnostics.event') or status >= 400):
                    trace.emit('server.response', endpoint=path, http_status=status)
            output = json.dumps(data).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self._cors()
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(output)))
            self.end_headers()
            self.wfile.write(output)

        def log_message(self, *args):
            pass
    return Handler

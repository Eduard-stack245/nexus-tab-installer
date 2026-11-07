"""Explicit one-time authorization of installed browser extensions.

Only extension origins can request pairing. Requests are time limited and
credentials are disclosed only following a deliberate approval in the desktop UI.
"""
from __future__ import annotations

from threading import RLock
import secrets
import time


ALLOWED_BROWSERS = frozenset(('chrome', 'edge', 'opera-gx', 'firefox'))


def is_extension_origin(origin: str | None) -> bool:
    if not origin:
        return False
    for prefix in ('chrome-extension://', 'moz-extension://'):
        if origin.startswith(prefix):
            host = origin[len(prefix):]
            return bool(host) and '/' not in host and len(host) <= 150 and host.isascii() and all(
                c.isalnum() or c in '-_' for c in host)
    return False


class PairingRequests:
    def __init__(self, *, token: str, clock=time.monotonic, ttl=600):
        self._lock = RLock()
        self._requests = {}
        self._token = token
        self._clock = clock
        self._ttl = ttl

    def _prune(self):
        now = self._clock()
        for request_id, req in list(self._requests.items()):
            if now - req['created'] > self._ttl:
                del self._requests[request_id]

    def request(self, browser: str, origin: str) -> str:
        if not is_extension_origin(origin) or browser not in ALLOWED_BROWSERS:
            raise ValueError('Browser extension authorization required')
        with self._lock:
            self._prune()
            for rid, req in self._requests.items():
                if req['origin'] == origin and req['browser'] == browser:
                    return rid
            if len(self._requests) >= 16:
                raise ValueError('Too many pending browser requests')
            rid = secrets.token_urlsafe(24)
            self._requests[rid] = {'browser': browser, 'origin': origin,
                                   'created': self._clock(), 'state': 'pending'}
            return rid

    def pending(self):
        with self._lock:
            self._prune()
            return [{'request_id': rid, 'browser': req['browser']}
                    for rid, req in self._requests.items() if req['state'] == 'pending']

    def approve(self, rid: str) -> bool:
        return self._decide(rid, 'approved')

    def deny(self, rid: str) -> bool:
        return self._decide(rid, 'denied')

    def _decide(self, rid, state):
        with self._lock:
            self._prune()
            req = self._requests.get(rid)
            if not req or req['state'] != 'pending':
                return False
            req['state'] = state
            return True

    def status(self, rid: str, origin: str | None) -> dict:
        with self._lock:
            self._prune()
            req = self._requests.get(rid)
            if not req or (origin and req['origin'] != origin):
                return {'state': 'expired'}
            state = req['state']
            if state == 'approved':
                del self._requests[rid]
                return {'state': 'approved', 'token': self._token}
            if state == 'denied':
                del self._requests[rid]
            return {'state': state}

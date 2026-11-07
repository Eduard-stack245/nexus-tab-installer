"""Temporary, privacy-limited snapshots of Nexus mod tabs for each browser profile.

An Edge profile and another Edge profile must not overwrite one another.
No non-Nexus URL is kept and stale sources are automatically ignored.
"""
from __future__ import annotations
from threading import RLock
import re
import time
import secrets
from nexus_tab_installer.core.links import normalize_entries

_SOURCE_ID = re.compile(r"^[0-9a-f]{32}$")


class TabSnapshots:
    def __init__(self, clock=time.monotonic):
        self._lock = RLock()
        self._clock = clock
        self._sources = {}
        self._refresh_revision = 0
        self._instance_id = secrets.token_hex(8)
        self._refresh_targets = set()
        self._refresh_responses = set()

    def update(self, browser: str, tabs: list, source_id: str | None = None) -> int:
        name = (browser or 'browser').strip().lower()[:32]
        if not name or not all(c.isalnum() or c in '-_ ' for c in name):
            raise ValueError('Invalid browser label')
        if source_id is not None and not _SOURCE_ID.fullmatch(source_id):
            raise ValueError('Invalid browser instance ID')
        source = f'{name}:{source_id}' if source_id else name
        clean = normalize_entries(tabs)
        with self._lock:
            # Bound the number of disconnected / restarted extension profiles.
            if source not in self._sources and len(self._sources) >= 64:
                oldest = min(self._sources, key=lambda k: self._sources[k][0])
                del self._sources[oldest]
            self._sources[source] = (self._clock(), clean)
            # Track every previously connected browser profile separately.
            # The first reply must not hide an unresponsive second Edge profile.
            if source in self._refresh_targets:
                self._refresh_responses.add(source)
        return len(clean)

    def collect(self, max_age=600) -> list:
        with self._lock:
            merged = []
            for created, rows in self._sources.values():
                if self._clock() - created <= max_age:
                    merged.extend(rows)
        return normalize_entries(merged)

    def sources(self, max_age=600) -> dict[str, int]:
        with self._lock:
            return {name: len(rows) for name, (created, rows) in self._sources.items()
                    if self._clock() - created <= max_age}

    def summary(self, max_age=600) -> dict[str, int]:
        """Human-friendly totals; multiple profiles of the same browser are summed."""
        totals = {}
        for source, count in self.sources(max_age).items():
            browser = source.split(':', 1)[0]
            totals[browser] = totals.get(browser, 0) + count
        return totals

    def profile_counts(self, max_age=600) -> dict[str, int]:
        """Number of active independent extension profiles by browser."""
        totals = {}
        for source in self.sources(max_age):
            browser = source.split(':', 1)[0]
            totals[browser] = totals.get(browser, 0) + 1
        return totals

    def request_refresh(self) -> int:
        """Ask connected browser profiles to send a new snapshot on their next poll."""
        with self._lock:
            self._refresh_revision += 1
            self._refresh_targets = {name for name, (created, _) in self._sources.items()
                                     if self._clock() - created <= 86400}
            self._refresh_responses = set()
            return self._refresh_revision

    def refresh_revision(self) -> int:
        with self._lock:
            return self._refresh_revision

    def refresh_marker(self) -> str:
        """Changes across restarts, so extension storage cannot suppress scans."""
        with self._lock:
            return f'{self._instance_id}:{self._refresh_revision}'

    def refresh_acknowledged(self, revision: int) -> bool:
        with self._lock:
            return (revision == self._refresh_revision and bool(self._refresh_targets)
                    and self._refresh_targets <= self._refresh_responses)

    def refresh_progress(self, revision: int) -> tuple[int, int]:
        with self._lock:
            if revision != self._refresh_revision:
                return 0, 0
            return len(self._refresh_responses), len(self._refresh_targets)

    def collect_refreshed(self, revision: int) -> list:
        """Only profiles that actually answered the current scan (partial result)."""
        with self._lock:
            if revision != self._refresh_revision:
                return []
            rows = []
            for source in self._refresh_responses:
                if source in self._sources:
                    rows.extend(self._sources[source][1])
        return normalize_entries(rows)

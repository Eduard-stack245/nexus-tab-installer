"""How often the app may ask the Nexus website for the next file.

Nexus publishes no limit for website downloads; its terms only forbid downloading
far beyond normal use. The numbers below are this program's own cautious choice,
not a threshold known to be safe. They reduce the load on the site.

Pacing is deliberately plain: fixed pauses, a daily ceiling and backing off when the
site signals trouble. Nothing here imitates a person or hides the automation.

The person may choose their own numbers, at their own risk, down to no pause at all. What
stays in any case is the pause the site itself asks for.

The history survives a restart, so reopening the program does not reset the pace.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

DAY = 86400
WARNING_MEMORY = 6 * 3600      # a verification page slows downloads for this long
MAX_SLOWDOWN = 4
FAILURE_WAIT_CAP = 1800
MAX_FAILURES = 6               # site failures in a row: stop and ask the user
COOLDOWN_BASE = 1800           # the site asked to slow down: 30 min, doubling
COOLDOWN_CAP = 6 * 3600


@dataclass(frozen=True)
class PacePreset:
    label: str
    gap: int      # seconds between two file pages
    batch: int    # files in a row before a longer rest
    rest: int     # seconds of that rest
    daily: int    # files in any 24 hours
    api_gap: int  # seconds between two Nexus API download links (Premium)


PRESETS = {
    'careful': PacePreset('Осторожный', gap=60, batch=25, rest=600, daily=300, api_gap=5),
    'moderate': PacePreset('Умеренный', gap=25, batch=40, rest=300, daily=600, api_gap=2),
}
DEFAULT_PRESET = 'careful'
CUSTOM = 'custom'
CUSTOM_DEFAULTS = {'gap': 60, 'batch': 25, 'rest_minutes': 10, 'daily': 300}
MIN_FAILURE_GAP = 10  # a failing site is never asked again at once, whatever the pace


def custom_preset(gap=None, batch=None, rest_minutes=None, daily=None):
    """The person's own pace. Zero turns a pause or a limit off; a blank field keeps the default."""
    def number(value, name, top):
        try:
            return max(0, min(int(str(value).strip()), top))
        except (TypeError, ValueError):
            return CUSTOM_DEFAULTS[name]
    pause = number(gap, 'gap', DAY)
    return PacePreset('Свой', gap=pause, batch=number(batch, 'batch', 100_000),
                      rest=number(rest_minutes, 'rest_minutes', 24 * 60) * 60,
                      daily=number(daily, 'daily', 1_000_000), api_gap=min(pause, 5))

WAIT_TEXT = {
    'gap': 'Пауза перед следующим файлом',
    'rest': 'Длинная пауза после серии файлов',
    'daily': 'Дневной предел файлов исчерпан, следующий файл через',
    'failures': 'Nexus не отдал файл, пауза перед следующей попыткой',
    'cooldown': 'Nexus просит подождать, пауза',
}


def clock_text(seconds):
    """0:42, 12:05 or 2:03:09."""
    seconds = max(0, int(seconds + 0.999))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f'{hours}:{minutes:02d}:{secs:02d}' if hours else f'{minutes}:{secs:02d}'


def duration_text(seconds):
    """Rough length for people: «45 мин», «2 ч 10 мин»."""
    minutes = max(1, round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    if not hours:
        return f'{minutes} мин'
    return f'{hours} ч {minutes} мин' if minutes else f'{hours} ч'


def _times(raw, now, age):
    """Timestamps from the saved file: recent, not in the future, oldest first."""
    if not isinstance(raw, list):
        return []
    valid = [float(t) for t in raw if isinstance(t, (int, float)) and not isinstance(t, bool)]
    return sorted(t for t in valid if now - age < t <= now)


class DownloadPacer:
    def __init__(self, path=None, preset=DEFAULT_PRESET, clock=time.time):
        self._path = Path(path) if path else None
        self._clock = clock
        self._lock = threading.RLock()
        self._preset = self._pace_from(preset)
        self._starts = []      # file pages opened in the last 24 hours
        self._warnings = []    # verification pages in the last hours
        self._strikes = []     # "slow down" answers in the last 24 hours
        self._cooldown_until = 0.0
        self._failures = 0     # site failures in a row
        self._api_next = 0.0
        self._load()

    # ----- persistence ---------------------------------------------------
    def _load(self):
        if not self._path:
            return
        try:
            data = json.loads(self._path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        now = self._clock()
        self._starts = _times(data.get('starts'), now, DAY)
        self._warnings = _times(data.get('warnings'), now, WARNING_MEMORY)
        self._strikes = _times(data.get('strikes'), now, DAY)
        cooldown = data.get('cooldown_until')
        if isinstance(cooldown, (int, float)) and now < cooldown <= now + COOLDOWN_CAP:
            self._cooldown_until = float(cooldown)
        failures = data.get('failures')
        if isinstance(failures, int) and 0 <= failures <= MAX_FAILURES:
            self._failures = failures

    def _save(self):
        if not self._path:
            return
        payload = {'starts': self._starts, 'warnings': self._warnings, 'strikes': self._strikes,
                   'cooldown_until': self._cooldown_until, 'failures': self._failures}
        temporary = self._path.with_name(self._path.name + '.tmp')
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload), encoding='utf-8')
            os.replace(temporary, self._path)
        except OSError:
            # A pace that is not remembered must not stop the downloads.
            pass

    def _prune(self, now):
        self._starts = [t for t in self._starts if now - t < DAY]
        self._warnings = [t for t in self._warnings if now - t < WARNING_MEMORY]
        self._strikes = [t for t in self._strikes if now - t < DAY]

    # ----- settings ------------------------------------------------------
    @property
    def preset(self):
        return self._preset

    @staticmethod
    def _pace_from(preset):
        return preset if isinstance(preset, PacePreset) else PRESETS.get(preset) or PRESETS[DEFAULT_PRESET]

    def set_preset(self, preset):
        """The name of a ready pace, or a PacePreset with the person's own numbers."""
        with self._lock:
            self._preset = self._pace_from(preset)

    # ----- the decision --------------------------------------------------
    def _slowdown(self):
        return min(MAX_SLOWDOWN, 1 + len(self._warnings))

    def _files_in_a_row(self):
        """Pages opened since the last pause at least as long as the rest."""
        run = 1 if self._starts else 0
        for index in range(len(self._starts) - 1, 0, -1):
            if self._starts[index] - self._starts[index - 1] >= self._preset.rest:
                break
            run += 1
        return run

    def wait(self):
        """(seconds, reason) until the next file page may be opened; (0, '') means now."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            pace = self._preset
            reasons = [(self._cooldown_until - now, 'cooldown')]
            if pace.daily and len(self._starts) >= pace.daily:
                reasons.append((self._starts[-pace.daily] + DAY - now, 'daily'))
            if self._starts:
                last = self._starts[-1]
                reasons.append((last + pace.gap * self._slowdown() - now, 'gap'))
                if pace.batch and pace.rest and self._files_in_a_row() >= pace.batch:
                    reasons.append((last + pace.rest - now, 'rest'))
                if self._failures:
                    extra = min(FAILURE_WAIT_CAP, max(pace.gap, MIN_FAILURE_GAP) * 2 ** self._failures)
                    reasons.append((last + extra - now, 'failures'))
            seconds, reason = max(reasons, key=lambda entry: entry[0])
            return (seconds, reason) if seconds > 0 else (0, '')

    def estimate(self, count):
        """Rough seconds to open ``count`` file pages, without the downloads themselves."""
        if count <= 0:
            return 0
        with self._lock:
            pace = self._preset
            steps = count - 1
            rests = steps // pace.batch if pace.batch and pace.rest else 0
            days = steps // pace.daily if pace.daily else 0
            gap = pace.gap * self._slowdown()
            return self.wait()[0] + gap * (steps - rests) + pace.rest * rests + DAY * days

    # ----- what happened -------------------------------------------------
    def record_start(self):
        with self._lock:
            self._starts.append(self._clock())
            self._save()

    def record_success(self):
        with self._lock:
            if self._failures:
                self._failures = 0
                self._save()

    def record_failure(self):
        """The site failed to give a file. Returns the number of failures in a row."""
        with self._lock:
            self._failures = min(MAX_FAILURES, self._failures + 1)
            self._save()
            return self._failures

    def reset_failures(self):
        with self._lock:
            self._failures = 0
            self._save()

    def record_warning(self):
        """The site showed a verification page: go slower for a few hours."""
        with self._lock:
            self._warnings.append(self._clock())
            self._save()

    def cool_down(self):
        """The site asked to slow down: hold everything. Returns the pause in seconds."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            self._strikes.append(now)
            seconds = min(COOLDOWN_CAP, COOLDOWN_BASE * 2 ** (len(self._strikes) - 1))
            self._cooldown_until = max(self._cooldown_until, now + seconds)
            self._save()
            return seconds

    def api_delay(self):
        """Seconds to wait before the next Nexus API download link; reserves its turn."""
        with self._lock:
            now = self._clock()
            turn = max(now, self._api_next)
            self._api_next = turn + self._preset.api_gap
            return turn - now

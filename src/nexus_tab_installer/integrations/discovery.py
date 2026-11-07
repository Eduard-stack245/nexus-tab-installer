"""Conservative manager discovery. Never guesses a customized Vortex download path."""
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class ManagerCandidate:
    manager: str
    label: str
    config: Path
    downloads: Path | None
    verified: bool


def discover_vortex(env: dict | None = None) -> list[ManagerCandidate]:
    env = os.environ if env is None else env
    locations = [Path(env[k]) / 'Vortex' for k in ('APPDATA','PROGRAMDATA') if env.get(k)]
    result = []
    for directory in locations:
        if not directory.is_dir():
            continue
        # Vortex allows custom downloads folders. We cannot infer them from the default.
        default_downloads = directory / 'downloads'
        result.append(ManagerCandidate('vortex', f'Vortex ({directory})', directory,
                                       default_downloads if default_downloads.is_dir() else None,
                                       False))
    return result


def discover_mo2(env: dict | None = None, roots=None) -> list[ManagerCandidate]:
    from .destinations import mo2_downloads, DestinationError, find_mo2_instances
    if roots is None and env is not None:
        from .destinations import _windows_install_roots
        roots = _windows_install_roots(env)
    paths = find_mo2_instances(roots)
    result = []
    for ini in paths:
        try:
            dest = mo2_downloads(ini)
            result.append(ManagerCandidate('mo2', f'MO2 ({ini.parent.name})', ini,
                                           dest, dest.is_dir()))
        except (DestinationError, OSError):
            continue
    return result

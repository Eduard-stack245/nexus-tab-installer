"""What kind of file a name promises. A Nexus file is often an archive, but it does not have to be."""
from __future__ import annotations

from pathlib import PurePath

# What MO2 and Vortex can open as an archive themselves.
ARCHIVE_SUFFIXES = frozenset({'.zip', '.7z', '.rar', '.tar', '.gz', '.gzip', '.tgz', '.bz2', '.bzip2', '.tbz2', '.xz',
                              '.txz', '.lzma', '.z', '.cab', '.arj', '.001', '.z01', '.r00', '.fomod', '.omod', '.dazip'})
# Programs are saved, but this application never starts them and never treats them as mods.
PROGRAM_SUFFIXES = frozenset({'.exe', '.msi', '.bat', '.cmd', '.ps1', '.scr', '.com'})


def _suffix(name):
    return PurePath(str(name)).suffix.casefold()


def is_archive_name(name):
    return _suffix(name) in ARCHIVE_SUFFIXES


def is_plain_file(name):
    """A file with a known extension that is not an archive: .esp, .schem, .dll, .txt."""
    suffix = _suffix(name)
    return bool(suffix) and suffix not in ARCHIVE_SUFFIXES


def is_program(name):
    return _suffix(name) in PROGRAM_SUFFIXES

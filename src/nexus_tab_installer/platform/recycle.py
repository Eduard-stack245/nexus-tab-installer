"""Deleted files go to the Recycle Bin, so that a wrong click can be undone in Windows."""
from __future__ import annotations

import ctypes
import os
import shutil
from ctypes import wintypes
from pathlib import Path

_DELETE = 3
_ALLOW_UNDO, _NO_CONFIRMATION, _SILENT, _NO_ERROR_UI = 0x40, 0x10, 0x04, 0x400


class _FileOperation(ctypes.Structure):
    _fields_ = [('window', wintypes.HWND), ('function', wintypes.UINT), ('source', ctypes.c_wchar_p), ('target', ctypes.c_wchar_p),
                ('flags', ctypes.c_ushort), ('aborted', wintypes.BOOL), ('mappings', ctypes.c_void_p), ('title', ctypes.c_wchar_p)]


def to_recycle_bin(path):
    """A file or a folder. On a disk without a Recycle Bin, a flash drive for one, it is deleted for good."""
    path = Path(path).resolve()
    if os.name != 'nt':
        shutil.rmtree(path) if path.is_dir() else path.unlink()
        return
    # The API takes a list that ends with two zeros: this one and the one every string has.
    operation = _FileOperation(None, _DELETE, str(path) + '\0', None, _ALLOW_UNDO | _NO_CONFIRMATION | _SILENT | _NO_ERROR_UI, False, None, None)
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if code or operation.aborted:
        raise OSError(f'Windows не смог удалить {path.name} (код {code})')

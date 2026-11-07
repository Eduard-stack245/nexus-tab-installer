"""Opt-in per-user Windows protocol registration. Never overwrites machine-wide keys."""
import os
import subprocess
import sys
from pathlib import Path


def register_nxm(handler_file=None, python_executable=None):
    if os.name != 'nt':
        raise RuntimeError('Регистрация nxm:// доступна только на Windows')
    import winreg
    handler = Path(handler_file or (Path(__file__).resolve().parents[3] / 'scripts' / 'nxm_entry.py')).resolve()
    if not handler.is_file():
        raise FileNotFoundError(handler)
    python = Path(python_executable or sys.executable).resolve()
    # Python launcher is required: executable paths and the URL are quoted.
    # subprocess.list2cmdline quotes %1 only as needed; Windows protocol passing
    # requires explicit quotes, so force them around the URL argument.
    if python.name.lower() == 'python.exe' and python.with_name('pythonw.exe').exists():
        python = python.with_name('pythonw.exe')
    command = subprocess.list2cmdline([str(python), str(handler)]) + ' "%1"'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Classes\nxm') as root:
        winreg.SetValueEx(root, '', 0, winreg.REG_SZ, 'URL:Nexus Mod Manager Protocol')
        winreg.SetValueEx(root, 'URL Protocol', 0, winreg.REG_SZ, '')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\Classes\nxm\shell\open\command') as key:
        winreg.SetValueEx(key, '', 0, winreg.REG_SZ, command)
    return command

"""A message from Windows for when the person is not looking at the program.

Nothing is installed and nothing is registered, in keeping with a portable program: the message goes
through the Windows PowerShell that is part of Windows, and it also lights up the taskbar button. Text
reaches the script through the environment, so a name of a mod cannot become a part of the script.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import threading
from ctypes import wintypes

_SCRIPT = r"""
$manager = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
$template = $manager::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType, Windows.UI.Notifications, ContentType = WindowsRuntime]::ToastText02)
$lines = $template.GetElementsByTagName('text')
$lines.Item(0).AppendChild($template.CreateTextNode($env:NEXUS_NOTIFY_TITLE)) > $null
$lines.Item(1).AppendChild($template.CreateTextNode($env:NEXUS_NOTIFY_TEXT)) > $null
$toast = [Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime]::new($template)
$manager::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show($toast)
"""
_TEXT_LIMIT = 240
_FLASH_ALL_UNTIL_FOCUS = 0x03 | 0x0C


class _FlashInfo(ctypes.Structure):
    _fields_ = [('size', wintypes.UINT), ('window', wintypes.HWND), ('flags', wintypes.DWORD),
                ('count', wintypes.UINT), ('timeout', wintypes.DWORD)]


def flash_window(title):
    """The taskbar button lights up until the person comes to the window. Focus is not taken."""
    if os.name != 'nt':
        return False
    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = wintypes.HWND
    window = user32.FindWindowW(None, title)
    if not window:
        return False
    info = _FlashInfo(ctypes.sizeof(_FlashInfo), window, _FLASH_ALL_UNTIL_FOCUS, 0, 0)
    user32.FlashWindowEx(ctypes.byref(info))
    return True


def show_toast(title, text, timeout=30):
    if os.name != 'nt':
        return False
    environment = {**os.environ, 'NEXUS_NOTIFY_TITLE': str(title)[:_TEXT_LIMIT], 'NEXUS_NOTIFY_TEXT': str(text)[:_TEXT_LIMIT]}
    try:
        done = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', _SCRIPT], env=environment,
                              capture_output=True, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def notify(title, text, window_title=''):
    """Never holds up the program, and a message that did not get through changes nothing."""
    def work():
        try:
            if window_title:
                flash_window(window_title)
            show_toast(title, text)
        except Exception:
            pass
    threading.Thread(target=work, daemon=True).start()

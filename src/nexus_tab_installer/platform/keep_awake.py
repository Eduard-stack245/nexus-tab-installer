"""Keep Windows from sleeping while a long download batch is running."""
import sys

_CONTINUOUS = 0x80000000
_SYSTEM_REQUIRED = 0x00000001


def set_keep_awake(active):
    """The flag belongs to the calling thread: call it from one thread only."""
    if sys.platform != 'win32':
        return
    import ctypes
    ctypes.windll.kernel32.SetThreadExecutionState(
        _CONTINUOUS | (_SYSTEM_REQUIRED if active else 0))

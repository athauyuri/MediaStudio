"""Read-only Windows process identity/liveness, including protection against PID reuse."""
import ctypes
from ctypes import wintypes
import os


class ProcessProbe:
    def _read(self, pid):
        if os.name != "nt":
            return None, None  # Unsupported probes never claim another owner is dead.
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        api.GetProcessTimes.restype = wintypes.BOOL
        handle = api.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle:
            return (False, None) if ctypes.get_last_error() == 87 else (None, None)
        try:
            wait = api.WaitForSingleObject(handle, 0)
            if wait == 0:
                return False, None
            if wait != 258:
                return None, None
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not api.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
                return None, None
            return True, str(created.dwLowDateTime | created.dwHighDateTime << 32)
        finally:
            api.CloseHandle(handle)

    def current(self):
        pid = os.getpid()
        alive, token = self._read(pid)
        if not alive or token is None:
            raise RuntimeError("Unable to establish Windows scan-process ownership")
        return pid, token

    def is_alive(self, pid, token):
        if token is None:
            return None
        alive, current = self._read(pid)
        if alive is not True:
            return alive
        return current == token

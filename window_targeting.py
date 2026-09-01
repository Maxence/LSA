from __future__ import annotations

import ctypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assist_common import parse_target_processes

try:
    from ctypes import wintypes
except ImportError:  # pragma: no cover
    wintypes = None  # type: ignore[assignment]


IS_WINDOWS = os.name == "nt"
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
GW_OWNER = 4
SW_RESTORE = 9
SW_MINIMIZE = 6


@dataclass(frozen=True)
class TargetWindow:
    hwnd: int
    thread_id: int
    process_id: int
    process_name: str
    keyboard_layout: int = 0
    title: str = ""
    was_minimized: bool = False


_user32: Any = None
_kernel32: Any = None
_enum_windows_proc_type: Any = None
_switch_to_this_window: Any = None

if IS_WINDOWS:
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _enum_windows_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    _user32.EnumWindows.argtypes = [_enum_windows_proc_type, wintypes.LPARAM]
    _user32.EnumWindows.restype = wintypes.BOOL
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
    _user32.GetWindow.restype = wintypes.HWND
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetForegroundWindow.argtypes = []
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
    _user32.GetKeyboardLayout.restype = ctypes.c_void_p
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.BringWindowToTop.argtypes = [wintypes.HWND]
    _user32.BringWindowToTop.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.SetActiveWindow.argtypes = [wintypes.HWND]
    _user32.SetActiveWindow.restype = wintypes.HWND
    _user32.SetFocus.argtypes = [wintypes.HWND]
    _user32.SetFocus.restype = wintypes.HWND
    _user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    _user32.AttachThreadInput.restype = wintypes.BOOL

    _kernel32.GetCurrentThreadId.argtypes = []
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    _switch_to_this_window = getattr(_user32, "SwitchToThisWindow", None)
    if _switch_to_this_window is not None:
        _switch_to_this_window.argtypes = [wintypes.HWND, wintypes.BOOL]
        _switch_to_this_window.restype = None


def _process_name(process_id: int) -> str:
    if not IS_WINDOWS or not process_id:
        return ""
    process_handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    if not process_handle:
        return ""
    try:
        capacity = 32768
        path_buffer = ctypes.create_unicode_buffer(capacity)
        size = wintypes.DWORD(capacity)
        if _kernel32.QueryFullProcessImageNameW(process_handle, 0, path_buffer, ctypes.byref(size)):
            return Path(path_buffer.value).name
        return ""
    finally:
        _kernel32.CloseHandle(process_handle)


def _window_title(hwnd: int) -> str:
    if not IS_WINDOWS:
        return ""
    length = int(_user32.GetWindowTextLengthW(hwnd) or 0)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value.strip()


def list_target_windows(targets: str) -> list[TargetWindow]:
    """Return one visible top-level window per matching process."""
    expected = parse_target_processes(targets)
    if not IS_WINDOWS or not expected:
        return []

    candidates: dict[int, TargetWindow] = {}

    def enum_callback(hwnd: int, _lparam: int) -> bool:
        try:
            hwnd_value = int(hwnd or 0)
            if not hwnd_value or not _user32.IsWindowVisible(hwnd_value):
                return True
            if int(_user32.GetWindow(hwnd_value, GW_OWNER) or 0):
                return True

            pid = wintypes.DWORD(0)
            thread_id = int(_user32.GetWindowThreadProcessId(hwnd_value, ctypes.byref(pid)) or 0)
            if not pid.value:
                return True

            process_name = _process_name(int(pid.value))
            if process_name.lower() not in expected:
                return True

            candidate = TargetWindow(
                hwnd=hwnd_value,
                thread_id=thread_id,
                process_id=int(pid.value),
                process_name=process_name,
                keyboard_layout=int(_user32.GetKeyboardLayout(thread_id) or 0),
                title=_window_title(hwnd_value),
                was_minimized=bool(_user32.IsIconic(hwnd_value)),
            )

            previous = candidates.get(candidate.process_id)
            if previous is None:
                candidates[candidate.process_id] = candidate
            else:
                # Prefer the window that looks most like the actual game window.
                previous_score = (bool(previous.title), not previous.was_minimized)
                candidate_score = (bool(candidate.title), not candidate.was_minimized)
                if candidate_score > previous_score:
                    candidates[candidate.process_id] = candidate
        except Exception:
            # A window may disappear while EnumWindows is walking the desktop.
            pass
        return True

    callback = _enum_windows_proc_type(enum_callback)
    _user32.EnumWindows(callback, 0)
    return sorted(candidates.values(), key=lambda item: (item.process_id, item.hwnd))


def is_window_valid(hwnd: int) -> bool:
    return bool(IS_WINDOWS and hwnd and _user32.IsWindow(hwnd))


def foreground_hwnd() -> int:
    if not IS_WINDOWS:
        return 0
    return int(_user32.GetForegroundWindow() or 0)


def activate_window(hwnd: int, *, timeout: float = 0.45) -> bool:
    """Try to make hwnd the foreground window, and verify that Windows accepted it."""
    if not is_window_valid(hwnd):
        return False
    if foreground_hwnd() == hwnd:
        return True

    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, SW_RESTORE)

    target_pid = wintypes.DWORD(0)
    target_thread = int(_user32.GetWindowThreadProcessId(hwnd, ctypes.byref(target_pid)) or 0)
    current_thread = int(_kernel32.GetCurrentThreadId() or 0)

    current_foreground = foreground_hwnd()
    foreground_pid = wintypes.DWORD(0)
    foreground_thread = 0
    if current_foreground:
        foreground_thread = int(
            _user32.GetWindowThreadProcessId(current_foreground, ctypes.byref(foreground_pid)) or 0
        )

    attached_pairs: list[tuple[int, int]] = []
    pairs = {
        (current_thread, foreground_thread),
        (current_thread, target_thread),
        (foreground_thread, target_thread),
    }
    for source, destination in pairs:
        if not source or not destination or source == destination:
            continue
        try:
            if _user32.AttachThreadInput(source, destination, True):
                attached_pairs.append((source, destination))
        except Exception:
            pass

    try:
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
        _user32.SetActiveWindow(hwnd)
        _user32.SetFocus(hwnd)
    finally:
        for source, destination in reversed(attached_pairs):
            try:
                _user32.AttachThreadInput(source, destination, False)
            except Exception:
                pass

    if foreground_hwnd() != hwnd and _switch_to_this_window is not None:
        try:
            _switch_to_this_window(hwnd, True)
        except Exception:
            pass

    deadline = time.monotonic() + max(0.05, float(timeout))
    while time.monotonic() < deadline:
        if foreground_hwnd() == hwnd:
            return True
        time.sleep(0.01)
    return foreground_hwnd() == hwnd


def restore_minimized_state(window: TargetWindow) -> None:
    if not IS_WINDOWS or not window.was_minimized or not is_window_valid(window.hwnd):
        return
    try:
        _user32.ShowWindow(window.hwnd, SW_MINIMIZE)
    except Exception:
        pass

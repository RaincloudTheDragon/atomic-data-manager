"""
OS taskbar progress for long Atomic scans/cleans.

Mirrors ``scene.atomic.operation_progress`` (0–100) onto the Windows taskbar
via ITaskbarList3.

Blender's WM job ticker calls ``WM_progress_clear`` → GHOST
``SetProgressState(NOPROGRESS)`` whenever no ``WM_JOB_PROGRESS`` jobs are
active. That wipe fights any external ``SetProgressValue`` and makes the bar
flap. While an Atomic session is open we hook ITaskbarList3's SetProgressState
(shared COM vtable in-process) and swallow NOPROGRESS for our HWND so the
determinate bar stays put.
"""

from __future__ import annotations

import sys
import ctypes
from ctypes import wintypes

import bpy

from .. import config

_session = False
_last_completed = -1  # 0..10000 high-water within the session

_tb_inited = False
_tb_ptr = None
_tb_hwnd = None
_set_value_fn = None
_set_state_fn = None  # possibly hooked wrapper

_GHOST_CLASS = "GHOST_WindowClass"
_S_OK = 0
_S_FALSE = 1
_TBPF_NOPROGRESS = 0
_TBPF_NORMAL = 2
_PAGE_EXECUTE_READWRITE = 0x40
_END_DELAY = 0.15

# COM vtable hook state (keep callback alive; restore on disable)
_hook_installed = False
_hook_cb = None
_orig_set_state = None
_vtable_slot_addr = None
_orig_slot_value = None


def begin():
    """Start (or restart) a taskbar progress session at a visible 1%."""
    global _session, _last_completed
    if sys.platform != "win32":
        _session = True
        _last_completed = -1
        return

    if not _ensure_com():
        _session = False
        _last_completed = -1
        config.debug_print("[Atomic Debug] Taskbar begin: COM unavailable")
        return

    hwnd = _lock_hwnd(force=True)
    _session = True
    _last_completed = -1
    if not hwnd:
        config.debug_print("[Atomic Debug] Taskbar begin: no GHOST HWND")
        return

    try:
        # 1% — Windows often draws nothing for 0/10000.
        _set_value_fn(_tb_ptr, hwnd, 100, 10000)
        _last_completed = 100
        config.debug_print(f"[Atomic Debug] Taskbar begin hwnd={int(hwnd)}")
    except (OSError, TypeError, ValueError, AttributeError) as err:
        config.debug_print(f"[Atomic Debug] Taskbar begin failed: {err}")
        _session = False


def set_progress(percent):
    """Push ``operation_progress`` (0–100) to the taskbar. No-op outside a session."""
    global _last_completed
    if not _session or sys.platform != "win32":
        return
    try:
        percent = float(percent)
    except (TypeError, ValueError):
        return
    percent = 0.0 if percent < 0.0 else 100.0 if percent > 100.0 else percent

    completed = int(round(percent * 100.0))
    completed = 0 if completed < 0 else 10000 if completed > 10000 else completed
    if completed < 100:
        completed = 100

    if _last_completed >= 0 and completed < _last_completed:
        return
    if completed == _last_completed:
        return

    if not _ensure_com():
        return
    hwnd = _lock_hwnd(force=False)
    if not hwnd or _set_value_fn is None:
        return
    try:
        hr = _set_value_fn(_tb_ptr, hwnd, completed, 10000)
        if hr not in (_S_OK, _S_FALSE):
            config.debug_print(
                f"[Atomic Debug] Taskbar SetProgressValue hr={hr} percent={percent}"
            )
            return
        _last_completed = completed
    except (OSError, TypeError, ValueError, AttributeError) as err:
        config.debug_print(f"[Atomic Debug] Taskbar set_progress({percent}) failed: {err}")


def end():
    """Close the session and clear the taskbar after a short paint delay."""
    global _session, _last_completed
    if not _session:
        return
    _session = False
    _last_completed = -1
    if sys.platform != "win32":
        return
    try:
        bpy.app.timers.register(_deferred_clear, first_interval=_END_DELAY)
    except (ValueError, RuntimeError):
        _deferred_clear()


update = set_progress


def _deferred_clear():
    global _tb_hwnd
    if _session:
        return None
    hwnd = _tb_hwnd
    _tb_hwnd = None
    if not hwnd or _orig_set_state is None:
        # Fall back to (possibly hooked) state fn — session is False so NOPROGRESS passes.
        if hwnd and _set_state_fn is not None and _tb_ptr is not None:
            try:
                _set_state_fn(_tb_ptr, hwnd, _TBPF_NOPROGRESS)
            except (OSError, TypeError, ValueError, AttributeError):
                pass
        return None
    try:
        # Call the real COM method so the clear is not swallowed.
        _orig_set_state(_tb_ptr, hwnd, _TBPF_NOPROGRESS)
        config.debug_print("[Atomic Debug] Taskbar end")
    except (OSError, TypeError, ValueError, AttributeError) as err:
        config.debug_print(f"[Atomic Debug] Taskbar end failed: {err}")
    return None


# --- COM / HWND / clear-hook -----------------------------------------------

def _guid(data1, data2, data3, data4):
    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    return GUID(data1, data2, data3, (ctypes.c_ubyte * 8)(*data4))


def _ensure_com():
    """Create ITaskbarList3 once and install the NOPROGRESS swallow hook."""
    global _tb_inited, _tb_ptr, _set_value_fn, _set_state_fn
    if _tb_inited:
        return _tb_ptr is not None and _set_value_fn is not None
    _tb_inited = True
    if sys.platform != "win32":
        return False
    try:
        ole32 = ctypes.windll.ole32
        ole32.CoInitialize.argtypes = [ctypes.c_void_p]
        ole32.CoInitialize.restype = ctypes.HRESULT
        ole32.CoCreateInstance.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        ]
        ole32.CoCreateInstance.restype = ctypes.HRESULT

        hr = ole32.CoInitialize(None)
        if hr not in (_S_OK, _S_FALSE):
            config.debug_print(f"[Atomic Debug] Taskbar CoInitialize hr={hr}")
            return False

        clsid = _guid(0x56FDF344, 0xFD6D, 0x11D0, (0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90))
        iid = _guid(0xEA1AFB91, 0x9E28, 0x4B86, (0x90, 0xE9, 0x9E, 0x9F, 0x8A, 0x5E, 0xEF, 0xAF))
        ptr = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(ptr)
        )
        if hr != _S_OK or not ptr.value:
            config.debug_print(f"[Atomic Debug] Taskbar CoCreateInstance hr={hr}")
            return False

        vtable = ctypes.cast(
            ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0],
            ctypes.POINTER(ctypes.c_void_p),
        )
        hr_init = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)(vtable[3])
        if hr_init(ptr) not in (_S_OK, _S_FALSE):
            return False

        _set_value_fn = ctypes.WINFUNCTYPE(
            ctypes.HRESULT,
            ctypes.c_void_p,
            wintypes.HWND,
            ctypes.c_ulonglong,
            ctypes.c_ulonglong,
        )(vtable[9])
        _set_state_fn = ctypes.WINFUNCTYPE(
            ctypes.HRESULT,
            ctypes.c_void_p,
            wintypes.HWND,
            ctypes.c_int,
        )(vtable[10])
        _tb_ptr = ptr

        _install_clear_hook(vtable)
        return True
    except (AttributeError, OSError, TypeError, ValueError) as err:
        config.debug_print(f"[Atomic Debug] Taskbar COM init failed: {err}")
        _tb_ptr = None
        _set_value_fn = None
        _set_state_fn = None
        return False


def _install_clear_hook(vtable):
    """
    Patch ITaskbarList3::SetProgressState so GHOST's WM_progress_clear cannot
    blank our bar mid-scan. Same vtable is shared by GHOST's TaskbarList.
    """
    global _hook_installed, _hook_cb, _orig_set_state, _vtable_slot_addr, _orig_slot_value
    global _set_state_fn
    if _hook_installed or sys.platform != "win32":
        return

    StateFn = ctypes.WINFUNCTYPE(
        ctypes.HRESULT,
        ctypes.c_void_p,
        wintypes.HWND,
        ctypes.c_int,
    )
    _orig_set_state = StateFn(vtable[10])

    @StateFn
    def _hooked_set_state(this, hwnd, state):
        # Swallow Blender's idle clears while Atomic owns the bar.
        if (
            _session
            and state == _TBPF_NOPROGRESS
            and _tb_hwnd
            and int(hwnd) == int(_tb_hwnd)
        ):
            return _S_OK
        return _orig_set_state(this, hwnd, state)

    _hook_cb = _hooked_set_state  # prevent GC
    hook_addr = ctypes.cast(_hook_cb, ctypes.c_void_p).value

    vtable_addr = ctypes.cast(
        ctypes.cast(_tb_ptr, ctypes.POINTER(ctypes.c_void_p))[0],
        ctypes.c_void_p,
    ).value
    slot_addr = vtable_addr + 10 * ctypes.sizeof(ctypes.c_void_p)
    _vtable_slot_addr = slot_addr
    _orig_slot_value = vtable[10]

    kernel32 = ctypes.windll.kernel32
    old_protect = wintypes.DWORD()
    size = ctypes.sizeof(ctypes.c_void_p)
    if not kernel32.VirtualProtect(
        ctypes.c_void_p(slot_addr), size, _PAGE_EXECUTE_READWRITE, ctypes.byref(old_protect)
    ):
        config.debug_print("[Atomic Debug] Taskbar clear-hook VirtualProtect failed")
        return

    ctypes.c_void_p.from_address(slot_addr).value = hook_addr
    kernel32.VirtualProtect(
        ctypes.c_void_p(slot_addr), size, old_protect.value, ctypes.byref(old_protect)
    )

    _set_state_fn = _hook_cb
    _hook_installed = True
    config.debug_print("[Atomic Debug] Taskbar clear-hook installed")


def _lock_hwnd(force=False):
    """Pin the main GHOST window for the session."""
    global _tb_hwnd
    user32 = ctypes.windll.user32
    if not force and _tb_hwnd and user32.IsWindow(_tb_hwnd):
        return _tb_hwnd

    pid = ctypes.windll.kernel32.GetCurrentProcessId()
    best = None
    best_score = -1

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _lparam):
        nonlocal best, best_score
        proc = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc))
        if proc.value != pid:
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, 4):  # GW_OWNER
            return True

        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        is_ghost = cls.value == _GHOST_CLASS

        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or ""

        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)

        score = area
        if is_ghost:
            score += 10 ** 12
        if "blender" in title.lower():
            score += 10 ** 11
        if score > best_score:
            best_score = score
            best = hwnd
        return True

    user32.EnumWindows(_enum, 0)
    _tb_hwnd = best
    return _tb_hwnd

"""
OS taskbar progress for long Atomic scans/cleans/searches.

Mirrors ``scene.atomic.operation_progress`` (0–100) onto the Windows taskbar
via ITaskbarList3.

Blender's WM job ticker calls ``WM_progress_clear`` → GHOST
``SetProgressState(NOPROGRESS)`` whenever no progress jobs are active. That
blanks any external determinate bar between updates (the flap).

While an Atomic session is open we install **one process-wide** hook on
ITaskbarList3::SetProgressState (shared COM vtable) that swallows NOPROGRESS
for our HWND. Hook + ctypes state live in ``bpy.app.driver_namespace`` so
addon reload cannot stack stale callbacks (that AV/crash path).

No high-frequency re-assert timers — those strobe against clears.
"""

from __future__ import annotations

import sys
import ctypes
from ctypes import wintypes

import bpy

from .. import config

# Process-lifetime DNS keys
_DNS_SESSION = "atomic_wm_progress_session"
_DNS_HWND = "atomic_wm_progress_hwnd"
_DNS_LAST = "atomic_wm_progress_last"
_DNS_CTYPES = "atomic_wm_progress_ctypes"
_DNS_HOOK_CB = "atomic_wm_progress_hook_cb"
_DNS_ORIG_ADDR = "atomic_wm_progress_orig_addr"
_DNS_HOOKED = "atomic_wm_progress_hooked"
_DNS_SLOT_ADDR = "atomic_wm_progress_slot_addr"

_tb_inited = False
_tb_ptr = None
_set_value_fn = None
_set_state_fn = None

_GHOST_CLASS = "GHOST_WindowClass"
_S_OK = 0
_S_FALSE = 1
_TBPF_NOPROGRESS = 0
_PAGE_EXECUTE_READWRITE = 0x40

_StateFn = ctypes.WINFUNCTYPE(
    ctypes.HRESULT,
    ctypes.c_void_p,
    wintypes.HWND,
    ctypes.c_int,
)


def begin():
    """Start (or restart) a taskbar progress session at a visible 1%."""
    if sys.platform != "win32":
        _dns_set_session(True)
        _dns_set_last(-1)
        return

    if not _ensure_com():
        _dns_set_session(False)
        _dns_set_last(-1)
        config.debug_print("[Atomic Debug] Taskbar begin: COM unavailable")
        return

    # end() unpatches so NOPROGRESS can stick — re-arm the clear-hook here.
    _ensure_hook_armed()

    hwnd = _lock_hwnd(force=True)
    _dns_set_session(True)
    _dns_set_last(-1)
    if not hwnd:
        config.debug_print("[Atomic Debug] Taskbar begin: no GHOST HWND")
        return

    try:
        _set_value_fn(_tb_ptr, hwnd, 100, 10000)
        _dns_set_last(100)
        config.debug_print(f"[Atomic Debug] Taskbar begin hwnd={int(hwnd)}")
    except (OSError, TypeError, ValueError, AttributeError) as err:
        config.debug_print(f"[Atomic Debug] Taskbar begin failed: {err}")
        _dns_set_session(False)


def set_progress(percent):
    """Push ``operation_progress`` (0–100) to the taskbar. No-op outside a session."""
    if not _dns_session() or sys.platform != "win32":
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

    last = _dns_last()
    if last >= 0 and completed < last:
        return
    if completed == last:
        return

    if _apply_value(completed):
        _dns_set_last(completed)


def pulse():
    """No-op keep-alive (hook handles clears). Kept for call-site compatibility."""
    return


def end():
    """Close the session and clear the taskbar overlay immediately.

    Idempotent: always drops DNS state and tries to blank the OS bar, even
    when the session flag is already False (file load can orphan a painted
    bar without ever flipping ``is_operation_running`` through our setter).
    """
    had_session = _dns_session()
    _dns_set_session(False)
    _dns_set_last(-1)
    if sys.platform != "win32":
        return
    _clear_taskbar_now()
    if had_session:
        config.debug_print("[Atomic Debug] Taskbar end")


update = set_progress


# --- DNS / ctypes bundle ---------------------------------------------------

def _dns():
    return bpy.app.driver_namespace


def _ctypes_bundle():
    """
    Process-lifetime ctypes cells the COM hook closes over.

    Must not live as module globals — addon reload would desync the hook.
    """
    dns = _dns()
    bundle = dns.get(_DNS_CTYPES)
    if not isinstance(bundle, dict) or "active" not in bundle:
        bundle = {
            "active": ctypes.c_int(0),
            "hwnd": wintypes.HWND(0),
            "orig_addr": ctypes.c_void_p(None),
            "reenter": ctypes.c_int(0),
        }
        dns[_DNS_CTYPES] = bundle
    return bundle


def _dns_session():
    return bool(_dns().get(_DNS_SESSION))


def _dns_set_session(active):
    _dns()[_DNS_SESSION] = bool(active)
    _ctypes_bundle()["active"].value = 1 if active else 0


def _dns_hwnd():
    hwnd = _dns().get(_DNS_HWND)
    return hwnd if hwnd else None


def _dns_set_hwnd(hwnd):
    _dns()[_DNS_HWND] = int(hwnd) if hwnd else None
    _ctypes_bundle()["hwnd"].value = int(hwnd) if hwnd else 0


def _dns_last():
    try:
        return int(_dns().get(_DNS_LAST, -1))
    except (TypeError, ValueError):
        return -1


def _dns_set_last(value):
    _dns()[_DNS_LAST] = int(value)


# --- apply / clear ---------------------------------------------------------

def _apply_value(completed):
    if not _dns_session():
        return False
    if not _ensure_com():
        return False
    hwnd = _lock_hwnd(force=False)
    if not hwnd or _set_value_fn is None:
        return False
    if not _dns_session():
        return False
    try:
        hr = _set_value_fn(_tb_ptr, hwnd, int(completed), 10000)
        return hr in (_S_OK, _S_FALSE)
    except (OSError, TypeError, ValueError, AttributeError) as err:
        config.debug_print(f"[Atomic Debug] Taskbar apply({completed}) failed: {err}")
        return False


def _call_orig_set_state(this, hwnd, state):
    """Call the real COM SetProgressState by saved address only."""
    addr = _dns().get(_DNS_ORIG_ADDR)
    if not isinstance(addr, int) or not addr:
        bundle_addr = _ctypes_bundle()["orig_addr"].value
        addr = int(bundle_addr) if bundle_addr else 0
    if not addr:
        return _S_OK
    try:
        return _StateFn(addr)(this, hwnd, state)
    except OSError:
        return _S_OK


def _hook_addr():
    cb = _dns().get(_DNS_HOOK_CB)
    if cb is None:
        return None
    try:
        return ctypes.cast(cb, ctypes.c_void_p).value
    except (TypeError, ValueError, AttributeError):
        return None


def _restore_orig_slot():
    """
    Put the real COM SetProgressState back in the shared vtable.

    After addon-reload flailing the slot can point at a dead ctypes trampoline
    that returns S_OK without clearing — Explorer then keeps a full/stuck bar.
    Clearing only works reliably once the real slot is restored.
    """
    dns = _dns()
    slot_addr = dns.get(_DNS_SLOT_ADDR)
    orig = dns.get(_DNS_ORIG_ADDR)
    if not isinstance(slot_addr, int) or not slot_addr:
        return False
    if not isinstance(orig, int) or not orig:
        return False
    hook = _hook_addr()
    if hook and orig == hook:
        # Poisoned "orig" — refuse to write the hook back as COM.
        return False
    if not _patch_vtable_slot(slot_addr, orig):
        return False
    dns[_DNS_HOOKED] = False
    return True


def _ensure_hook_armed():
    """Re-install the clear-hook after end() restored the real COM slot."""
    if _tb_ptr is None:
        return
    try:
        vtable = ctypes.cast(
            ctypes.cast(_tb_ptr, ctypes.POINTER(ctypes.c_void_p))[0],
            ctypes.POINTER(ctypes.c_void_p),
        )
        _install_clear_hook(vtable)
    except (AttributeError, OSError, TypeError, ValueError):
        pass


def _clear_taskbar_now():
    """Blank the taskbar overlay. Re-resolve HWND if the cached one died."""
    if not _ensure_com() or _tb_ptr is None:
        _dns_set_hwnd(None)
        return

    hwnd = _dns_hwnd()
    user32 = ctypes.windll.user32
    if not hwnd or not user32.IsWindow(hwnd):
        hwnd = _lock_hwnd(force=True)

    # Unhook first so NOPROGRESS hits real shell32, not a stale trampoline.
    _restore_orig_slot()

    try:
        if hwnd:
            _call_orig_set_state(_tb_ptr, hwnd, _TBPF_NOPROGRESS)
            # Belt-and-suspenders: also via live vtable slot after restore.
            try:
                vtable = ctypes.cast(
                    ctypes.cast(_tb_ptr, ctypes.POINTER(ctypes.c_void_p))[0],
                    ctypes.POINTER(ctypes.c_void_p),
                )
                _StateFn(int(vtable[10]))(_tb_ptr, hwnd, _TBPF_NOPROGRESS)
            except (OSError, TypeError, ValueError, AttributeError):
                pass
    except (OSError, TypeError, ValueError, AttributeError) as err:
        config.debug_print(f"[Atomic Debug] Taskbar clear failed: {err}")
    finally:
        _dns_set_hwnd(None)


# --- COM hook (once per Blender process) -----------------------------------

def _make_clear_hook(bundle):
    """Build the SetProgressState trampoline closed over process-lifetime cells."""
    c_active = bundle["active"]
    c_hwnd = bundle["hwnd"]
    c_orig = bundle["orig_addr"]
    c_reenter = bundle["reenter"]

    @_StateFn
    def _hooked_set_state(this, hwnd, state):
        # Swallow Blender idle clears while Atomic owns the bar.
        # Use only ctypes cells — no module globals (reload-safe).
        try:
            if c_reenter.value:
                # Nested call — forward to real COM, never fake S_OK (that
                # permanently sticks Explorer's green bar after a bad reload).
                addr = c_orig.value
                if not addr:
                    return _S_OK
                return _StateFn(addr)(this, hwnd, state)

            c_reenter.value = 1
            try:
                if (
                    state == _TBPF_NOPROGRESS
                    and c_active.value
                    and c_hwnd.value
                    and int(hwnd) == int(c_hwnd.value)
                ):
                    return _S_OK
                addr = c_orig.value
                if not addr:
                    return _S_OK
                return _StateFn(addr)(this, hwnd, state)
            finally:
                c_reenter.value = 0
        except Exception:
            c_reenter.value = 0
            return _S_OK

    return _hooked_set_state


def _patch_vtable_slot(slot_addr, new_addr):
    kernel32 = ctypes.windll.kernel32
    old_protect = wintypes.DWORD()
    size = ctypes.sizeof(ctypes.c_void_p)
    if not kernel32.VirtualProtect(
        ctypes.c_void_p(slot_addr), size, _PAGE_EXECUTE_READWRITE, ctypes.byref(old_protect)
    ):
        return False
    ctypes.c_void_p.from_address(slot_addr).value = new_addr
    kernel32.VirtualProtect(
        ctypes.c_void_p(slot_addr), size, old_protect.value, ctypes.byref(old_protect)
    )
    return True


def _resolve_orig_addr(vtable):
    """True COM SetProgressState address — never a Python ctypes hook."""
    dns = _dns()
    saved = dns.get(_DNS_ORIG_ADDR)
    if isinstance(saved, int) and saved:
        return saved

    hook_addrs = set()
    cb = dns.get(_DNS_HOOK_CB)
    if cb is not None:
        try:
            hook_addrs.add(ctypes.cast(cb, ctypes.c_void_p).value)
        except (TypeError, ValueError, AttributeError):
            pass

    cur = int(vtable[10])
    if cur and cur not in hook_addrs:
        dns[_DNS_ORIG_ADDR] = cur
        return cur

    config.debug_print(
        "[Atomic Debug] Taskbar clear-hook: COM orig unavailable "
        "(restart Blender if the taskbar bar flaps)"
    )
    return None


def _install_clear_hook(vtable):
    """Install exactly one process-wide SetProgressState hook."""
    if sys.platform != "win32":
        return

    dns = _dns()
    bundle = _ctypes_bundle()
    vtable_addr = ctypes.cast(
        ctypes.cast(_tb_ptr, ctypes.POINTER(ctypes.c_void_p))[0],
        ctypes.c_void_p,
    ).value
    slot_addr = vtable_addr + 10 * ctypes.sizeof(ctypes.c_void_p)
    dns[_DNS_SLOT_ADDR] = slot_addr

    orig_addr = _resolve_orig_addr(vtable)
    if not orig_addr:
        return

    dns[_DNS_ORIG_ADDR] = orig_addr
    bundle["orig_addr"].value = orig_addr

    existing_cb = dns.get(_DNS_HOOK_CB)
    if existing_cb is not None and dns.get(_DNS_HOOKED):
        try:
            existing_addr = ctypes.cast(existing_cb, ctypes.c_void_p).value
            if int(vtable[10]) != existing_addr:
                # Slot drifted — re-bind to the kept callback.
                _patch_vtable_slot(slot_addr, existing_addr)
                config.debug_print("[Atomic Debug] Taskbar clear-hook re-bound")
            return
        except (TypeError, ValueError, AttributeError, OSError):
            pass

    hook_cb = _make_clear_hook(bundle)
    dns[_DNS_HOOK_CB] = hook_cb
    hook_addr = ctypes.cast(hook_cb, ctypes.c_void_p).value

    if not _patch_vtable_slot(slot_addr, hook_addr):
        config.debug_print("[Atomic Debug] Taskbar clear-hook VirtualProtect failed")
        return

    dns[_DNS_HOOKED] = True
    config.debug_print("[Atomic Debug] Taskbar clear-hook installed")


# --- COM / HWND ------------------------------------------------------------

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
    """Create ITaskbarList3 once and ensure the process-wide clear-hook exists."""
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
        _tb_ptr = ptr

        _install_clear_hook(vtable)
        hook_cb = _dns().get(_DNS_HOOK_CB)
        _set_state_fn = hook_cb if hook_cb is not None else _StateFn(vtable[10])
        return True
    except (AttributeError, OSError, TypeError, ValueError) as err:
        config.debug_print(f"[Atomic Debug] Taskbar COM init failed: {err}")
        _tb_ptr = None
        _set_value_fn = None
        _set_state_fn = None
        return False


def _lock_hwnd(force=False):
    """Pin the main GHOST window for the session."""
    user32 = ctypes.windll.user32
    if not force:
        hwnd = _dns_hwnd()
        if hwnd and user32.IsWindow(hwnd):
            return hwnd

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
        if user32.GetWindow(hwnd, 4):
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
    _dns_set_hwnd(best)
    return best

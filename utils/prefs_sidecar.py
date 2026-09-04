"""
Sidecar JSON for Atomic addon preferences.

Blender frees AddonPreferences on disable/enable (VS Code Reload Addons),
so values that must survive reloads are mirrored here under the user CONFIG
directory.
"""

import json
import os

import bpy

from .. import config

SIDECAR_VERSION = 1
SIDECAR_FILENAME = "atomic_data_manager_prefs.json"

# Skip writes while applying a restore (avoids feedback loops).
_restoring = False
_last_written = None


def is_restoring():
    """True while apply_snapshot is writing into AddonPreferences."""
    return _restoring


def sidecar_path():
    """Absolute path to the preferences sidecar JSON file."""
    base = bpy.utils.user_resource("CONFIG")
    if not base:
        return None
    return os.path.join(base, SIDECAR_FILENAME)


def prefs_snapshot(prefs):
    """
    Build a JSON-serialisable dict from AddonPreferences.

    Imported lazily to avoid circular imports with preferences_ui.
    """
    from ..ui.preferences_ui import (
        get_prefs_filename_equivalents,
        get_prefs_search_paths,
    )

    if prefs is None:
        return None

    return {
        "version": SIDECAR_VERSION,
        "enable_missing_file_warning": bool(prefs.enable_missing_file_warning),
        "include_fake_users": bool(prefs.include_fake_users),
        "enable_pie_menu_ui": bool(prefs.enable_pie_menu_ui),
        "enable_debug_prints": bool(prefs.enable_debug_prints),
        "storage_navigate_frame_view": bool(prefs.storage_navigate_frame_view),
        "safe_clean_empty_scene": bool(prefs.safe_clean_empty_scene),
        "remap_search_paths": list(get_prefs_search_paths(prefs)),
        "remap_filename_equivalents": [
            [a, b] for a, b in get_prefs_filename_equivalents(prefs)
        ],
        "pie_menu_type": str(prefs.pie_menu_type),
        "pie_menu_alt": bool(prefs.pie_menu_alt),
        "pie_menu_any": bool(prefs.pie_menu_any),
        "pie_menu_ctrl": bool(prefs.pie_menu_ctrl),
        "pie_menu_oskey": bool(prefs.pie_menu_oskey),
        "pie_menu_shift": bool(prefs.pie_menu_shift),
    }


def apply_snapshot(data, prefs):
    """Apply a sidecar dict onto AddonPreferences (in-memory only)."""
    from ..ui.preferences_ui import (
        set_prefs_filename_equivalents,
        set_prefs_search_paths,
    )

    if not data or prefs is None:
        return False

    global _restoring
    _restoring = True
    try:
        for key in (
            "enable_missing_file_warning",
            "include_fake_users",
            "enable_pie_menu_ui",
            "enable_debug_prints",
            "storage_navigate_frame_view",
            "safe_clean_empty_scene",
            "pie_menu_type",
            "pie_menu_alt",
            "pie_menu_any",
            "pie_menu_ctrl",
            "pie_menu_oskey",
            "pie_menu_shift",
        ):
            if key in data and hasattr(prefs, key):
                try:
                    setattr(prefs, key, data[key])
                except Exception as e:
                    config.debug_print(
                        f"[Atomic Debug] Sidecar skip {key}: {e}"
                    )

        if "remap_search_paths" in data:
            set_prefs_search_paths(
                data.get("remap_search_paths") or [],
                prefs=prefs,
                ensure_one=True,
            )

        pairs = []
        for item in data.get("remap_filename_equivalents") or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                pairs.append((item[0], item[1]))
        set_prefs_filename_equivalents(pairs, prefs=prefs, ensure_one=True)
        return True
    finally:
        _restoring = False


def load_sidecar():
    """Read sidecar JSON; return dict or None."""
    path = sidecar_path()
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return None
        return data
    except Exception as e:
        print(f"[Atomic] Could not read prefs sidecar {path}: {e}")
        return None


def save_sidecar(prefs=None):
    """
    Write current preferences to the sidecar if they changed.

    Returns True when a write occurred.
    """
    global _last_written

    if _restoring:
        return False

    from ..ui.preferences_ui import _get_addon_prefs

    prefs = prefs or _get_addon_prefs()
    path = sidecar_path()
    if not prefs or not path:
        return False

    snapshot = prefs_snapshot(prefs)
    if snapshot is None:
        return False

    encoded = json.dumps(snapshot, indent=2, sort_keys=True)
    if encoded == _last_written:
        return False

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.write("\n")
        _last_written = encoded
        config.debug_print(f"[Atomic Debug] Wrote prefs sidecar: {path}")
        return True
    except Exception as e:
        print(f"[Atomic] Could not write prefs sidecar {path}: {e}")
        return False


def restore_sidecar_into_prefs(prefs=None):
    """
    Load sidecar and apply to AddonPreferences.

    Call from register() after AddonPreferences is available.
    """
    from ..ui.preferences_ui import _get_addon_prefs

    prefs = prefs or _get_addon_prefs()
    data = load_sidecar()
    if not data or not prefs:
        return False

    ok = apply_snapshot(data, prefs)
    if ok:
        # Cache so the immediate post-restore save is a no-op.
        global _last_written
        try:
            _last_written = json.dumps(
                prefs_snapshot(prefs), indent=2, sort_keys=True
            )
        except Exception:
            _last_written = None
        config.debug_print(
            f"[Atomic Debug] Restored prefs from sidecar: {sidecar_path()}"
        )
    return ok

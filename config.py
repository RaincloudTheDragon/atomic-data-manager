"""
Copyright (C) 2019 Remington Creative

This file is part of Atomic Data Manager.

Atomic Data Manager is free software: you can redistribute
it and/or modify it under the terms of the GNU General Public License
as published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

Atomic Data Manager is distributed in the hope that it will
be useful, but WITHOUT ANY WARRANTY; without even the implied warranty
of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
General Public License for more details.

You should have received a copy of the GNU General Public License along
with Atomic Data Manager.  If not, see <https://www.gnu.org/licenses/>.

---

This file contains global copies of Atomic's preferences so that they
can be easily access throughout the add-on.

NOTE:
Changing the values of these variables will NOT change the values in the
Atomic's preferences. If you want to change a setting, change it in
Blender, not in here.

"""

# visible atomic preferences
enable_missing_file_warning = True
include_fake_users = False
enable_pie_menu_ui = True
enable_debug_prints = False
storage_navigate_frame_view = False
# Switch windows to an empty scene during Clean/Nuke bulk removes (default on)
safe_clean_empty_scene = True
# Semicolon-separated folders for missing-library Search (remap) defaults
remap_search_roots = ""
# Semicolon-separated missing=equivalent basename pairs for remap Search
remap_filename_equivalents = ""

# hidden atomic preferences
pie_menu_type = "D"
pie_menu_alt = False
pie_menu_any = False
pie_menu_ctrl = False
pie_menu_oskey = False
pie_menu_shift = False

# End the current scan batch after an ID that exceeds this (seconds) so the
# status bar can name it — one extra timer tick per slow ID, not per item.
SCAN_HANG_THRESHOLD_SEC = 0.5


def note_scan_hang(state, phase, name, elapsed):
    """Record a slow scan ID on state and log it (when debug prints are on)."""
    if state is not None:
        state['hang_phase'] = phase
        state['hang_name'] = name
        state['hang_elapsed'] = float(elapsed)
    debug_print(
        f"[Atomic Debug] SLOW {phase}: '{name}' took {elapsed:.2f}s"
    )


def hang_status_suffix(state):
    """Status-bar fragment when the last batch ended on a slow ID."""
    if not state:
        return ""
    name = state.get('hang_name')
    if not name:
        return ""
    elapsed = state.get('hang_elapsed')
    if elapsed is None:
        return f" — slow: {name}"
    return f" — slow: {name} ({elapsed:.1f}s)"


def debug_print(*args, **kwargs):
    """
    Print debug messages only if enable_debug_prints is True.
    Usage: debug_print("message") or debug_print(f"formatted {value}")

    Reads the live module attribute so `config.enable_debug_prints = ...`
    from preferences sync is always honoured.

    Always flushes so mid-batch scan lines show the current ID before a hang
    (stdout otherwise buffers until the timer tick ends).
    """
    if globals().get("enable_debug_prints"):
        kwargs.setdefault("flush", True)
        print(*args, **kwargs)
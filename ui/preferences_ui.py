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

This file contains the Atomic preferences UI, preferences properties, and
some functions for syncing the preference properties with external factors.

"""

import bpy
import os
from bpy.utils import register_class
from ..utils import compat
from .. import config
import sys
# updater removed in Blender 4.5 extension format

# Get the root module name dynamically
def _get_addon_module_name():
    """Get the root addon module name for bl_idname."""
    # In Blender 5.0 extensions loaded via VSCode, the module name is the full path
    # e.g., "bl_ext.vscode_development.atomic_data_manager"
    # We need to get it from the parent package (atomic_data_manager)
    try:
        # Get parent package name from __package__ (remove .ui suffix)
        if __package__:
            parent_pkg = __package__.rsplit('.', 1)[0] if '.' in __package__ else __package__
            # Get the actual module from sys.modules to get its __name__
            parent_module = sys.modules.get(parent_pkg)
            if parent_module and hasattr(parent_module, '__name__'):
                module_name = parent_module.__name__
                config.debug_print(f"[Atomic Debug] Using parent module __name__ as bl_idname: {module_name}")
                return module_name
            else:
                # Use the package name directly
                config.debug_print(f"[Atomic Debug] Using parent package name as bl_idname: {parent_pkg}")
                return parent_pkg
    except Exception as e:
        config.debug_print(f"[Atomic Debug] Could not get parent module name: {e}")
    
    # Last fallback
    module_name = "atomic_data_manager"
    config.debug_print(f"[Atomic Debug] Using fallback bl_idname: {module_name}")
    return module_name


def _get_addon_prefs():
    # robustly find our AddonPreferences instance regardless of module name
    prefs = bpy.context.preferences
    for addon in prefs.addons.values():
        ap = getattr(addon, "preferences", None)
        if ap and hasattr(ap, "bl_idname") and ap.bl_idname == ATOMIC_PT_preferences_panel.bl_idname:
            return ap
        # fallback: match by known property
        if ap and hasattr(ap, "enable_missing_file_warning"):
            return ap
    return None


def _save_after_pref_change():
    """
    Persist user preferences after programmatic updates.
    """
    bpy.ops.wm.save_userpref()


def set_enable_missing_file_warning(value):
    """
    Programmatically toggle the missing file warning preference.
    """
    ap = _get_addon_prefs()
    if not ap:
        return
    ap.enable_missing_file_warning = value
    copy_prefs_to_config(None, None)
    _save_after_pref_change()


def set_include_fake_users(value):
    """
    Programmatically toggle inclusion of fake users.
    """
    ap = _get_addon_prefs()
    if not ap:
        return
    ap.include_fake_users = value
    copy_prefs_to_config(None, None)
    _save_after_pref_change()


def set_enable_pie_menu_ui(value):
    """
    Programmatically toggle the pie menu UI preference.
    """
    ap = _get_addon_prefs()
    if not ap:
        return
    ap.enable_pie_menu_ui = value
    copy_prefs_to_config(None, None)
    _save_after_pref_change()




def copy_prefs_to_config(self, context):
    # copies the values of Atomic's preferences to the variables in
    # config.py for global use

    atomic_preferences = _get_addon_prefs()
    if not atomic_preferences:
        return

    # visible atomic preferences
    config.enable_missing_file_warning = \
        atomic_preferences.enable_missing_file_warning

    config.enable_pie_menu_ui = \
        atomic_preferences.enable_pie_menu_ui

    config.include_fake_users = \
        atomic_preferences.include_fake_users

    config.enable_debug_prints = \
        atomic_preferences.enable_debug_prints

    config.single_threaded_image_deep_scan = \
        atomic_preferences.single_threaded_image_deep_scan

    config.max_deep_scan_workers = \
        atomic_preferences.max_deep_scan_workers

    # hidden atomic preferences
    config.pie_menu_type = \
        atomic_preferences.pie_menu_type

    config.pie_menu_alt = \
        atomic_preferences.pie_menu_alt

    config.pie_menu_any = \
        atomic_preferences.pie_menu_any

    config.pie_menu_ctrl = \
        atomic_preferences.pie_menu_ctrl

    config.pie_menu_oskey = \
        atomic_preferences.pie_menu_oskey

    config.pie_menu_shift = \
        atomic_preferences.pie_menu_shift


def update_pie_menu_hotkeys(self, context):
    atomic_preferences = _get_addon_prefs()
    if not atomic_preferences:
        return

    # add the hotkeys if the preference is enabled
    if atomic_preferences.enable_pie_menu_ui:
        add_pie_menu_hotkeys()

    # remove the hotkeys otherwise
    else:
        remove_pie_menu_hotkeys()


def add_pie_menu_hotkeys():
    # adds the pie menu hotkeys to blender's addon keymaps

    global keymaps
    keyconfigs = bpy.context.window_manager.keyconfigs.addon

    # check to see if a window keymap already exists
    if "Window" in keyconfigs.keymaps.keys():
        km = keyconfigs.keymaps['Window']

    # if not, crate a new one
    else:
        km = keyconfigs.keymaps.new(
            name="Window",
            space_type='EMPTY',
            region_type='WINDOW'
        )

    # add a new keymap item to that keymap
    kmi = km.keymap_items.new(
        idname="atomic.invoke_pie_menu_ui",
        type=config.pie_menu_type,
        value="PRESS",
        alt=config.pie_menu_alt,
        any=config.pie_menu_any,
        ctrl=config.pie_menu_ctrl,
        oskey=config.pie_menu_oskey,
        shift=config.pie_menu_shift,
    )

    # # point the keymap item to our pie menu
    # kmi.properties.name = "ATOMIC_MT_main_pie"
    keymaps.append((km, kmi))


def remove_pie_menu_hotkeys():
    # removes the pie menu hotkeys from blender's addon keymaps if they
    # exist there

    global keymaps

    # remove each hotkey in our keymaps list if it exists in blenders
    # addon keymaps
    for km, kmi in keymaps:
        km.keymap_items.remove(kmi)

    # clear our keymaps list
    keymaps.clear()


# Atomic Data Manager Preference Panel UI
class ATOMIC_PT_preferences_panel(bpy.types.AddonPreferences):
    # bl_idname must match the add-on's module name exactly
    # Get it dynamically to ensure it matches what Blender registered
    bl_idname = _get_addon_module_name()

    # visible atomic preferences
    enable_missing_file_warning: bpy.props.BoolProperty(
        description="Display a warning on startup if Atomic detects "
                    "missing files in your project",
        default=True
    )

    include_fake_users: bpy.props.BoolProperty(
        description="Include data-blocks with only fake users in unused "
                    "data detection",
        default=False
    )

    enable_pie_menu_ui: bpy.props.BoolProperty(
        description="Enable the Atomic pie menu UI, so you can clean "
                    "your project from anywhere.",
        default=True,
        update=update_pie_menu_hotkeys
    )

    enable_debug_prints: bpy.props.BoolProperty(
        description="Enable debug print statements in the console",
        default=False
    )

    single_threaded_image_deep_scan: bpy.props.BoolProperty(
        description="Use slower but more stable single-threaded deep scan",
        default=False
    )

    max_deep_scan_workers: bpy.props.IntProperty(
        description="Maximum number of parallel Blender worker processes for deep scan",
        default=4,
        min=1,
        max=max(1, os.cpu_count() or 4)  # Dynamic max: all machine threads
    )

    # hidden atomic preferences
    pie_menu_type: bpy.props.StringProperty(
        default="D"
    )

    pie_menu_alt: bpy.props.BoolProperty(
        default=False
    )

    pie_menu_any: bpy.props.BoolProperty(
        default=False
    )

    pie_menu_ctrl: bpy.props.BoolProperty(
        default=False
    )

    pie_menu_oskey: bpy.props.BoolProperty(
        default=False
    )

    pie_menu_shift: bpy.props.BoolProperty(
        default=False
    )

    # updater properties removed

    def draw(self, context):
        layout = self.layout
        
        # Debug: verify draw is being called
        config.debug_print("[Atomic Debug] Preferences draw() method called")

        split = layout.split()

        # left column
        col = split.column()

        # enable missing file warning toggle
        col.prop(
            self,
            "enable_missing_file_warning",
            text="Show Missing File Warning"
        )

        # right column
        col = split.column()

        # ignore fake users toggle
        col.prop(
            self,
            "include_fake_users",
            text="Include Fake Users"
        )

        # enable debug prints toggle
        col.prop(
            self,
            "enable_debug_prints",
            text="Enable Debug Prints"
        )

        # Image deep scan settings
        col.separator()
        col.label(text="Image Deep Scan:", icon='IMAGE_DATA')
        col.prop(
            self,
            "single_threaded_image_deep_scan",
            text="Single-Threaded Mode (Slower, More Stable)"
        )
        col.prop(
            self,
            "max_deep_scan_workers",
            text="Max Worker Processes"
        )

        # pie menu settings
        pie_split = col.split(factor=0.55)  # nice

        # enable pie menu ui toggle
        pie_split.prop(
            self,
            "enable_pie_menu_ui",
            text="Enable Pie Menu"
        )

        # put the property in a row so it can be disabled
        pie_row = pie_split.row()
        pie_row.enabled = self.enable_pie_menu_ui

        if pie_row.enabled:
            # keymap item that contains our pie menu hotkey
            # note: keymap item index hardcoded with an index -- may be
            # dangerous if more keymap items are added
            kmi = bpy.context.window_manager.keyconfigs.addon.keymaps[
                'Window'].keymap_items[0]

            # hotkey property
            pie_row.prop(
                kmi,
                "type",
                text="",
                full_event=True
            )

            # update hotkey preferences
            self.pie_menu_type = kmi.type
            self.pie_menu_any = kmi.any
            self.pie_menu_alt = kmi.alt
            self.pie_menu_ctrl = kmi.ctrl
            self.pie_menu_oskey = kmi.oskey
            self.pie_menu_shift = kmi.shift

        separator = layout.row()  # extra space

        # updater UI removed

        # update config with any new preferences
        copy_prefs_to_config(None, None)


reg_list = [ATOMIC_PT_preferences_panel]
keymaps = []


def register():
    for cls in reg_list:
        try:
            register_class(cls)
            config.debug_print(f"[Atomic Debug] Registered preferences class: {cls.__name__} with bl_idname: {cls.bl_idname}")
        except Exception as e:
            print(f"[Atomic Error] Failed to register preferences class {cls.__name__}: {e}")
            import traceback
            traceback.print_exc()

    # make sure global preferences are updated on registration
    copy_prefs_to_config(None, None)

    # update keymaps
    add_pie_menu_hotkeys()


def unregister():
    for cls in reg_list:
        compat.safe_unregister_class(cls)

    remove_pie_menu_hotkeys()

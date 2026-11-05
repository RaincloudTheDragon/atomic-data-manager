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

This file contains the user interface for the missing file dialog that
pops up when missing files are detected on file load.

"""

import bpy
from bpy.utils import register_class
from ..utils import compat
from bpy.app.handlers import persistent
from .. import config
from ..stats import missing
from .utils import ui_layouts

# Popup regions for the detect-missing props dialog. area.tag_redraw alone
# does not refresh invoke_props_dialog — need Region.tag_refresh_ui (4.2+).
_detect_popup_regions = set()


def _region_still_exists(region):
    """True if region is still a live UI region."""
    if region is None:
        return False
    try:
        with bpy.context.temp_override(region=region):
            return True
    except (TypeError, ReferenceError, AttributeError):
        return False


def _remember_detect_popup_region(context):
    """Cache the detect-missing props-dialog region for Refresh."""
    region = getattr(context, "region_popup", None)
    if region is not None:
        _detect_popup_regions.add(region)


def _tag_detect_ui_redraw():
    """Force the open detect-missing dialog to redraw with fresh lists."""
    for region in list(_detect_popup_regions):
        if not _region_still_exists(region):
            _detect_popup_regions.discard(region)
            continue
        try:
            region.tag_redraw()
            if hasattr(region, "tag_refresh_ui"):
                region.tag_refresh_ui()
        except (ReferenceError, AttributeError):
            _detect_popup_regions.discard(region)

    try:
        screen = bpy.context.screen
        if screen is not None:
            for area in screen.areas:
                area.tag_redraw()
    except (AttributeError, ReferenceError):
        pass


def _warp_cursor_to_area_center(context, prefer_area_type="VIEW_3D") -> None:
    """Best-effort: move cursor to area center so popups appear centered.

    Blender's popup placement is often tied to the last event mouse position.
    Warping is a hack, but it's the only reliable way to 'center' popups in some contexts.
    """
    win = getattr(context, "window", None)
    screen = getattr(context, "screen", None)
    if win is None or screen is None:
        return

    area = None
    for a in screen.areas:
        if a.type == prefer_area_type:
            area = a
            break
    if area is None and screen.areas:
        area = screen.areas[0]
    if area is None:
        return

    try:
        # cursor_warp expects WINDOW-relative coordinates (0,0 at window bottom-left),
        # not OS desktop coordinates. Warping to the window center is the most
        # reliable option across layouts.
        x = int(win.width / 2)
        y = int(win.height / 2)
        win.cursor_warp(x, y)
    except Exception:
        pass


def _has_any_missing_files():
    """True when session-missing or nested-missing lists are non-empty."""
    return bool(
        missing.images()
        or missing.libraries()
        or missing.libraries_with_nested_missing()
    )


# Atomic Data Manager Detect Missing Files Popup
class ATOMIC_OT_detect_missing(bpy.types.Operator):
    """Detect missing files in this project"""
    bl_idname = "atomic.detect_missing"
    bl_label = "Missing File Detection"

    # Autodetect on load should not open an empty "none found" dialog
    silent_if_empty: bpy.props.BoolProperty(
        name="Silent If Empty",
        default=False,
        options={'SKIP_SAVE', 'HIDDEN'},
    )

    def draw(self, context):
        layout = self.layout

        # Keep popup region so Refresh can tag_refresh_ui (not a new dialog).
        _remember_detect_popup_region(context)

        # Live query each redraw so Refresh / Relink results show without
        # depending on a stored operator instance (that goes stale after Search).
        missing_images = missing.images()
        missing_libraries = missing.libraries()
        nested_libs = missing.libraries_with_nested_missing()
        has_session_missing = bool(missing_images or missing_libraries)
        has_nested_missing = bool(nested_libs)

        # Session-local missing files (relinkable in this blend)
        if has_session_missing:

            # header warning
            row = layout.row()
            row.label(
                text="Atomic has detected one or more missing files in "
                     "your project!"
            )

            # missing images box list (copy icon: hover = path, click = clipboard)
            if missing_images:
                ui_layouts.box_list_missing_paths(
                    layout=layout,
                    title="Images",
                    items=missing_images,
                    kind="IMAGE",
                    icon="IMAGE_DATA",
                    columns=3
                )

            # missing libraries box list (hover = expected path, click = copy)
            if missing_libraries:
                ui_layouts.box_list_missing_paths(
                    layout=layout,
                    title="Libraries",
                    items=missing_libraries,
                    kind="LIBRARY",
                    icon="LIBRARY_DATA_DIRECT",
                    columns=3
                )

            row = layout.separator()  # extra space

            # recovery option buttons
            row = layout.row()
            row.label(text="What would you like to do?")

            row = layout.row()
            row.scale_y = 1.5
            row.operator("atomic.reload_missing", text="Reload", icon="FILE_REFRESH")
            row.operator("atomic.remove_missing", text="Remove", icon="TRASH")
            row.operator("atomic.search_missing", text="Search", icon="VIEWZOOM")
            row.operator("atomic.replace_missing", text="Replace", icon="FILEBROWSER")

        # Linked libraries with nested missing deps — open source, do not relink here
        if has_nested_missing:
            if has_session_missing:
                layout.separator()

            col = layout.column(align=True)
            row = col.row()
            row.alert = True
            row.label(text="Linked libraries with missing dependencies", icon='ERROR')
            row = col.row()
            row.enabled = False
            row.label(text="Cannot relink from this session — edit the source file.")

            box = layout.box()
            for entry in nested_libs:
                row = box.row(align=True)
                count = entry.get("missing_count", 0)
                label = entry.get("library_basename") or entry.get("library_key") or "?"
                chain = entry.get("link_chain") or []
                # Show parent→… only when this lib is indirect (chain longer than itself)
                if len(chain) > 1:
                    label = " → ".join(chain)
                examples = entry.get("missing_examples") or []
                tip = examples[0]["name"] if examples else ""
                # Prefer first nested miss path for hover/copy; fall back to the source .blend
                copy_path = ""
                if examples:
                    copy_path = missing.nested_example_abspath(entry, examples[0])
                if not copy_path:
                    copy_path = entry.get("library_filepath") or ""
                # Label for the row text — avoids stealing dialog accelerators from Open/Search/etc.
                row.label(
                    text=f"{label}  ({count} missing" + (f": {tip}" if tip else "") + ")",
                    icon='LIBRARY_DATA_DIRECT',
                )
                copy_op = row.operator(
                    "atomic.copy_missing_path",
                    text="",
                    icon='COPYDOWN',
                    emboss=False,
                )
                copy_op.filepath = copy_path
                copy_op.item_name = tip or label
                op = row.operator(
                    "atomic.open_library_source",
                    text="Open",
                    icon='FILE_BLEND',
                )
                op.filepath = entry.get("library_filepath") or ""

        # missing files interface if no missing files are found
        if not has_session_missing and not has_nested_missing:
            row = layout.row()
            row.label(text="No missing files were found!")

            # empty box list
            ui_layouts.box_list(
                layout=layout
            )

        # Refresh button — redraws this dialog in place
        row = layout.row()
        row.operator(
            "atomic.detect_missing_refresh",
            text="Refresh",
            icon="FILE_REFRESH",
        )

        row = layout.separator()  # extra space

    def execute(self, context):
        # Buttons now directly invoke operators, so execute just closes the dialog
        # IGNORE is the default behavior (no action taken)
        return {'FINISHED'}

    def invoke(self, context, event):
        # Snapshot for dialog width only; draw() re-queries live lists.
        images = missing.images()
        libraries = missing.libraries()
        nested = missing.libraries_with_nested_missing()
        has_missing = bool(images or libraries or nested)

        # Autodetect on file load: never raise an empty confirmation dialog
        if not has_missing and self.silent_if_empty:
            return {'CANCELLED'}

        wm = context.window_manager

        # Force popup placement to screen center (see BlenderArtists reference).
        _warp_cursor_to_area_center(context)

        # invoke large dialog if there are missing files
        if has_missing:
            return wm.invoke_props_dialog(self, width=500)

        # Manual Detect Missing: small dialog reporting none found
        return wm.invoke_props_dialog(self, width=300)


@persistent
def autodetect_missing_files(dummy=None):
    # invokes the detect missing popup when missing files are detected upon
    # loading a new Blender project
    # Use a timer to defer the operator call since load_post handlers
    # cannot directly invoke operators that modify data
    if not config.enable_missing_file_warning:
        return
    if not _has_any_missing_files():
        return

    def invoke_detect_missing():
        # Re-check after load settles (pack-linked archive libs may not be fully wired at load_post, which previously caused an empty popup).
        try:
            if not _has_any_missing_files():
                return None
            bpy.ops.atomic.detect_missing('INVOKE_DEFAULT', silent_if_empty=True)
        except RuntimeError:
            # If still in invalid context, ignore (will be handled on next user action)
            pass
        return None  # Run once

    bpy.app.timers.register(invoke_detect_missing, first_interval=0.1)


# Refresh operator for missing file detection
class ATOMIC_OT_detect_missing_refresh(bpy.types.Operator):
    """Refresh the open Missing File Detection dialog in place"""
    bl_idname = "atomic.detect_missing_refresh"
    bl_label = "Refresh Missing Files"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        # Never spawn a second detect_missing dialog; that was the bug after
        # Search/Relink invalidated the stored operator instance. draw() reads
        # live missing lists; just force the existing props dialog to redraw.
        _tag_detect_ui_redraw()
        self.report({'INFO'}, "Missing files list refreshed")
        return {'FINISHED'}


reg_list = [ATOMIC_OT_detect_missing, ATOMIC_OT_detect_missing_refresh]


def _purge_autodetect_load_post_handlers():
    """Remove every autodetect_missing_files on load_post, including stale hot-reload copies.

    ``importlib.reload`` creates a new function object, so ``handler not in load_post`` keeps appending while unregister only removes the current identity. Stale copies still fire on file load and reopen an empty missing-files dialog.
    """
    for handler in list(bpy.app.handlers.load_post):
        if getattr(handler, "__name__", None) != "autodetect_missing_files":
            continue
        module = getattr(handler, "__module__", "") or ""
        if "atomic_data_manager" not in module:
            continue
        try:
            bpy.app.handlers.load_post.remove(handler)
        except ValueError:
            pass


def register():
    for item in reg_list:
        register_class(item)

    # run missing file auto-detection after loading a Blender file
    _purge_autodetect_load_post_handlers()
    bpy.app.handlers.load_post.append(autodetect_missing_files)


def unregister():
    for item in reg_list:
        compat.safe_unregister_class(item)

    # stop running missing file auto-detection after loading a Blender file
    _purge_autodetect_load_post_handlers()

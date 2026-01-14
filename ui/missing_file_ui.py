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

# Module-level state for detect missing operator instance
_detect_missing_operator_instance = None


# Atomic Data Manager Detect Missing Files Popup
class ATOMIC_OT_detect_missing(bpy.types.Operator):
    """Detect missing files in this project"""
    bl_idname = "atomic.detect_missing"
    bl_label = "Missing File Detection"

    # missing file lists
    missing_images = []
    missing_libraries = []

    def draw(self, context):
        layout = self.layout

        # missing files interface if missing files are found
        if self.missing_images or self.missing_libraries:

            # header warning
            row = layout.row()
            row.label(
                text="Atomic has detected one or more missing files in "
                     "your project!"
            )

            # missing images box list
            if self.missing_images:
                ui_layouts.box_list(
                    layout=layout,
                    title="Images",
                    items=self.missing_images,
                    icon="IMAGE_DATA",
                    columns=3
                )

            # missing libraries box list
            if self.missing_libraries:
                ui_layouts.box_list(
                    layout=layout,
                    title="Libraries",
                    items=self.missing_libraries,
                    icon="LIBRARY_DATA_DIRECT",
                    columns=3
                )

            row = layout.separator()  # extra space

            # recovery option buttons
            row = layout.row()
            row.label(text="What would you like to do?")

            row = layout.row()
            row.scale_y = 1.5
            op_reload = row.operator("atomic.reload_missing", text="Reload", icon="FILE_REFRESH")
            op_remove = row.operator("atomic.remove_missing", text="Remove", icon="TRASH")
            op_search = row.operator("atomic.search_missing", text="Search", icon="VIEWZOOM")
            op_replace = row.operator("atomic.replace_missing", text="Replace", icon="FILEBROWSER")
            
            # Refresh button
            row = layout.row()
            refresh_op = row.operator("atomic.detect_missing_refresh", text="Refresh", icon="FILE_REFRESH")

        # missing files interface if no missing files are found
        else:
            row = layout.row()
            row.label(text="No missing files were found!")

            # empty box list
            ui_layouts.box_list(
                layout=layout
            )

        row = layout.separator()  # extra space

    def execute(self, context):
        # Buttons now directly invoke operators, so execute just closes the dialog
        # IGNORE is the default behavior (no action taken)
        return {'FINISHED'}

    def invoke(self, context, event):
        global _detect_missing_operator_instance
        
        # Store operator instance for refresh functionality
        _detect_missing_operator_instance = self
        
        # Always refresh missing file lists when invoked
        self.missing_images = missing.images()
        self.missing_libraries = missing.libraries()

        wm = context.window_manager

        # invoke large dialog if there are missing files
        if self.missing_images or self.missing_libraries:
            return wm.invoke_props_dialog(self, width=500)

        # invoke small dialog if there are no missing files
        else:
            return wm.invoke_popup(self, width=300)


@persistent
def autodetect_missing_files(dummy=None):
    # invokes the detect missing popup when missing files are detected upon
    # loading a new Blender project
    # Use a timer to defer the operator call since load_post handlers
    # cannot directly invoke operators that modify data
    if config.enable_missing_file_warning and \
            (missing.images() or missing.libraries()):
        def invoke_detect_missing():
            try:
                bpy.ops.atomic.detect_missing('INVOKE_DEFAULT')
            except RuntimeError:
                # If still in invalid context, ignore (will be handled on next user action)
                pass
            return None  # Run once
        
        bpy.app.timers.register(invoke_detect_missing, first_interval=0.1)


# Refresh operator for missing file detection
class ATOMIC_OT_detect_missing_refresh(bpy.types.Operator):
    """Refresh missing file detection"""
    bl_idname = "atomic.detect_missing_refresh"
    bl_label = "Refresh Missing Files"
    bl_options = {'INTERNAL'}
    
    def execute(self, context):
        global _detect_missing_operator_instance
        
        # Update the stored operator instance if it exists and is valid
        if _detect_missing_operator_instance is not None:
            try:
                # Check if operator instance is still valid
                _ = _detect_missing_operator_instance.bl_idname
                
                # Update the missing file lists
                _detect_missing_operator_instance.missing_images = missing.images()
                _detect_missing_operator_instance.missing_libraries = missing.libraries()
                
                # Redraw all areas to refresh the dialog
                for area in context.screen.areas:
                    area.tag_redraw()
                
                self.report({'INFO'}, "Missing files list refreshed")
                return {'FINISHED'}
            except (ReferenceError, AttributeError, TypeError):
                # Operator instance invalidated, clear it
                _detect_missing_operator_instance = None
        
        # If no valid instance, invoke a new dialog
        bpy.ops.atomic.detect_missing('INVOKE_DEFAULT')
        return {'FINISHED'}


reg_list = [ATOMIC_OT_detect_missing, ATOMIC_OT_detect_missing_refresh]


def register():
    for item in reg_list:
        register_class(item)

    # run missing file auto-detection after loading a Blender file
    bpy.app.handlers.load_post.append(autodetect_missing_files)


def unregister():
    for item in reg_list:
        compat.safe_unregister_class(item)

    # stop running missing file auto-detection after loading a Blender file
    bpy.app.handlers.load_post.remove(autodetect_missing_files)

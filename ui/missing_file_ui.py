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


# Atomic Data Manager Detect Missing Files Popup
class ATOMIC_OT_detect_missing(bpy.types.Operator):
    """Detect missing files in this project"""
    bl_idname = "atomic.detect_missing"
    bl_label = "Missing File Detection"

    # missing file lists
    missing_images = []
    missing_libraries = []

    # missing file recovery option enum property
    recovery_option: bpy.props.EnumProperty(
        items=[
            (
                'IGNORE',
                'Ignore Missing Files',
                'Ignore the missing files and leave them offline'
             ),
            (
                'RELOAD',
                'Reload Missing Files',
                'Reload the missing files from their existing file paths'
            ),
            (
                'REMOVE',
                'Remove Missing Files',
                'Remove the missing files from the project'
            ),
            (
                'SEARCH',
                'Search for Missing Files (under development)',
                'Search for the missing files in a directory'
            ),
            (
                'REPLACE',
                'Specify Replacement Files (under development)',
                'Replace missing files with new files'
            ),
            ],
        default='IGNORE'
    )

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

            # recovery option selection
            row = layout.row()
            row.label(text="What would you like to do?")

            row = layout.row()
            row.prop(self, 'recovery_option', text="")

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

        # ignore missing files will take no action

        # reload missing files
        if self.recovery_option == 'RELOAD':
            bpy.ops.atomic.reload_missing('INVOKE_DEFAULT')

        # remove missing files
        elif self.recovery_option == 'REMOVE':
            bpy.ops.atomic.remove_missing('INVOKE_DEFAULT')

            # search for missing files
        elif self.recovery_option == 'SEARCH':
            bpy.ops.atomic.search_missing('INVOKE_DEFAULT')

            # replace missing files
        elif self.recovery_option == 'REPLACE':
            bpy.ops.atomic.replace_missing('INVOKE_DEFAULT')

        return {'FINISHED'}

    def invoke(self, context, event):

        # update missing file lists
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


reg_list = [ATOMIC_OT_detect_missing]


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

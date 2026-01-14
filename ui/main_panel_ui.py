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

This file contains the primary Atomic Data Manager panel that will
appear in the Scene tab of the Properties panel.

This panel contains the Nuke/Clean/Undo buttons as well as the data
category toggles and the category selection tools.

"""

import bpy
from bpy.utils import register_class
from ..utils import compat
from ..stats import count
from .utils import ui_layouts


# Atomic Data Manager Main Panel
class ATOMIC_PT_main_panel(bpy.types.Panel):
    """The main Atomic Data Manager panel"""
    bl_label = "Atomic Data Manager"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"

    def draw(self, context):
        layout = self.layout
        atom = bpy.context.scene.atomic
        category_props = [
            atom.collections,
            atom.images,
            atom.lights,
            atom.materials,
            atom.node_groups,
            atom.objects,
            atom.particles,
            atom.textures,
            atom.armatures,
            atom.worlds
        ]

        # Progress display section (only visible when operation is running)
        if atom.is_operation_running:
            box = layout.box()
            col = box.column(align=True)
            
            # Progress bar with percentage (Blender shows percentage in the bar with PERCENTAGE subtype)
            progress_row = col.row(align=True)
            progress_row.scale_y = 1.5
            progress_row.prop(atom, "operation_progress", text="", slider=True)
            
            # Status text
            if atom.operation_status:
                col.label(text=atom.operation_status, icon='TIME')
            
            # Cancel button
            row = col.row()
            row.scale_y = 1.5
            row.operator("atomic.cancel_operation", text="Cancel", icon='X')
            
            layout.separator()

        # nuke and clean buttons
        row = layout.row(align=True)
        row.scale_y = 2.0
        row.operator("atomic.nuke", text="Nuke", icon="GHOST_ENABLED")
        row.operator("atomic.clean", text="Clean", icon="PARTICLEMODE")
        row.operator("atomic.undo", text="Undo", icon="LOOP_BACK")

        row = layout.row()

        # category toggles
        split = layout.split(align=False)

        # left column
        col = split.column(align=True)

        # collections buttons
        splitcol = col.split(factor=0.8, align=True)

        splitcol.prop(
            atom,
            "collections",
            text="Collections",
            icon='GROUP',
            toggle=True
        )

        splitcol.operator(
            "atomic.inspect_collections",
            icon='VIEWZOOM',
            text=""
        )

        # objects buttons
        splitcol = col.split(factor=0.8, align=True)

        splitcol.prop(
            atom,
            "objects",
            text="Objects",
            icon='OBJECT_DATA',
            toggle=True
        )

        splitcol.operator(
            "atomic.inspect_objects",
            icon='VIEWZOOM',
            text=""
        )

        # lights buttons
        splitcol = col.split(factor=0.8, align=True)

        splitcol.prop(
            atom,
            "lights",
            text="Lights",
            icon='LIGHT',
            toggle=True
        )

        splitcol.operator(
            "atomic.inspect_lights",
            icon='VIEWZOOM',
            text=""
        )

        # node groups buttons
        splitcol = col.split(factor=0.8, align=True)

        splitcol.prop(
            atom,
            "node_groups",
            text="Node Groups",
            icon='NODETREE',
            toggle=True
        )

        splitcol.operator(
            "atomic.inspect_node_groups",
            icon='VIEWZOOM',
            text=""
        )

        # textures button
        splitcol = col.split(factor=0.8, align=True)

        splitcol.prop(
            atom,
            "textures",
            text="Textures",
            icon='TEXTURE',
            toggle=True
        )

        splitcol.operator(
            "atomic.inspect_textures",
            icon='VIEWZOOM',
            text=""
        )

        # right column
        col = split.column(align=True)

        # images buttons (deep scan checkbox, images checkbox, inspect button)
        # Standard split layout for images (matches other categories)
        images_split = col.split(factor=0.8, align=True)
        
        # Images checkbox (will be slightly offset due to deep scan, but inspect aligns)
        images_split.prop(
            atom,
            "images",
            text="Images",
            toggle=True,
            icon='IMAGE_DATA'
        )

        # Inspect button (right, aligns with other inspect buttons)
        images_split.operator(
            "atomic.inspect_images",
            icon='VIEWZOOM',
            text=""
        )

        # materials buttons
        splitcol = col.split(factor=0.8, align=True)

        splitcol.prop(
            atom,
            "materials",
            text="Materials",
            icon='MATERIAL',
            toggle=True
        )

        splitcol.operator(
            "atomic.inspect_materials",
            icon='VIEWZOOM',
            text=""
        )

        # armatures buttons
        splitcol = col.split(factor=0.8, align=True)

        splitcol.prop(
            atom,
            "armatures",
            text="Armatures",
            icon='ARMATURE_DATA',
            toggle=True
        )

        splitcol.operator(
            "atomic.inspect_armatures",
            icon='VIEWZOOM',
            text=""
        )

        # particles buttons
        splitcol = col.split(factor=0.8, align=True)

        splitcol.prop(
            atom,
            "particles",
            text="Particles",
            icon='PARTICLES',
            toggle=True
        )

        splitcol.operator(
            "atomic.inspect_particles",
            icon='VIEWZOOM',
            text=""
        )

        # worlds buttons
        splitcol = col.split(factor=0.8, align=True)
        splitcol.prop(
            atom,
            "worlds",
            text="Worlds",
            icon='WORLD',
            toggle=True
        )

        splitcol.operator(
            "atomic.inspect_worlds",
            icon='VIEWZOOM',
            text=""
        )

        # selection operators
        row = layout.row(align=True)

        row.operator(
            "atomic.smart_select",
            text='Smart Select',
            icon='ZOOM_SELECTED'
        )

        if all(prop is True for prop in category_props):
            row.operator(
                "atomic.deselect_all",
                text="Deselect All",
                icon='RESTRICT_SELECT_ON'
            )

        else:
            row.operator(
                "atomic.select_all",
                text="Select All",
                icon='RESTRICT_SELECT_OFF'
            )

        # Cache and missing file management
        row = layout.row(align=True)
        row.operator("atomic.clear_cache", text="Clear Cache", icon="FILE_REFRESH")
        row.operator("atomic.detect_missing", text="Detect Missing", icon="LIBRARY_DATA_DIRECT")
        
reg_list = [ATOMIC_PT_main_panel]


def register():
    for cls in reg_list:
        register_class(cls)


def unregister():
    for cls in reg_list:
        compat.safe_unregister_class(cls)

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

This file contains the main operators found in the main panel of the
Atomic Data Manager interface. This includes nuke, clean, undo, and the
various selection operations.

"""

import bpy
from bpy.utils import register_class
from ..utils import compat
from ..stats import unused
from ..stats import unused_parallel
from .utils import clean
from .utils import nuke
from ..ui.utils import ui_layouts

# Cache for unused data-blocks to avoid recalculation
# This is invalidated when undo steps occur or after cleaning
_unused_cache = None
_cache_valid = False


def _invalidate_cache():
    """Invalidate the unused data cache."""
    global _unused_cache, _cache_valid
    _unused_cache = None
    _cache_valid = False


# Atomic Data Manager Clear Cache Operator
class ATOMIC_OT_clear_cache(bpy.types.Operator):
    """Clear the unused data cache"""
    bl_idname = "atomic.clear_cache"
    bl_label = "Clear Cache"
    bl_description = "Manually clear the unused data cache. This forces a fresh scan on the next Smart Select or Clean operation"

    def execute(self, context):
        _invalidate_cache()
        print("[Atomic] Cache cleared manually")
        return {'FINISHED'}


# Atomic Data Manager Nuke Operator
class ATOMIC_OT_nuke(bpy.types.Operator):
    """Remove all data-blocks from the selected categories"""
    bl_idname = "atomic.nuke"
    bl_label = "CAUTION!"

    def draw(self, context):
        atom = bpy.context.scene.atomic
        layout = self.layout

        col = layout.column()
        col.label(text="Remove the following data-blocks?")

        # No Data Section
        if not (atom.collections or atom.images or atom.lights or
                atom.materials or atom.node_groups or atom.particles or
                atom.textures or atom.worlds):

            ui_layouts.box_list(
                layout=layout,
            )

        # display when the main panel collections property is toggled
        if atom.collections:
            from ..utils import compat
            collections = sorted([c.name for c in bpy.data.collections 
                                 if not compat.is_library_or_override(c)])
            ui_layouts.box_list(
                layout=layout,
                title="Collections",
                items=collections,
                icon="OUTLINER_OB_GROUP_INSTANCE"
            )

        # display when the main panel images property is toggled
        if atom.images:
            from ..utils import compat
            images = sorted([i.name for i in bpy.data.images 
                            if not compat.is_library_or_override(i)])
            ui_layouts.box_list(
                layout=layout,
                title="Images",
                items=images,
                icon="IMAGE_DATA"
            )

        # display when the main panel lights property is toggled
        if atom.lights:
            from ..utils import compat
            lights = sorted([l.name for l in bpy.data.lights 
                           if not compat.is_library_or_override(l)])
            ui_layouts.box_list(
                layout=layout,
                title="Lights",
                items=lights,
                icon="OUTLINER_OB_LIGHT"
            )

        # display when the main panel materials property is toggled
        if atom.materials:
            from ..utils import compat
            materials = sorted([m.name for m in bpy.data.materials 
                               if not compat.is_library_or_override(m)])
            ui_layouts.box_list(
                layout=layout,
                title="Materials",
                items=materials,
                icon="MATERIAL"
            )

        # display when the main panel node groups property is toggled
        if atom.node_groups:
            from ..utils import compat
            node_groups = sorted([ng.name for ng in bpy.data.node_groups 
                                 if not compat.is_library_or_override(ng)])
            ui_layouts.box_list(
                layout=layout,
                title="Node Groups",
                items=node_groups,
                icon="NODETREE"
            )

        # display when the main panel particle systems property is toggled
        if atom.particles:
            from ..utils import compat
            particles = sorted([p.name for p in bpy.data.particles 
                               if not compat.is_library_or_override(p)])
            ui_layouts.box_list(
                layout=layout,
                title="Particle Systems",
                items=particles,
                icon="PARTICLES"
            )

        # display when the main panel textures property is toggled
        if atom.textures:
            from ..utils import compat
            textures = sorted([t.name for t in bpy.data.textures 
                              if not compat.is_library_or_override(t)])
            ui_layouts.box_list(
                layout=layout,
                title="Textures",
                items=textures,
                icon="TEXTURE"
            )

        # display when the main panel worlds property is toggled
        if atom.worlds:
            from ..utils import compat
            worlds = sorted([w.name for w in bpy.data.worlds 
                           if not compat.is_library_or_override(w)])
            ui_layouts.box_list(
                layout=layout,
                title="Worlds",
                items=worlds,
                icon="WORLD"
            )

        row = layout.row()  # extra spacing

    def execute(self, context):
        atom = bpy.context.scene.atomic

        if atom.collections:
            nuke.collections()

        if atom.images:
            nuke.images()

        if atom.lights:
            nuke.lights()

        if atom.materials:
            nuke.materials()

        if atom.node_groups:
            nuke.node_groups()

        if atom.particles:
            nuke.particles()

        if atom.textures:
            nuke.textures()

        if atom.worlds:
            nuke.worlds()

        bpy.ops.atomic.deselect_all()

        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)


# Atomic Data Manager Clean Operator
class ATOMIC_OT_clean(bpy.types.Operator):
    """Remove all unused data-blocks from the selected categories"""
    bl_idname = "atomic.clean"
    bl_label = "Clean"

    # Use None as sentinel to indicate "not yet calculated"
    # Empty lists [] indicate "calculated and found nothing"
    unused_collections = None
    unused_images = None
    unused_lights = None
    unused_materials = None
    unused_node_groups = None
    unused_objects = None
    unused_particles = None
    unused_textures = None
    unused_armatures = None
    unused_worlds = None

    def draw(self, context):
        atom = bpy.context.scene.atomic
        layout = self.layout

        col = layout.column()
        col.label(text="Remove the following data-blocks?")

        # display if no main panel properties are toggled
        if not (atom.collections or atom.images or atom.lights or
                atom.materials or atom.node_groups or atom.objects or
                atom.particles or atom.textures or atom.armatures or
                atom.worlds):

            ui_layouts.box_list(
                layout=layout,
            )

        # display when the main panel collections property is toggled
        if atom.collections:
            ui_layouts.box_list(
                layout=layout,
                title="Collections",
                items=self.unused_collections,
                icon="OUTLINER_OB_GROUP_INSTANCE"
            )

        # display when the main panel images property is toggled
        if atom.images:
            ui_layouts.box_list(
                layout=layout,
                title="Images",
                items=self.unused_images,
                icon="IMAGE_DATA"
            )

        # display when the main panel lights property is toggled
        if atom.lights:
            ui_layouts.box_list(
                layout=layout,
                title="Lights",
                items=self.unused_lights,
                icon="OUTLINER_OB_LIGHT"
            )

        # display when the main panel materials property is toggled
        if atom.materials:
            ui_layouts.box_list(
                layout=layout,
                title="Materials",
                items=self.unused_materials,
                icon="MATERIAL"
            )

        # display when the main panel node groups property is toggled
        if atom.node_groups:
            ui_layouts.box_list(
                layout=layout,
                title="Node Groups",
                items=self.unused_node_groups,
                icon="NODETREE"
            )

        # display when the main panel objects property is toggled
        if atom.objects:
            ui_layouts.box_list(
                layout=layout,
                title="Objects",
                items=self.unused_objects,
                icon="OBJECT_DATA"
            )

        # display when the main panel particle systems property is toggled
        if atom.particles:
            ui_layouts.box_list(
                layout=layout,
                title="Particle Systems",
                items=self.unused_particles,
                icon="PARTICLES"
            )

        # display when the main panel textures property is toggled
        if atom.textures:
            ui_layouts.box_list(
                layout=layout,
                title="Textures",
                items=self.unused_textures,
                icon="TEXTURE"
            )

        # display when the main panel armatures property is toggled
        if atom.armatures:
            ui_layouts.box_list(
                layout=layout,
                title="Armatures",
                items=self.unused_armatures,
                icon="ARMATURE_DATA"
            )

        # display when the main panel worlds property is toggled
        if atom.worlds:
            ui_layouts.box_list(
                layout=layout,
                title="Worlds",
                items=self.unused_worlds,
                icon="WORLD"
            )

        row = layout.row()  # extra spacing

    def execute(self, context):
        atom = bpy.context.scene.atomic

        # Use cached lists from invoke() if available, otherwise recalculate
        # This avoids expensive recalculation when the dialog showed empty results
        # Note: Empty lists [] are valid cached results - they mean "no unused items found"
        # None means "not yet calculated", which triggers recalculation
        if atom.collections:
            clean.collections(self.unused_collections if self.unused_collections is not None else None)

        if atom.images:
            clean.images(self.unused_images if self.unused_images is not None else None)

        if atom.lights:
            clean.lights(self.unused_lights if self.unused_lights is not None else None)

        if atom.materials:
            clean.materials(self.unused_materials if self.unused_materials is not None else None)

        if atom.node_groups:
            clean.node_groups(self.unused_node_groups if self.unused_node_groups is not None else None)

        if atom.objects:
            clean.objects(self.unused_objects if self.unused_objects is not None else None)

        if atom.particles:
            clean.particles(self.unused_particles if self.unused_particles is not None else None)

        if atom.textures:
            clean.textures(self.unused_textures if self.unused_textures is not None else None)

        if atom.armatures:
            clean.armatures(self.unused_armatures if self.unused_armatures is not None else None)

        if atom.worlds:
            clean.worlds(self.unused_worlds if self.unused_worlds is not None else None)

        # Invalidate cache after cleaning (data has changed)
        _invalidate_cache()

        bpy.ops.atomic.deselect_all()

        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        atom = bpy.context.scene.atomic

        # Check if cache is valid, otherwise recalculate
        global _unused_cache, _cache_valid
        if _cache_valid and _unused_cache is not None:
            all_unused = _unused_cache
        else:
            # Use parallel execution for better performance
            all_unused = unused_parallel.get_all_unused_parallel()
            _unused_cache = all_unused
            _cache_valid = True

        # Debug: Print what categories are selected and what was found
        selected_categories = []
        found_items = {}
        
        if atom.collections:
            selected_categories.append('collections')
            self.unused_collections = all_unused['collections']
            if self.unused_collections:
                found_items['collections'] = len(self.unused_collections)

        if atom.images:
            selected_categories.append('images')
            self.unused_images = all_unused['images']
            if self.unused_images:
                found_items['images'] = len(self.unused_images)

        if atom.lights:
            selected_categories.append('lights')
            self.unused_lights = all_unused['lights']
            if self.unused_lights:
                found_items['lights'] = len(self.unused_lights)

        if atom.materials:
            selected_categories.append('materials')
            self.unused_materials = all_unused['materials']
            if self.unused_materials:
                found_items['materials'] = len(self.unused_materials)

        if atom.node_groups:
            selected_categories.append('node_groups')
            self.unused_node_groups = all_unused['node_groups']
            if self.unused_node_groups:
                found_items['node_groups'] = len(self.unused_node_groups)

        if atom.objects:
            selected_categories.append('objects')
            self.unused_objects = all_unused['objects']
            if self.unused_objects:
                found_items['objects'] = len(self.unused_objects)

        if atom.particles:
            selected_categories.append('particles')
            self.unused_particles = all_unused['particles']
            if self.unused_particles:
                found_items['particles'] = len(self.unused_particles)

        if atom.textures:
            selected_categories.append('textures')
            self.unused_textures = all_unused['textures']
            if self.unused_textures:
                found_items['textures'] = len(self.unused_textures)

        if atom.armatures:
            selected_categories.append('armatures')
            self.unused_armatures = all_unused['armatures']
            if self.unused_armatures:
                found_items['armatures'] = len(self.unused_armatures)

        if atom.worlds:
            selected_categories.append('worlds')
            self.unused_worlds = all_unused['worlds']
            if self.unused_worlds:
                found_items['worlds'] = len(self.unused_worlds)

        # Debug: Only print when categories are selected but nothing found
        if selected_categories:
            if found_items:
                print(f"[Atomic Clean] Selected categories: {', '.join(selected_categories)}")
                print(f"[Atomic Clean] Found unused items: {found_items}")
            else:
                print(f"[Atomic Clean] Selected categories: {', '.join(selected_categories)}")
                print(f"[Atomic Clean] WARNING: No unused items found in selected categories!")

        return wm.invoke_props_dialog(self)


# Atomic Data Manager Undo Operator
class ATOMIC_OT_undo(bpy.types.Operator):
    """Undo the previous action"""
    bl_idname = "atomic.undo"
    bl_label = "Undo"

    def execute(self, context):
        bpy.ops.ed.undo()
        # Invalidate cache after undo
        _invalidate_cache()
        return {'FINISHED'}


# Atomic Data Manager Smart Select Operator
class ATOMIC_OT_smart_select(bpy.types.Operator):
    """Auto-select categories with unused data"""
    bl_idname = "atomic.smart_select"
    bl_label = "Smart Select"

    def execute(self, context):
        # Use parallel execution for better performance
        unused_flags = unused_parallel.get_unused_for_smart_select()
        
        # Debug: Print only when something is detected
        detected_categories = []
        for category, has_unused in unused_flags.items():
            if has_unused:
                detected_categories.append(category)
        
        if detected_categories:
            print(f"[Atomic Smart Select] Detected unused items in: {', '.join(detected_categories)}")
            # Get actual counts for debug
            all_unused = unused_parallel.get_all_unused_parallel()
            for category in detected_categories:
                count = len(all_unused.get(category, []))
                if count > 0:
                    print(f"  - {category}: {count} unused items")
        
        # Also populate the full cache for use by Clean operator
        # This allows Clean to reuse the results without recalculation
        global _unused_cache, _cache_valid
        if not _cache_valid or _unused_cache is None:
            _unused_cache = unused_parallel.get_all_unused_parallel()
            _cache_valid = True
        
        atom = bpy.context.scene.atomic
        atom.collections = unused_flags['collections']
        atom.images = unused_flags['images']
        atom.lights = unused_flags['lights']
        atom.materials = unused_flags['materials']
        atom.node_groups = unused_flags['node_groups']
        atom.objects = unused_flags['objects']
        atom.particles = unused_flags['particles']
        atom.textures = unused_flags['textures']
        atom.armatures = unused_flags['armatures']
        atom.worlds = unused_flags['worlds']

        return {'FINISHED'}


# Atomic Data Manager Select All Operator
class ATOMIC_OT_select_all(bpy.types.Operator):
    """Select all categories"""
    bl_idname = "atomic.select_all"
    bl_label = "Select All"

    def execute(self, context):
        bpy.context.scene.atomic.collections = True
        bpy.context.scene.atomic.images = True
        bpy.context.scene.atomic.lights = True
        bpy.context.scene.atomic.materials = True
        bpy.context.scene.atomic.node_groups = True
        bpy.context.scene.atomic.objects = True
        bpy.context.scene.atomic.particles = True
        bpy.context.scene.atomic.textures = True
        bpy.context.scene.atomic.armatures = True
        bpy.context.scene.atomic.worlds = True
        return {'FINISHED'}


# Atomic Data Manager Deselect All Operator
class ATOMIC_OT_deselect_all(bpy.types.Operator):
    """Deselect all categories"""
    bl_idname = "atomic.deselect_all"
    bl_label = "Deselect All"

    def execute(self, context):
        bpy.context.scene.atomic.collections = False
        bpy.context.scene.atomic.images = False
        bpy.context.scene.atomic.lights = False
        bpy.context.scene.atomic.materials = False
        bpy.context.scene.atomic.node_groups = False
        bpy.context.scene.atomic.objects = False
        bpy.context.scene.atomic.particles = False
        bpy.context.scene.atomic.textures = False
        bpy.context.scene.atomic.armatures = False
        bpy.context.scene.atomic.worlds = False

        return {'FINISHED'}


reg_list = [
    ATOMIC_OT_clear_cache,
    ATOMIC_OT_nuke,
    ATOMIC_OT_clean,
    ATOMIC_OT_undo,
    ATOMIC_OT_smart_select,
    ATOMIC_OT_select_all,
    ATOMIC_OT_deselect_all
]


def register():
    for item in reg_list:
        register_class(item)


def unregister():
    for item in reg_list:
        compat.safe_unregister_class(item)

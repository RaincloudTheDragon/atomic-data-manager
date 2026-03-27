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

This file contains functions for cleaning out specific data categories.

"""

import bpy
from ...stats import unused
from ...utils import compat


def detach_scene_objects_from_removal_targets(object_names_to_remove):
    """
    Unparent (world transform preserved) and remove Armature modifiers pointing at
    objects that are about to be deleted, for objects that are NOT in the removal
    set. Prevents in-scene props (e.g. parented to a rig) from being lost when the
    rig object is removed.

    Returns:
        list[str]: Messages suitable for operator.report (one string per change).
    """
    if not object_names_to_remove:
        return []
    remove = set(object_names_to_remove)
    reports = []

    for obj in bpy.data.objects:
        if obj.name in remove:
            continue
        if compat.is_library_or_override(obj):
            continue

        parts = []

        if obj.parent is not None and obj.parent.name in remove:
            mw = obj.matrix_world.copy()
            obj.parent = None
            obj.matrix_world = mw
            parts.append("unparented (kept world transform)")

        for mod in list(obj.modifiers):
            if mod.type != 'ARMATURE':
                continue
            target = getattr(mod, "object", None)
            if target is not None and target.name in remove:
                obj.modifiers.remove(mod)
                parts.append("removed Armature modifier targeting deleted object")

        if parts:
            reports.append(
                "Atomic: “%s”: %s" % (obj.name, "; ".join(parts))
            )

    return reports


def collections(cached_list=None):
    # removes all unused collections from the project
    # If cached_list is provided, use it instead of recalculating
    if cached_list is not None:
        collection_keys = cached_list
    else:
        collection_keys = unused.collections_deep()
    
    for collection_key in collection_keys:
        if collection_key in bpy.data.collections:
            bpy.data.collections.remove(bpy.data.collections[collection_key])


def images(cached_list=None):
    # removes all unused images from the project
    # If cached_list is provided, use it instead of recalculating
    if cached_list is not None:
        image_keys = cached_list
    else:
        image_keys = unused.images_deep()
    
    for image_key in image_keys:
        if image_key in bpy.data.images:
            bpy.data.images.remove(bpy.data.images[image_key])


def lights(cached_list=None):
    # removes all unused lights from the project
    # If cached_list is provided, use it instead of recalculating
    if cached_list is not None:
        light_keys = cached_list
    else:
        light_keys = unused.lights_deep()
    
    for light_key in light_keys:
        if light_key in bpy.data.lights:
            bpy.data.lights.remove(bpy.data.lights[light_key])


def materials(cached_list=None):
    # removes all unused materials from the project
    # If cached_list is provided, use it instead of recalculating
    if cached_list is not None:
        material_keys = cached_list
    else:
        material_keys = unused.materials_deep()
    
    for material_key in material_keys:
        if material_key in bpy.data.materials:
            bpy.data.materials.remove(bpy.data.materials[material_key])


def node_groups(cached_list=None):
    # removes all unused node groups from the project
    # If cached_list is provided, use it instead of recalculating
    if cached_list is not None:
        node_group_keys = cached_list
    else:
        node_group_keys = unused.node_groups_deep()
    
    for node_group_key in node_group_keys:
        if node_group_key in bpy.data.node_groups:
            bpy.data.node_groups.remove(bpy.data.node_groups[node_group_key])


def particles(cached_list=None):
    # removes all unused particle systems from the project
    # If cached_list is provided, use it instead of recalculating
    if cached_list is not None:
        particle_keys = cached_list
    else:
        particle_keys = unused.particles_deep()
    
    for particle_key in particle_keys:
        if particle_key in bpy.data.particles:
            bpy.data.particles.remove(bpy.data.particles[particle_key])


def textures(cached_list=None):
    # removes all unused textures from the project
    # If cached_list is provided, use it instead of recalculating
    if cached_list is not None:
        texture_keys = cached_list
    else:
        texture_keys = unused.textures_deep()
    
    for texture_key in texture_keys:
        if texture_key in bpy.data.textures:
            bpy.data.textures.remove(bpy.data.textures[texture_key])


def worlds(cached_list=None):
    # removes all unused worlds from the project
    # If cached_list is provided, use it instead of recalculating
    if cached_list is not None:
        world_keys = cached_list
    else:
        world_keys = unused.worlds()
    
    for world_key in world_keys:
        if world_key in bpy.data.worlds:
            bpy.data.worlds.remove(bpy.data.worlds[world_key])


def objects(cached_list=None):
    # removes all unused objects from the project
    # If cached_list is provided, use it instead of recalculating
    if cached_list is not None:
        object_keys = cached_list
    else:
        object_keys = unused.objects_deep()

    for object_key in object_keys:
        if object_key in bpy.data.objects:
            bpy.data.objects.remove(bpy.data.objects[object_key])


def armatures(cached_list=None):
    # removes all unused armatures from the project
    # If cached_list is provided, use it instead of recalculating
    if cached_list is not None:
        armature_keys = cached_list
    else:
        armature_keys = unused.armatures_deep()
    
    for armature_key in armature_keys:
        if armature_key in bpy.data.armatures:
            bpy.data.armatures.remove(bpy.data.armatures[armature_key])

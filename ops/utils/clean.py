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
from . import delete as delete_utils
from . import safe_delete


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


def _clean_keys(data, keys):
    """Remove each key from data, skipping linked/override IDs."""
    with safe_delete.safe_datablock_removal():
        for key in keys:
            delete_utils.remove_if_local(data, key)


def collections(cached_list=None):
    # removes all unused collections from the project
    if cached_list is not None:
        collection_keys = cached_list
    else:
        collection_keys = unused.collections_deep()
    _clean_keys(bpy.data.collections, collection_keys)


def images(cached_list=None):
    # removes all unused images from the project
    if cached_list is not None:
        image_keys = cached_list
    else:
        image_keys = unused.images_deep()
    _clean_keys(bpy.data.images, image_keys)


def lights(cached_list=None):
    # removes all unused lights from the project
    if cached_list is not None:
        light_keys = cached_list
    else:
        light_keys = unused.lights_deep()
    _clean_keys(bpy.data.lights, light_keys)


def materials(cached_list=None):
    # removes all unused materials from the project
    if cached_list is not None:
        material_keys = cached_list
    else:
        material_keys = unused.materials_deep()
    _clean_keys(bpy.data.materials, material_keys)


def node_groups(cached_list=None):
    # removes all unused node groups from the project
    if cached_list is not None:
        node_group_keys = cached_list
    else:
        node_group_keys = unused.node_groups_deep()
    _clean_keys(bpy.data.node_groups, node_group_keys)


def particles(cached_list=None):
    # removes all unused particle systems from the project
    if cached_list is not None:
        particle_keys = cached_list
    else:
        particle_keys = unused.particles_deep()
    _clean_keys(bpy.data.particles, particle_keys)


def textures(cached_list=None):
    # removes all unused textures from the project
    if cached_list is not None:
        texture_keys = cached_list
    else:
        texture_keys = unused.textures_deep()
    _clean_keys(bpy.data.textures, texture_keys)


def worlds(cached_list=None):
    # removes all unused worlds from the project
    if cached_list is not None:
        world_keys = cached_list
    else:
        world_keys = unused.worlds()
    _clean_keys(bpy.data.worlds, world_keys)


def objects(cached_list=None):
    # removes all unused objects from the project
    if cached_list is not None:
        object_keys = cached_list
    else:
        object_keys = unused.objects_deep()
    _clean_keys(bpy.data.objects, object_keys)


def armatures(cached_list=None):
    # removes all unused armatures from the project
    if cached_list is not None:
        armature_keys = cached_list
    else:
        armature_keys = unused.armatures_deep()
    _clean_keys(bpy.data.armatures, armature_keys)


def actions(cached_list=None):
    # removes all unused actions from the project
    if cached_list is not None:
        action_keys = cached_list
    else:
        action_keys = unused.actions_deep()
    if hasattr(bpy.data, "actions"):
        _clean_keys(bpy.data.actions, action_keys)

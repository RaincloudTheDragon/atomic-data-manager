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

This file contains functions for deleting individual data-blocks from
Atomic's inspection inteface.

"""

import bpy
from ...utils import compat


# Category name -> bpy.data collection (Clean / Nuke / stepped purge).
CATEGORY_DATA = {
    'collections': lambda: bpy.data.collections,
    'images': lambda: bpy.data.images,
    'lights': lambda: bpy.data.lights,
    'materials': lambda: bpy.data.materials,
    'node_groups': lambda: bpy.data.node_groups,
    'objects': lambda: bpy.data.objects,
    'particles': lambda: bpy.data.particles,
    'textures': lambda: bpy.data.textures,
    'armatures': lambda: bpy.data.armatures,
    'actions': lambda: getattr(bpy.data, 'actions', None),
    'worlds': lambda: bpy.data.worlds,
}


def remove_if_local(data, key):
    """Remove a datablock by name only if it is safe to clean.

    Resolves among same-named linked/local IDs by pointer (orphaned local
    namesakes first). Uses is_protected_from_clean plus a live scene-
    reachability check so instance-collection / override trees are never
    deleted mid-chain.

    Returns:
        True if removed, False if missing or skipped.
    """
    if data is None:
        return False
    datablock = compat.resolve_cleanable_datablock(data, key)
    if datablock is None:
        return False

    # Live scene anchor — never delete something still reachable from a scene
    try:
        from ...stats import users as users_stats
        if isinstance(datablock, bpy.types.Object):
            # Pointer-accurate only: name-based object_all is wrong with namesakes
            if not compat.is_scene_orphaned_local_object(datablock):
                if users_stats.object_all(datablock.name):
                    return False
                for scene in bpy.data.scenes:
                    try:
                        for ob in scene.objects:
                            if ob.as_pointer() == datablock.as_pointer():
                                return False
                    except (AttributeError, RuntimeError, ReferenceError):
                        continue
        elif isinstance(datablock, bpy.types.Collection):
            if users_stats.collection_all(datablock.name):
                return False
        elif isinstance(datablock, bpy.types.Material):
            try:
                if users_stats.material_has_scene_reachable_user(
                    datablock.name, material=datablock
                ):
                    return False
            except Exception:
                pass
    except Exception:
        pass

    # Capture local mesh/armature data before object removal (may become orphaned)
    ob_data = None
    try:
        if isinstance(datablock, bpy.types.Object):
            ob_data = datablock.data
            if ob_data is not None and compat.is_library_or_override(ob_data):
                ob_data = None
    except (AttributeError, RuntimeError, ReferenceError):
        ob_data = None

    try:
        data.remove(datablock)
    except (ReferenceError, RuntimeError):
        return False

    # Drop leftover local obdata if nothing else uses it
    if ob_data is not None:
        try:
            if ob_data.users == 0 and not compat.is_library_or_override(ob_data):
                if isinstance(ob_data, bpy.types.Mesh):
                    bpy.data.meshes.remove(ob_data)
                elif isinstance(ob_data, bpy.types.Armature):
                    bpy.data.armatures.remove(ob_data)
        except (AttributeError, ReferenceError, RuntimeError, KeyError):
            pass
    return True


def remove_category_item(category, key):
    """Remove one unused item by Atomic category name; skips protected IDs."""
    getter = CATEGORY_DATA.get(category)
    if not getter:
        return False
    return remove_if_local(getter(), key)


def filter_unused_names(category, names):
    """Drop names that resolve to protected (linked/override/namesake) IDs."""
    if not names:
        return []
    getter = CATEGORY_DATA.get(category)
    if not getter:
        return list(names)
    data = getter()
    if data is None:
        return list(names)
    kept = []
    for key in names:
        try:
            if compat.resolve_cleanable_datablock(data, key) is None:
                continue
        except (AttributeError, RuntimeError, ReferenceError):
            continue
        kept.append(key)
    return kept


def sanitize_unused_results(all_unused):
    """Filter every category list in a scan-results dict."""
    if not isinstance(all_unused, dict):
        return all_unused
    cleaned = {}
    for category, value in all_unused.items():
        if isinstance(value, list) and category in CATEGORY_DATA:
            cleaned[category] = filter_unused_names(category, value)
        else:
            cleaned[category] = value
    return cleaned


def delete_datablock(data, key):
    # deletes a specific data-block from a set of data (local only)
    remove_if_local(data, key)


def collection(key):
    # removes a specific collection
    delete_datablock(bpy.data.collections, key)


def image(key):
    # removes a specific image
    delete_datablock(bpy.data.images, key)


def light(key):
    # removes a specific light
    delete_datablock(bpy.data.lights, key)


def material(key):
    # removes a specific material
    delete_datablock(bpy.data.materials, key)


def node_group(key):
    # removes a specific node group
    delete_datablock(bpy.data.node_groups, key)


def particle(key):
    # removes a specific particle system
    delete_datablock(bpy.data.particles, key)


def texture(key):
    # removes a specific texture
    delete_datablock(bpy.data.textures, key)


def world(key):
    # removes a specific world
    delete_datablock(bpy.data.worlds, key)


def action(key):
    # removes a specific action
    delete_datablock(bpy.data.actions, key)

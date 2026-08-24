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

This file contains functions that detect data-blocks that have no users,
as determined by stats.users.py

"""

import bpy
from .. import config
from ..utils import compat
from ..utils import version
from . import users
from . import ghost_users


def shallow(data):
    # returns a list of keys of unused data-blocks in the data that may be
    # incomplete, but is significantly faster than doing a deep search

    unused = []

    for datablock in data:
        # Skip library-linked and override datablocks
        if compat.is_library_or_override(datablock):
            continue

        # if data-block has no users or if it has a fake user and
        # ignore fake users is enabled
        if datablock.users == 0 or (datablock.users == 1 and
                                    datablock.use_fake_user and
                                    config.include_fake_users):
            unused.append(datablock.name)

    return unused


def collections_deep():
    # returns a full list of keys of unused collections

    unused = []

    for collection in bpy.data.collections:
        # Skip library-linked and override datablocks
        if compat.is_library_or_override(collection):
            continue
        if not users.collection_all(collection.name):
            unused.append(collection.name)

    return unused


def collections_shallow():
    # returns a list of keys of unused collections that may be
    # incomplete, but is significantly faster.

    unused = []

    for collection in bpy.data.collections:
        # Skip library-linked and override datablocks
        if compat.is_library_or_override(collection):
            continue
        if not (collection.objects or collection.children):
            unused.append(collection.name)

    return unused


def images_deep():
    # returns a full list of keys of unused images

    unused = []

    # a list of image keys that should not be flagged as unused
    # this list also exists in images_shallow()
    do_not_flag = ["Render Result", "Viewer Node", "D-NOISE Export"]

    total_images = len(bpy.data.images)
    config.debug_print(f"[Atomic Debug] images_deep(): Starting, total images: {total_images}")
    checked = 0

    for image in bpy.data.images:
        # Skip library-linked and override datablocks
        if compat.is_library_or_override(image):
            continue
        
        checked += 1
        config.debug_print(f"[Atomic Debug] images_deep(): Checking image {checked}/{total_images}: '{image.name}'")
        
        # First check: standard unused detection
        config.debug_print(f"[Atomic Debug] images_deep(): Calling users.image_all('{image.name}')...")
        if not users.image_all(image.name):
            config.debug_print(f"[Atomic Debug] images_deep(): Image '{image.name}' is unused (first check)")
            # check if image has a fake user or if ignore fake users
            # is enabled
            if not image.use_fake_user or config.include_fake_users:
                # if image is not in our do not flag list
                if image.name not in do_not_flag:
                    unused.append(image.name)
        else:
            # Second check: image is used, but check if it's ONLY used by unused objects
            # This fixes issue #5: images used by unused objects should be marked as unused
            # Get all objects that use this image (directly or indirectly)
            objects_using_image = []
            
            # Check materials that use the image
            config.debug_print(f"[Atomic Debug] images_deep(): Getting materials for '{image.name}'...")
            mat_names = users.image_materials(image.name)
            config.debug_print(f"[Atomic Debug] images_deep(): Found {len(mat_names)} materials using '{image.name}'")
            for mat_name in mat_names:
                # Get objects using this material
                config.debug_print(f"[Atomic Debug] images_deep(): Getting objects for material '{mat_name}'...")
                objects_using_image.extend(users.material_objects(mat_name))
                # Also check Geometry Nodes usage
                config.debug_print(f"[Atomic Debug] images_deep(): Getting Geometry Nodes objects for material '{mat_name}'...")
                objects_using_image.extend(users.material_geometry_nodes(mat_name))
            
            # Check Geometry Nodes directly
            config.debug_print(f"[Atomic Debug] images_deep(): Getting Geometry Nodes objects for '{image.name}'...")
            objects_using_image.extend(users.image_geometry_nodes(image.name))
            
            # Remove duplicates
            objects_using_image = list(set(objects_using_image))
            config.debug_print(f"[Atomic Debug] images_deep(): Found {len(objects_using_image)} objects using '{image.name}'")
            
            # If image is only used by objects, and ALL those objects are unused, mark image as unused
            # Check each object individually to avoid recursion issues
            if objects_using_image:
                config.debug_print(f"[Atomic Debug] images_deep(): Checking if all {len(objects_using_image)} objects are unused...")
                all_objects_unused = all(not users.object_all(obj_name) for obj_name in objects_using_image)
                if all_objects_unused:
                    config.debug_print(f"[Atomic Debug] images_deep(): All objects are unused, marking '{image.name}' as unused")
                    # Check if image has a fake user or if ignore fake users is enabled
                    if not image.use_fake_user or config.include_fake_users:
                        # if image is not in our do not flag list
                        if image.name not in do_not_flag:
                            unused.append(image.name)
                            config.debug_print(f"[Atomic Debug] images_deep(): Added '{image.name}' to unused list")
                else:
                    config.debug_print(f"[Atomic Debug] images_deep(): Some objects are used, '{image.name}' is not unused")
        config.debug_print(f"[Atomic Debug] images_deep(): Finished checking '{image.name}'")

    config.debug_print(f"[Atomic Debug] images_deep(): Complete, checked {checked} images, found {len(unused)} unused")
    return unused


def images_shallow():
    # returns a list of keys of unused images that may be
    # incomplete, but is significantly faster than doing a deep search

    unused_images = shallow(bpy.data.images)

    # a list of image keys that should not be flagged as unused
    # this list also exists in images_deep()
    do_not_flag = ["Render Result", "Viewer Node", "D-NOISE Export"]

    # remove do not flag keys from unused images
    for key in do_not_flag:
        if key in unused_images:
            unused_images.remove(key)

    return unused_images


def lights_deep():
    # returns a list of keys of unused lights

    unused = []

    for light in bpy.data.lights:
        # Skip library-linked and override datablocks
        if compat.is_library_or_override(light):
            continue
        if not users.light_all(light.name):

            # check if light has a fake user or if ignore fake users
            # is enabled
            if not light.use_fake_user or config.include_fake_users:
                unused.append(light.name)

    return unused


def lights_shallow():
    # returns a list of keys of unused lights that may be
    # incomplete, but is significantly faster than doing a deep search

    return shallow(bpy.data.lights)


def materials_deep():
    # returns a list of keys of unused materials

    unused = []

    for material in bpy.data.materials:
        # Skip library-linked and override datablocks
        if compat.is_library_or_override(material):
            continue
        
        # Check if material is used by brushes - these should always be ignored
        if users.material_brushes(material.name):
            continue
        
        # First check: standard unused detection
        if not users.material_all(material.name):
            # check if material has a fake user or if ignore fake users
            # is enabled
            if not material.use_fake_user or config.include_fake_users:
                if material.users > 0 and not material.use_fake_user:
                    # CC3 / iClone import_cache ID props keep bpy.users>0 with no object/world use.
                    if not ghost_users.material_blender_users_fully_cc3_ghosts(material):
                        continue
                unused.append(material.name)
        else:
            # Second check: material is used, but check if it's ONLY used by unused objects
            # This fixes issue #5: materials used by unused objects should be marked as unused
            # Get all objects that use this material
            objects_using_material = []
            objects_using_material.extend(users.material_objects(material.name))
            objects_using_material.extend(users.material_geometry_nodes(material.name))
            
            # Remove duplicates
            objects_using_material = list(set(objects_using_material))
            
            # If material is only used by objects, and ALL those objects are unused, mark material as unused
            # Check each object individually to avoid recursion issues
            if objects_using_material:
                all_objects_unused = all(not users.object_all(obj_name) for obj_name in objects_using_material)
                if all_objects_unused:
                    # Check if material has a fake user or if ignore fake users is enabled
                    if not material.use_fake_user or config.include_fake_users:
                        unused.append(material.name)

    return unused


def materials_shallow():
    # returns a list of keys of unused material that may be
    # incomplete, but is significantly faster than doing a deep search

    unused_materials = shallow(bpy.data.materials)

    # Filter out materials used by brushes - these should always be ignored
    filtered = []
    for key in unused_materials:
        material = bpy.data.materials.get(key)
        if material and not users.material_brushes(key):
            filtered.append(key)

    return filtered


def _is_compositor_node_tree(node_group):
    """
    Check if a node group is a compositor node tree.
    In Blender 5.0+, each scene has a compositing_node_tree that should be ignored.
    """
    if _compositor_session is not None:
        if id(node_group) in _compositor_session['compositor_tree_ids']:
            return True
    else:
        for scene in bpy.data.scenes:
            if not scene.use_nodes:
                continue
            try:
                node_tree = compat.get_scene_compositor_node_tree(scene)
                if node_tree and node_tree == node_group:
                    if config.enable_debug_prints:
                        config.debug_print(
                            f"[Atomic Debug] _is_compositor_node_tree: "
                            f"'{node_group.name}' is scene '{scene.name}' compositor"
                        )
                    return True
            except (AttributeError, RuntimeError, ReferenceError):
                continue

    if _cached_node_group_compositors(node_group.name):
        if config.enable_debug_prints:
            config.debug_print(
                f"[Atomic Debug] _is_compositor_node_tree: "
                f"'{node_group.name}' is used in compositor"
            )
        return True

    return False


# Shared by node_groups_deep() and RNA Smart Select fallback.
_node_group_unused_cache = {}
_compositor_session = None


def begin_node_group_scan_session():
    """Build per-scan caches (compositor trees, user lookups) before node_groups pass."""
    global _compositor_session
    compositor_tree_ids = set()
    compositor_embedded_names = set()
    for scene in bpy.data.scenes:
        if not scene.use_nodes:
            continue
        try:
            node_tree = compat.get_scene_compositor_node_tree(scene)
            if node_tree:
                compositor_tree_ids.add(id(node_tree))
                compositor_embedded_names.add(node_tree.name)
                for node in node_tree.nodes:
                    nested = getattr(node, 'node_tree', None)
                    if nested is not None:
                        compositor_embedded_names.add(nested.name)
        except (AttributeError, RuntimeError, ReferenceError):
            continue
    _compositor_session = {
        'compositor_tree_ids': compositor_tree_ids,
        'compositor_embedded_names': compositor_embedded_names,
        'object_all': {},
        'node_group_all': {},
        'node_group_materials': {},
        'node_group_objects': {},
        'node_group_node_groups': {},
        'node_group_compositors': {},
    }


def _session_node_group_compositors(node_group_key):
    """O(1) compositor-user check using trees scanned once at session start."""
    session = _compositor_session
    if node_group_key in session['compositor_embedded_names']:
        return ['Compositor']
    node_group = bpy.data.node_groups.get(node_group_key)
    if node_group and id(node_group) in session['compositor_tree_ids']:
        return ['Compositor']
    for parent_name in _cached_node_group_node_groups(node_group_key):
        if parent_name in session['compositor_embedded_names']:
            return ['Compositor']
    return []


def _cached_object_all(object_key):
    if _compositor_session is None:
        return bool(users.object_all(object_key))
    cache = _compositor_session['object_all']
    if object_key not in cache:
        cache[object_key] = bool(users.object_all(object_key))
    return cache[object_key]


def _cached_node_group_all(node_group_key):
    if _compositor_session is None:
        return users.node_group_all(node_group_key)
    cache = _compositor_session['node_group_all']
    if node_group_key not in cache:
        cache[node_group_key] = users.distinct(
            _cached_node_group_compositors(node_group_key)
            + _cached_node_group_materials(node_group_key)
            + _cached_node_group_node_groups(node_group_key)
            + users.node_group_textures(node_group_key)
            + users.node_group_worlds(node_group_key)
            + _cached_node_group_objects(node_group_key)
        )
    return cache[node_group_key]


def _cached_node_group_materials(node_group_key):
    if _compositor_session is None:
        return users.node_group_materials(node_group_key)
    cache = _compositor_session['node_group_materials']
    if node_group_key not in cache:
        cache[node_group_key] = users.node_group_materials(node_group_key)
    return cache[node_group_key]


def _cached_node_group_objects(node_group_key):
    if _compositor_session is None:
        return users.node_group_objects(node_group_key)
    cache = _compositor_session['node_group_objects']
    if node_group_key not in cache:
        cache[node_group_key] = users.node_group_objects(node_group_key)
    return cache[node_group_key]


def _cached_node_group_node_groups(node_group_key):
    if _compositor_session is None:
        return users.node_group_node_groups(node_group_key)
    cache = _compositor_session['node_group_node_groups']
    if node_group_key not in cache:
        cache[node_group_key] = users.node_group_node_groups(node_group_key)
    return cache[node_group_key]


def _cached_node_group_compositors(node_group_key):
    if _compositor_session is None:
        return users.node_group_compositors(node_group_key)
    cache = _compositor_session['node_group_compositors']
    if node_group_key not in cache:
        cache[node_group_key] = _session_node_group_compositors(node_group_key)
    return cache[node_group_key]


def clear_node_group_rna_cache():
    """Reset node-group cleanability memo (call before/after each node_groups RNA pass)."""
    global _node_group_unused_cache, _compositor_session
    _node_group_unused_cache = {}
    _compositor_session = None


def is_node_group_cleanable(ng_name, visited=None):
    """
    True when node_groups_deep() would treat this group as unused/cleanable
    (only orphan/out-of-scene users, or no users).
    """
    global _node_group_unused_cache

    if visited is None:
        visited = set()

    if ng_name in visited:
        return False
    visited.add(ng_name)

    if ng_name in _node_group_unused_cache:
        return _node_group_unused_cache[ng_name]

    node_group = bpy.data.node_groups.get(ng_name)
    if not node_group:
        _node_group_unused_cache[ng_name] = False
        return False

    if compat.is_library_or_override(node_group):
        _node_group_unused_cache[ng_name] = False
        return False
    if _is_compositor_node_tree(node_group):
        _node_group_unused_cache[ng_name] = False
        return False

    all_users = _cached_node_group_all(ng_name)
    if config.enable_debug_prints:
        config.debug_print(
            f"[Atomic Debug] is_node_group_cleanable: '{ng_name}' - all_users = {all_users}"
        )
    if not all_users:
        cleanable = not node_group.use_fake_user or config.include_fake_users
        _node_group_unused_cache[ng_name] = cleanable
        return cleanable

    materials_using_ng = _cached_node_group_materials(ng_name)
    objects_using_ng = _cached_node_group_objects(ng_name)
    parent_node_groups = _cached_node_group_node_groups(ng_name)

    all_objects_using_ng = list(objects_using_ng)
    for mat_name in materials_using_ng:
        objects_using_mat = users.material_objects(mat_name)
        objects_using_mat.extend(users.material_geometry_nodes(mat_name))
        all_objects_using_ng.extend(objects_using_mat)

    all_objects_using_ng = list(set(all_objects_using_ng))

    all_objects_unused = True
    if all_objects_using_ng:
        all_objects_unused = all(
            not _cached_object_all(obj_name) for obj_name in all_objects_using_ng
        )

    all_parent_ngs_unused = True
    if parent_node_groups:
        for parent_ng_name in parent_node_groups:
            if not is_node_group_cleanable(parent_ng_name, visited.copy()):
                all_parent_ngs_unused = False
                break

    cleanable = False
    if all_objects_unused and all_parent_ngs_unused:
        cleanable = not node_group.use_fake_user or config.include_fake_users

    _node_group_unused_cache[ng_name] = cleanable
    return cleanable


def node_groups_deep():
    # returns a list of keys of unused node_groups

    unused = []
    clear_node_group_rna_cache()
    begin_node_group_scan_session()

    for node_group in bpy.data.node_groups:
        # Skip library-linked and override datablocks
        if compat.is_library_or_override(node_group):
            continue
        # Skip compositor node trees (Blender 5.0+ creates one per file)
        if _is_compositor_node_tree(node_group):
            continue

        if is_node_group_cleanable(node_group.name):
            unused.append(node_group.name)

    return unused


def node_groups_shallow():
    # returns a list of keys of unused node groups that may be
    # incomplete, but is significantly faster than doing a deep search

    unused = shallow(bpy.data.node_groups)
    
    # Filter out compositor node trees (Blender 5.0+ creates one per file)
    filtered = []
    for node_group_name in unused:
        node_group = bpy.data.node_groups.get(node_group_name)
        if node_group and not _is_compositor_node_tree(node_group):
            filtered.append(node_group_name)
    
    return filtered


def particles_deep():
    # returns a list of keys of unused particle systems

    if not hasattr(bpy.data, 'particles'):
        return []

    unused = []

    for particle in bpy.data.particles:
        # Skip library-linked and override datablocks
        if compat.is_library_or_override(particle):
            continue
        if not users.particle_all(particle.name):

            # check if particle system has a fake user or if ignore fake
            # users is enabled
            if not particle.use_fake_user or config.include_fake_users:
                unused.append(particle.name)

    return unused


def particles_shallow():
    # returns a list of keys of unused particle systems that may be
    # incomplete, but is significantly faster than doing a deep search

    return shallow(bpy.data.particles) if hasattr(bpy.data, 'particles') else []


def textures_deep():
    # returns a list of keys of unused textures

    if not hasattr(bpy.data, 'textures'):
        return []

    unused = []

    for texture in bpy.data.textures:
        # Skip library-linked and override datablocks
        if compat.is_library_or_override(texture):
            continue
        if not users.texture_all(texture.name):

            # check if texture has a fake user or if ignore fake users
            # is enabled
            if not texture.use_fake_user or config.include_fake_users:
                unused.append(texture.name)

    return unused


def textures_shallow():
    # returns a list of keys of unused textures that may be
    # incomplete, but is significantly faster than doing a deep search

    return shallow(bpy.data.textures) if hasattr(bpy.data, 'textures') else []


def worlds():
    # returns a full list of keys of unused worlds
    config.debug_print(f"[Atomic Debug] unused.worlds(): Starting, total worlds: {len(bpy.data.worlds)}")
    unused = []
    checked = 0

    for world in bpy.data.worlds:
        # Skip library-linked and override datablocks
        if compat.is_library_or_override(world):
            continue
        
        checked += 1
        config.debug_print(f"[Atomic Debug] unused.worlds(): Checking '{world.name}' (users={world.users}, fake_user={world.use_fake_user})")

        # if data-block has no users or if it has a fake user and
        # ignore fake users is enabled
        if world.users == 0 or (world.users == 1 and
                                world.use_fake_user and
                                config.include_fake_users):
            config.debug_print(f"[Atomic Debug] unused.worlds(): '{world.name}' is unused, adding to list")
            unused.append(world.name)

    config.debug_print(f"[Atomic Debug] unused.worlds(): Complete, checked {checked} worlds, found {len(unused)} unused")
    return unused


def objects_deep():
    # returns a list of keys of unused objects

    unused = []

    for obj in bpy.data.objects:
        # Skip library-linked and override datablocks
        if compat.is_library_or_override(obj):
            continue
        if not users.object_all(obj.name):

            # check if object has a fake user or if ignore fake users
            # is enabled
            if not obj.use_fake_user or config.include_fake_users:
                unused.append(obj.name)

    return unused


def objects_shallow():
    # returns a list of keys of unused objects that may be
    # incomplete, but is significantly faster than doing a deep search

    return shallow(bpy.data.objects)


def armatures_deep():
    # returns a list of keys of unused armatures

    unused = []

    for armature in bpy.data.armatures:
        # Skip library-linked and override datablocks
        if compat.is_library_or_override(armature):
            continue
        if not users.armature_all(armature.name):

            # check if armature has a fake user or if ignore fake users
            # is enabled
            if not armature.use_fake_user or config.include_fake_users:
                unused.append(armature.name)

    return unused


def armatures_shallow():
    # returns a list of keys of unused armatures that may be
    # incomplete, but is significantly faster than doing a deep search

    return shallow(bpy.data.armatures)


def actions_deep():
    # returns a list of keys of unused actions

    unused_list = []

    if not hasattr(bpy.data, "actions"):
        return unused_list

    for action in bpy.data.actions:
        if compat.is_library_or_override(action):
            continue
        if not users.action_all(action.name):
            if not action.use_fake_user or config.include_fake_users:
                unused_list.append(action.name)

    return unused_list


def actions_shallow():
    # returns a list of keys of unused actions that may be
    # incomplete, but is significantly faster than doing a deep search

    if not hasattr(bpy.data, "actions"):
        return []
    return shallow(bpy.data.actions)

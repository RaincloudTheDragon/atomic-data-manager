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
from . import users


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

    for image in bpy.data.images:
        # Skip library-linked and override datablocks
        if compat.is_library_or_override(image):
            continue
        if not users.image_all(image.name):

            # check if image has a fake user or if ignore fake users
            # is enabled
            if not image.use_fake_user or config.include_fake_users:

                # if image is not in our do not flag list
                if image.name not in do_not_flag:
                    unused.append(image.name)

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
        if not users.material_all(material.name):

            # check if material has a fake user or if ignore fake users
            # is enabled
            if not material.use_fake_user or config.include_fake_users:
                unused.append(material.name)

    return unused


def materials_shallow():
    # returns a list of keys of unused material that may be
    # incomplete, but is significantly faster than doing a deep search

    return shallow(bpy.data.materials)


def node_groups_deep():
    # returns a list of keys of unused node_groups

    unused = []

    for node_group in bpy.data.node_groups:
        # Skip library-linked and override datablocks
        if compat.is_library_or_override(node_group):
            continue
        if not users.node_group_all(node_group.name):

            # check if node group has a fake user or if ignore fake users
            # is enabled
            if not node_group.use_fake_user or config.include_fake_users:
                unused.append(node_group.name)

    return unused


def node_groups_shallow():
    # returns a list of keys of unused node groups that may be
    # incomplete, but is significantly faster than doing a deep search

    return shallow(bpy.data.node_groups)


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

    unused = []

    for world in bpy.data.worlds:
        # Skip library-linked and override datablocks
        if compat.is_library_or_override(world):
            continue

        # if data-block has no users or if it has a fake user and
        # ignore fake users is enabled
        if world.users == 0 or (world.users == 1 and
                                world.use_fake_user and
                                config.include_fake_users):
            unused.append(world.name)

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

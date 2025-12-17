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

This file contains functions that count quantities of various sets of data.

"""

import bpy
from ..utils import compat
from . import unused
from . import unnamed
from . import missing


def collections():
    # returns the number of collections in the project
    count = 0
    for collection in bpy.data.collections:
        if not compat.is_library_or_override(collection):
            count += 1
    return count


def collections_unused():
    # returns the number of unused collections in the project

    return len(unused.collections_shallow())


def collections_unnamed():
    # returns the number of unnamed collections in the project

    return len(unnamed.collections())


def images():
    # returns the number of images in the project
    count = 0
    for image in bpy.data.images:
        if not compat.is_library_or_override(image):
            count += 1
    return count


def images_unused():
    # returns the number of unused images in the project

    return len(unused.images_shallow())


def images_unnamed():
    # returns the number of unnamed images in the project

    return len(unnamed.images())


def images_missing():
    # returns the number of missing images in the project

    return len(missing.images())


def lights():
    # returns the number of lights in the project
    count = 0
    for light in bpy.data.lights:
        if not compat.is_library_or_override(light):
            count += 1
    return count


def lights_unused():
    # returns the number of unused lights in the project

    return len(unused.lights_shallow())


def lights_unnamed():
    # returns the number of unnamed lights in the project

    return len(unnamed.lights())


def materials():
    # returns the number of materials in the project
    count = 0
    for material in bpy.data.materials:
        if not compat.is_library_or_override(material):
            count += 1
    return count


def materials_unused():
    # returns the number of unused materials in the project

    return len(unused.materials_shallow())


def materials_unnamed():
    # returns the number of unnamed materials in the project

    return len(unnamed.materials())


def node_groups():
    # returns the number of node groups in the project
    count = 0
    for node_group in bpy.data.node_groups:
        if not compat.is_library_or_override(node_group):
            count += 1
    return count


def node_groups_unused():
    # returns the number of unused node groups in the project

    return len(unused.node_groups_shallow())


def node_groups_unnamed():
    # returns the number of unnamed node groups in the project

    return len(unnamed.node_groups())


def objects():
    # returns the number of objects in the project
    count = 0
    for obj in bpy.data.objects:
        if not compat.is_library_or_override(obj):
            count += 1
    return count


def objects_unnamed():
    # returns the number of unnamed objects in the project

    return len(unnamed.objects())


def particles():
    # returns the number of particles in the project
    count = 0
    for particle in bpy.data.particles:
        if not compat.is_library_or_override(particle):
            count += 1
    return count


def particles_unused():
    # returns the number of unused particles in the project

    return len(unused.particles_shallow())


def particles_unnamed():
    # returns the number of unnamed particle systems in the project

    return len(unnamed.particles())


def textures():
    # returns the number of textures in the project
    count = 0
    for texture in bpy.data.textures:
        if not compat.is_library_or_override(texture):
            count += 1
    return count


def textures_unused():
    # returns the number of unused textures in the project

    return len(unused.textures_shallow())


def textures_unnamed():
    # returns the number of unnamed textures in the project

    return len(unnamed.textures())


def worlds():
    # returns the number of worlds in the project
    count = 0
    for world in bpy.data.worlds:
        if not compat.is_library_or_override(world):
            count += 1
    return count


def worlds_unused():
    # returns the number of unused worlds in the project

    return len(unused.worlds())


def worlds_unnamed():
    # returns the number of unnamed worlds in the project

    return len(unnamed.worlds())

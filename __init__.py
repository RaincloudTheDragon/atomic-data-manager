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

This file contains Atomic's global properties and handles the
registration for all packages within the add-on.

"""

import bpy
from bpy.utils import register_class
from bpy.utils import unregister_class
from . import ops
from . import ui
from .ui import inspect_ui
from . import rainys_repo_bootstrap


# Atomic Data Manager Properties
class ATOMIC_PG_main(bpy.types.PropertyGroup):
    # main panel toggle buttons
    collections: bpy.props.BoolProperty(default=False)
    images: bpy.props.BoolProperty(default=False)
    lights: bpy.props.BoolProperty(default=False)
    materials: bpy.props.BoolProperty(default=False)
    node_groups: bpy.props.BoolProperty(default=False)
    particles: bpy.props.BoolProperty(default=False)
    textures: bpy.props.BoolProperty(default=False)
    worlds: bpy.props.BoolProperty(default=False)

    # inspect data-block search fields
    collections_field: bpy.props.StringProperty(
        update=inspect_ui.update_inspection)

    images_field: bpy.props.StringProperty(
        update=inspect_ui.update_inspection)

    lights_field: bpy.props.StringProperty(
        update=inspect_ui.update_inspection)

    materials_field: bpy.props.StringProperty(
        update=inspect_ui.update_inspection)

    node_groups_field: bpy.props.StringProperty(
        update=inspect_ui.update_inspection)

    particles_field: bpy.props.StringProperty(
        update=inspect_ui.update_inspection)

    textures_field: bpy.props.StringProperty(
        update=inspect_ui.update_inspection)

    worlds_field: bpy.props.StringProperty(
        update=inspect_ui.update_inspection)

    # enum for the inspection mode that is currently active
    active_inspection: bpy.props.EnumProperty(
        items=[
            (
                'COLLECTIONS',
                'Collections',
                'Collections'
            ),
            (
                'IMAGES',
                'Images',
                'Images'
            ),
            (
                'LIGHTS',
                'Lights',
                'Lights'
            ),
            (
                'MATERIALS',
                'Materials',
                'Materials'
            ),
            (
                'NODE_GROUPS',
                'Node Groups',
                'Node Groups'
            ),
            (
                'PARTICLES',
                'Particles',
                'Particles'
            ),
            (
                'TEXTURES',
                'Textures',
                'Textures'
            ),
            (
                'WORLDS',
                'Worlds',
                'Worlds'
            )
        ],
        default='COLLECTIONS'
    )

    # enum for the type of data being shown in the stats panel
    stats_mode: bpy.props.EnumProperty(
        items=[
            (
                'OVERVIEW',  # identifier
                'Overview',  # title
                'Overview',  # description
                'FILE',      # icon
                0            # number / id
            ),
            (
                'COLLECTIONS',
                'Collections',
                'Collections',
                'GROUP',
                1
            ),
            (
                'IMAGES',
                'Images',
                'Images',
                'IMAGE_DATA',
                2
            ),
            (
                'LIGHTS',
                'Lights',
                'Lights',
                'LIGHT',
                3
            ),
            (
                'MATERIALS',
                'Materials',
                'Materials',
                'MATERIAL',
                4
            ),
            (
                'OBJECTS',
                'Objects',
                'Objects',
                'OBJECT_DATA',
                5
            ),
            (
                'NODE_GROUPS',
                'Node Groups',
                'Node Groups',
                'NODETREE',
                6
            ),
            (
                'PARTICLES',
                'Particle Systems',
                'Particle Systems',
                'PARTICLES',
                7
            ),
            (
                'TEXTURES',
                'Textures',
                'Textures',
                'TEXTURE',
                8
            ),
            (
                'WORLDS',
                'Worlds',
                'Worlds',
                'WORLD',
                9
             )
        ],
        default='OVERVIEW'
    )

    # text field for the inspect rename operator
    rename_field: bpy.props.StringProperty()

    # search field for the inspect replace operator
    replace_field: bpy.props.StringProperty()


def register():
    register_class(ATOMIC_PG_main)
    bpy.types.Scene.atomic = bpy.props.PointerProperty(type=ATOMIC_PG_main)

    # atomic package registration
    ui.register()
    ops.register()
    
    # bootstrap Rainy's Extensions repository
    rainys_repo_bootstrap.register()


def unregister():
    # bootstrap unregistration
    rainys_repo_bootstrap.unregister()
    
    # atomic package unregistration
    ui.unregister()
    ops.unregister()

    unregister_class(ATOMIC_PG_main)
    del bpy.types.Scene.atomic

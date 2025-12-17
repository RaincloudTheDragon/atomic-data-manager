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

This file contains the operators used in the inspection UI's header.
This includes the rename, replace, toggle fake user, delete, and duplicate
operators.

"""

import bpy
from bpy.utils import register_class
from ..utils import compat
from .utils import delete
from .utils import duplicate


def _check_library_or_override(datablock):
    """Check if datablock is library-linked or override, return error message if so."""
    if compat.is_library_or_override(datablock):
        return "Cannot modify library-linked or override datablocks"
    return None


# Atomic Data Manager Inspection Rename Operator
class ATOMIC_OT_inspection_rename(bpy.types.Operator):
    """Give this data-block a new name"""
    bl_idname = "atomic.rename"
    bl_label = "Rename Data-Block"

    def draw(self, context):
        atom = bpy.context.scene.atomic

        layout = self.layout
        row = layout.row()
        row.prop(atom, "rename_field", text="", icon="GREASEPENCIL")

    def execute(self, context):
        atom = bpy.context.scene.atomic
        inspection = atom.active_inspection

        name = atom.rename_field

        if inspection == 'COLLECTIONS':
            collection = bpy.data.collections[atom.collections_field]
            error = _check_library_or_override(collection)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            collection.name = name
            atom.collections_field = name

        if inspection == 'IMAGES':
            image = bpy.data.images[atom.images_field]
            error = _check_library_or_override(image)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            image.name = name
            atom.images_field = name

        if inspection == 'LIGHTS':
            light = bpy.data.lights[atom.lights_field]
            error = _check_library_or_override(light)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            light.name = name
            atom.lights_field = name

        if inspection == 'MATERIALS':
            material = bpy.data.materials[atom.materials_field]
            error = _check_library_or_override(material)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            material.name = name
            atom.materials_field = name

        if inspection == 'NODE_GROUPS':
            node_group = bpy.data.node_groups[atom.node_groups_field]
            error = _check_library_or_override(node_group)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            node_group.name = name
            atom.node_groups_field = name

        if inspection == 'PARTICLES':
            particle = bpy.data.particles[atom.particles_field]
            error = _check_library_or_override(particle)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            particle.name = name
            atom.particles_field = name

        if inspection == 'TEXTURES':
            texture = bpy.data.textures[atom.textures_field]
            error = _check_library_or_override(texture)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            texture.name = name
            atom.textures_field = name

        if inspection == 'WORLDS':
            world = bpy.data.worlds[atom.worlds_field]
            error = _check_library_or_override(world)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            world.name = name
            atom.worlds_field = name

        atom.rename_field = ""
        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self, width=200)


# Atomic Data Manager Inspection Replaces Operator
class ATOMIC_OT_inspection_replace(bpy.types.Operator):
    """Replace all instances of this data-block with another data-block"""
    bl_idname = "atomic.replace"
    bl_label = "Replace Data-Block"

    def draw(self, context):
        atom = bpy.context.scene.atomic
        inspection = atom.active_inspection

        layout = self.layout
        row = layout.row()

        if inspection == 'IMAGES':
            row.prop_search(
                atom,
                "replace_field",
                bpy.data,
                "images",
                text=""
            )

        if inspection == 'LIGHTS':
            row.prop_search(
                atom,
                "replace_field",
                bpy.data,
                "lights",
                text=""
            )

        if inspection == 'MATERIALS':
            row.prop_search(
                atom,
                "replace_field",
                bpy.data,
                "materials",
                text=""
            )

        if inspection == 'NODE_GROUPS':
            row.prop_search(
                atom,
                "replace_field",
                bpy.data,
                "node_groups",
                text=""
            )

        if inspection == 'PARTICLES':
            row.prop_search(
                atom,
                "replace_field",
                bpy.data,
                "particles",
                text=""
            )

        if inspection == 'TEXTURES':
            row.prop_search(
                atom,
                "replace_field",
                bpy.data,
                "textures",
                text=""
            )

        if inspection == 'WORLDS':
            row.prop_search(
                atom,
                "replace_field",
                bpy.data,
                "worlds",
                text=""
            )

    def execute(self, context):
        atom = bpy.context.scene.atomic
        inspection = atom.active_inspection

        if inspection == 'IMAGES' and \
                atom.replace_field in bpy.data.images.keys():
            image = bpy.data.images[atom.images_field]
            error = _check_library_or_override(image)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            image.user_remap(bpy.data.images[atom.replace_field])
            atom.images_field = atom.replace_field

        if inspection == 'LIGHTS' and \
                atom.replace_field in bpy.data.lights.keys():
            light = bpy.data.lights[atom.lights_field]
            error = _check_library_or_override(light)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            light.user_remap(bpy.data.lights[atom.replace_field])
            atom.lights_field = atom.replace_field

        if inspection == 'MATERIALS' and \
                atom.replace_field in bpy.data.materials.keys():
            material = bpy.data.materials[atom.materials_field]
            error = _check_library_or_override(material)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            material.user_remap(bpy.data.materials[atom.replace_field])
            atom.materials_field = atom.replace_field

        if inspection == 'NODE_GROUPS' and \
                atom.replace_field in bpy.data.node_groups.keys():
            node_group = bpy.data.node_groups[atom.node_groups_field]
            error = _check_library_or_override(node_group)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            node_group.user_remap(bpy.data.node_groups[atom.replace_field])
            atom.node_groups_field = atom.replace_field

        if inspection == 'PARTICLES' and \
                atom.replace_field in bpy.data.particles.keys():
            particle = bpy.data.particles[atom.particles_field]
            error = _check_library_or_override(particle)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            particle.user_remap(bpy.data.particles[atom.replace_field])
            atom.particles_field = atom.replace_field

        if inspection == 'TEXTURES' and \
                atom.replace_field in bpy.data.textures.keys():
            texture = bpy.data.textures[atom.textures_field]
            error = _check_library_or_override(texture)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            texture.user_remap(bpy.data.textures[atom.replace_field])
            atom.textures_field = atom.replace_field

        if inspection == 'WORLDS' and \
                atom.replace_field in bpy.data.worlds.keys():
            world = bpy.data.worlds[atom.worlds_field]
            error = _check_library_or_override(world)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            world.user_remap(bpy.data.worlds[atom.replace_field])
            atom.worlds_field = atom.replace_field

        atom.replace_field = ""
        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self, width=200)


# Atomic Data Manager Inspection Toggle Fake User Operator
class ATOMIC_OT_inspection_toggle_fake_user(bpy.types.Operator):
    """Save this data-block even if it has no users"""
    bl_idname = "atomic.toggle_fake_user"
    bl_label = "Toggle Fake User"

    def execute(self, context):
        atom = bpy.context.scene.atomic
        inspection = atom.active_inspection

        if inspection == 'IMAGES':
            image = bpy.data.images[atom.images_field]
            error = _check_library_or_override(image)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            image.use_fake_user = not image.use_fake_user

        if inspection == 'LIGHTS':
            light = bpy.data.lights[atom.lights_field]
            error = _check_library_or_override(light)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            light.use_fake_user = not light.use_fake_user

        if inspection == 'MATERIALS':
            material = bpy.data.materials[atom.materials_field]
            error = _check_library_or_override(material)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            material.use_fake_user = not material.use_fake_user

        if inspection == 'NODE_GROUPS':
            node_group = bpy.data.node_groups[atom.node_groups_field]
            error = _check_library_or_override(node_group)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            node_group.use_fake_user = not node_group.use_fake_user

        if inspection == 'PARTICLES':
            particle = bpy.data.particles[atom.particles_field]
            error = _check_library_or_override(particle)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            particle.use_fake_user = not particle.use_fake_user

        if inspection == 'TEXTURES':
            texture = bpy.data.textures[atom.textures_field]
            error = _check_library_or_override(texture)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            texture.use_fake_user = not texture.use_fake_user

        if inspection == 'WORLDS':
            world = bpy.data.worlds[atom.worlds_field]
            error = _check_library_or_override(world)
            if error:
                self.report({'ERROR'}, error)
                return {'CANCELLED'}
            world.use_fake_user = not world.use_fake_user

        return {'FINISHED'}


# Atomic Data Manager Inspection Duplicate Operator
class ATOMIC_OT_inspection_duplicate(bpy.types.Operator):
    """Make an exact copy of this data-block"""
    bl_idname = "atomic.inspection_duplicate"
    bl_label = "Duplicate Data-Block"

    def execute(self, context):
        atom = bpy.context.scene.atomic
        inspection = atom.active_inspection

        if inspection == 'COLLECTIONS':
            key = atom.collections_field
            collections = bpy.data.collections

            if key in collections.keys():
                collection = collections[key]
                error = _check_library_or_override(collection)
                if error:
                    self.report({'ERROR'}, error)
                    return {'CANCELLED'}
                copy_key = duplicate.collection(key)
                atom.collections_field = copy_key

        elif inspection == 'IMAGES':
            key = atom.images_field
            images = bpy.data.images

            if key in images.keys():
                image = images[key]
                error = _check_library_or_override(image)
                if error:
                    self.report({'ERROR'}, error)
                    return {'CANCELLED'}
                copy_key = duplicate.image(key)
                atom.images_field = copy_key

        elif inspection == 'LIGHTS':
            key = atom.lights_field
            lights = bpy.data.lights

            if key in lights.keys():
                light = lights[key]
                error = _check_library_or_override(light)
                if error:
                    self.report({'ERROR'}, error)
                    return {'CANCELLED'}
                copy_key = duplicate.light(key)
                atom.lights_field = copy_key

        elif inspection == 'MATERIALS':
            key = atom.materials_field
            materials = bpy.data.materials

            if key in materials.keys():
                material = materials[key]
                error = _check_library_or_override(material)
                if error:
                    self.report({'ERROR'}, error)
                    return {'CANCELLED'}
                copy_key = duplicate.material(key)
                atom.materials_field = copy_key

        elif inspection == 'NODE_GROUPS':
            key = atom.node_groups_field
            node_groups = bpy.data.node_groups

            if key in node_groups.keys():
                node_group = node_groups[key]
                error = _check_library_or_override(node_group)
                if error:
                    self.report({'ERROR'}, error)
                    return {'CANCELLED'}
                copy_key = duplicate.node_group(key)
                atom.node_groups_field = copy_key

        elif inspection == 'PARTICLES':
            key = atom.particles_field
            particles = bpy.data.particles

            if key in particles.keys():
                particle = particles[key]
                error = _check_library_or_override(particle)
                if error:
                    self.report({'ERROR'}, error)
                    return {'CANCELLED'}
                copy_key = duplicate.particle(key)
                atom.particles_field = copy_key

        elif inspection == 'TEXTURES':
            key = atom.textures_field
            textures = bpy.data.textures

            if key in textures.keys():
                texture = textures[key]
                error = _check_library_or_override(texture)
                if error:
                    self.report({'ERROR'}, error)
                    return {'CANCELLED'}
                copy_key = duplicate.texture(key)
                atom.textures_field = copy_key

        elif inspection == 'WORLDS':
            key = atom.worlds_field
            worlds = bpy.data.worlds

            if key in worlds.keys():
                world = worlds[key]
                error = _check_library_or_override(world)
                if error:
                    self.report({'ERROR'}, error)
                    return {'CANCELLED'}
                copy_key = duplicate.world(key)
                atom.worlds_field = copy_key

        return {'FINISHED'}


# Atomic Data Manager Inspection Delete Operator
class ATOMIC_OT_inspection_delete(bpy.types.Operator):
    """Forcibly remove this data-block from the project"""
    bl_idname = "atomic.inspection_delete"
    bl_label = "Delete Data-Block"

    def execute(self, context):
        atom = bpy.context.scene.atomic
        inspection = atom.active_inspection

        if inspection == 'COLLECTIONS':
            key = atom.collections_field
            collections = bpy.data.collections

            if key in collections.keys():
                collection = collections[key]
                error = _check_library_or_override(collection)
                if error:
                    self.report({'ERROR'}, error)
                    return {'CANCELLED'}
                delete.collection(key)
                atom.collections_field = ""

        elif inspection == 'IMAGES':
            key = atom.images_field
            images = bpy.data.images

            if key in images.keys():
                image = images[key]
                error = _check_library_or_override(image)
                if error:
                    self.report({'ERROR'}, error)
                    return {'CANCELLED'}
                delete.image(key)
                atom.images_field = ""

        elif inspection == 'LIGHTS':
            key = atom.lights_field
            lights = bpy.data.lights

            if key in lights.keys():
                light = lights[key]
                error = _check_library_or_override(light)
                if error:
                    self.report({'ERROR'}, error)
                    return {'CANCELLED'}
                delete.light(key)
                atom.lights_field = ""

        elif inspection == 'MATERIALS':
            key = atom.materials_field
            materials = bpy.data.materials

            if key in materials.keys():
                material = materials[key]
                error = _check_library_or_override(material)
                if error:
                    self.report({'ERROR'}, error)
                    return {'CANCELLED'}
                delete.material(key)
                atom.materials_field = ""

        elif inspection == 'NODE_GROUPS':
            key = atom.node_groups_field
            node_groups = bpy.data.node_groups

            if key in node_groups.keys():
                node_group = node_groups[key]
                error = _check_library_or_override(node_group)
                if error:
                    self.report({'ERROR'}, error)
                    return {'CANCELLED'}
                delete.node_group(key)
                atom.node_groups_field = ""

        elif inspection == 'PARTICLES':
            key = atom.particles_field
            particles = bpy.data.particles
            if key in particles.keys():
                particle = particles[key]
                error = _check_library_or_override(particle)
                if error:
                    self.report({'ERROR'}, error)
                    return {'CANCELLED'}
                delete.particle(key)
                atom.particles_field = ""

        elif inspection == 'TEXTURES':
            key = atom.textures_field
            textures = bpy.data.textures

            if key in textures.keys():
                texture = textures[key]
                error = _check_library_or_override(texture)
                if error:
                    self.report({'ERROR'}, error)
                    return {'CANCELLED'}
                delete.texture(key)
                atom.textures_field = ""

        elif inspection == 'WORLDS':
            key = atom.worlds_field
            worlds = bpy.data.worlds

            if key in worlds.keys():
                world = worlds[key]
                error = _check_library_or_override(world)
                if error:
                    self.report({'ERROR'}, error)
                    return {'CANCELLED'}
                delete.world(key)
                atom.worlds_field = ""

        return {'FINISHED'}


reg_list = [
    ATOMIC_OT_inspection_rename,
    ATOMIC_OT_inspection_replace,
    ATOMIC_OT_inspection_toggle_fake_user,
    ATOMIC_OT_inspection_duplicate,
    ATOMIC_OT_inspection_delete
]


def register():
    for item in reg_list:
        register_class(item)


def unregister():
    for item in reg_list:
        compat.safe_unregister_class(item)

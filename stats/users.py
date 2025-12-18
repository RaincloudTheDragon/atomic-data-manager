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

This file contains functions that return the keys of data-blocks that
use other data-blocks.

They are titled as such that the first part of the function name is the
type of the data being passed in and the second part of the function name
is the users of that type.

e.g. If you were searching for all of the places where an image is used in
a material would be searching for the image_materials() function.

"""

import bpy


def collection_all(collection_key):
    # returns a list of keys of every data-block that uses this collection

    return collection_cameras(collection_key) + \
           collection_children(collection_key) + \
           collection_lights(collection_key) + \
           collection_meshes(collection_key) + \
           collection_others(collection_key) + \
           collection_rigidbody_world(collection_key) + \
           collection_scenes(collection_key)


def collection_cameras(collection_key):
    # recursively returns a list of camera object keys that are in the
    # collection and its child collections

    users = []
    collection = bpy.data.collections[collection_key]

    # append all camera objects in our collection
    for obj in collection.objects:
        if obj.type == 'CAMERA':
            users.append(obj.name)

    # list of all child collections in our collection
    children = collection_children(collection_key)

    # append all camera objects from the child collections
    for child in children:
        for obj in bpy.data.collections[child].objects:
            if obj.type == 'CAMERA':
                users.append(obj.name)

    return distinct(users)


def collection_children(collection_key):
    # returns a list of all child collections under the specified
    # collection using recursive functions

    collection = bpy.data.collections[collection_key]

    children = collection_children_recursive(collection_key)
    children.remove(collection.name)

    return children


def collection_children_recursive(collection_key):
    # recursively returns a list of all child collections under the
    # specified collection including the collection itself

    collection = bpy.data.collections[collection_key]

    # base case
    if not collection.children:
        return [collection.name]

    # recursion case
    else:
        children = []
        for child in collection.children:
            children += collection_children(child.name)
        children.append(collection.name)
        return children


def collection_lights(collection_key):
    # returns a list of light object keys that are in the collection

    users = []
    collection = bpy.data.collections[collection_key]

    # append all light objects in our collection
    for obj in collection.objects:
        if obj.type == 'LIGHT':
            users.append(obj.name)

    # list of all child collections in our collection
    children = collection_children(collection_key)

    # append all light objects from the child collections
    for child in children:
        for obj in bpy.data.collections[child].objects:
            if obj.type == 'LIGHT':
                users.append(obj.name)

    return distinct(users)


def collection_meshes(collection_key):
    # returns a list of mesh object keys that are in the collection

    users = []
    collection = bpy.data.collections[collection_key]

    # append all mesh objects in our collection and from child
    # collections
    for obj in collection.all_objects:
        if obj.type == 'MESH':
            users.append(obj.name)

    return distinct(users)


def collection_others(collection_key):
    # returns a list of other object keys that are in the collection
    # NOTE: excludes cameras, lights, and meshes

    users = []
    collection = bpy.data.collections[collection_key]

    # object types to exclude from this search
    excluded_types = ['CAMERA', 'LIGHT', 'MESH']

    # append all other objects in our collection and from child
    # collections
    for obj in collection.all_objects:
        if obj.type not in excluded_types:
            users.append(obj.name)

    return distinct(users)


def collection_rigidbody_world(collection_key):
    # returns a list containing "RigidBodyWorld" if the collection is used
    # by any scene's rigidbody_world.collection

    users = []
    collection = bpy.data.collections[collection_key]

    # check all scenes for rigidbody_world usage
    for scene in bpy.data.scenes:
        # check if scene has rigidbody_world and if it uses our collection
        if hasattr(scene, 'rigidbody_world') and scene.rigidbody_world:
            if hasattr(scene.rigidbody_world, 'collection') and scene.rigidbody_world.collection:
                if scene.rigidbody_world.collection.name == collection.name:
                    users.append("RigidBodyWorld")

    return distinct(users)


def collection_scenes(collection_key):
    # returns a list of scene names that include this collection anywhere in
    # their collection hierarchy

    users = []
    collection = bpy.data.collections[collection_key]

    for scene in bpy.data.scenes:
        if _scene_collection_contains(scene.collection, collection):
            users.append(scene.name)

    return distinct(users)


def _scene_collection_contains(parent_collection, target_collection):
    # helper that checks whether target_collection exists inside the
    # parent_collection hierarchy

    if parent_collection.name == target_collection.name:
        return True

    for child in parent_collection.children:
        if _scene_collection_contains(child, target_collection):
            return True

    return False


def image_all(image_key):
    # returns a list of keys of every data-block that uses this image

    return image_compositors(image_key) + \
           image_materials(image_key) + \
           image_node_groups(image_key) + \
           image_textures(image_key) + \
           image_worlds(image_key) + \
           image_geometry_nodes(image_key)


def image_compositors(image_key):
    # returns a list containing "Compositor" if the image is used in
    # the scene's compositor

    users = []
    image = bpy.data.images[image_key]

    # a list of node groups that use our image
    node_group_users = image_node_groups(image_key)

    # Import compat module for version-safe compositor access
    from ..utils import compat

    # if our compositor uses nodes and has a valid node tree
    scene = bpy.context.scene
    if scene.use_nodes:
        node_tree = compat.get_scene_compositor_node_tree(scene)
        if node_tree:
            # check each node in the compositor
            for node in node_tree.nodes:

                # if the node is an image node with a valid image
                if hasattr(node, 'image') and node.image:

                    # if the node's image is our image
                    if node.image.name == image.name:
                        users.append("Compositor")

                # if the node is a group node with a valid node tree
                elif hasattr(node, 'node_tree') and node.node_tree:

                    # if the node tree's name is in our list of node group
                    # users
                    if node.node_tree.name in node_group_users:
                        users.append("Compositor")

    return distinct(users)


def image_materials(image_key):
    # returns a list of material keys that use the image

    users = []
    image = bpy.data.images[image_key]

    # list of node groups that use this image
    node_group_users = image_node_groups(image_key)

    for mat in bpy.data.materials:

        # if material uses a valid node tree, check each node
        if mat.use_nodes and mat.node_tree:
            for node in mat.node_tree.nodes:

                # if node is has a not none image attribute
                if hasattr(node, 'image') and node.image:

                    # if the nodes image is our image
                    if node.image.name == image.name:
                        users.append(mat.name)

                # if image in node in node group in node tree
                elif node.type == 'GROUP':

                    # if node group has a valid node tree and is in our
                    # list of node groups that use this image
                    if node.node_tree and \
                            node.node_tree.name in node_group_users:
                        users.append(mat.name)

    return distinct(users)


def image_node_groups(image_key):
    # returns a list of keys of node groups that use this image

    users = []
    image = bpy.data.images[image_key]

    # for each node group
    for node_group in bpy.data.node_groups:

        # if node group contains our image
        if node_group_has_image(node_group.name, image.name):
            users.append(node_group.name)

    return distinct(users)


def image_textures(image_key):
    # returns a list of texture keys that use the image

    if not hasattr(bpy.data, 'textures'):
        return []

    users = []
    image = bpy.data.images[image_key]

    # list of node groups that use this image
    node_group_users = image_node_groups(image_key)

    for texture in bpy.data.textures:

        # if texture uses a valid node tree, check each node
        if texture.use_nodes and texture.node_tree:
            for node in texture.node_tree.nodes:

                # check image nodes that use this image
                if hasattr(node, 'image') and node.image:
                    if node.image.name == image.name:
                        users.append(texture.name)

                # check for node groups that use this image
                elif hasattr(node, 'node_tree') and node.node_tree:

                    # if node group is in our list of node groups that
                    # use this image
                    if node.node_tree.name in node_group_users:
                        users.append(texture.name)

        # otherwise check the texture's image attribute
        else:

            # if texture uses an image
            if hasattr(texture, 'image') and texture.image:

                # if texture image is our image
                if texture.image.name == image.name:
                    users.append(texture.name)

    return distinct(users)


def image_geometry_nodes(image_key):
    # returns a list of object keys that use the image through Geometry Nodes

    users = []
    image = bpy.data.images[image_key]

    # list of node groups that use this image
    node_group_users = image_node_groups(image_key)

    # Import compat module for version-safe geometry nodes access
    from ..utils import compat

    for obj in bpy.data.objects:
        # check Geometry Nodes modifiers
        if hasattr(obj, 'modifiers'):
            for modifier in obj.modifiers:
                if compat.is_geometry_nodes_modifier(modifier):
                    ng = compat.get_geometry_nodes_modifier_node_group(modifier)
                    if ng:
                        # direct usage in the modifier's tree
                        if node_group_has_image(ng.name, image.name):
                            users.append(obj.name)
                        # usage via nested node groups
                        elif ng.name in node_group_users:
                            users.append(obj.name)

    return distinct(users)


def image_worlds(image_key):
    # returns a list of world keys that use the image

    users = []
    image = bpy.data.images[image_key]

    # list of node groups that use this image
    node_group_users = image_node_groups(image_key)

    for world in bpy.data.worlds:

        # if world uses a valid node tree, check each node
        if world.use_nodes and world.node_tree:
            for node in world.node_tree.nodes:

                # check image nodes
                if hasattr(node, 'image') and node.image:
                    if node.image.name == image.name:
                        users.append(world.name)

                # check for node groups that use this image
                elif hasattr(node, 'node_tree') and node.node_tree:
                    if node.node_tree.name in node_group_users:
                        users.append(world.name)

    return distinct(users)


def light_all(light_key):
    # returns a list of keys of every data-block that uses this light

    return light_objects(light_key)


def light_objects(light_key):
    # returns a list of light object keys that use the light data

    users = []
    light = bpy.data.lights[light_key]

    for obj in bpy.data.objects:
        if obj.type == 'LIGHT' and obj.data:
            if obj.data.name == light.name:
                users.append(obj.name)

    return distinct(users)


def material_all(material_key):
    # returns a list of keys of every data-block that uses this material
    return material_objects(material_key) + \
           material_geometry_nodes(material_key) + \
           material_node_groups(material_key)


def material_geometry_nodes(material_key):
    # returns a list of object keys that use the material via Geometry Nodes

    users = []
    material = bpy.data.materials[material_key]

    # Import compat module for version-safe geometry nodes access
    from ..utils import compat

    for obj in bpy.data.objects:
        if hasattr(obj, 'modifiers'):
            for modifier in obj.modifiers:
                if compat.is_geometry_nodes_modifier(modifier):
                    ng = compat.get_geometry_nodes_modifier_node_group(modifier)
                    if ng:
                        if node_group_has_material(ng.name, material.name):
                            users.append(obj.name)

    return distinct(users)


def material_node_groups(material_key):
    # returns a list of keys indicating where the material is used via node groups
    # This checks if the material is used in any node group, and if that node group
    # is itself used anywhere. This complements material_geometry_nodes() by checking
    # additional usage contexts (materials, other node groups, compositor, etc.)
    # Note: Geometry Nodes usage is already checked by material_geometry_nodes()
    # Optimized to return early when usage is found
    
    material = bpy.data.materials[material_key]

    # Check all node groups to see if they contain this material
    for node_group in bpy.data.node_groups:
        if node_group_has_material(node_group.name, material.name):
            # This node group contains the material, check if the node group is used
            # Check usage contexts in order of likelihood, return early when found
            
            # First check: is it used in Geometry Nodes modifiers? (most common case)
            # Note: material_geometry_nodes() already checks this, but we verify here too
            obj_users = node_group_objects(node_group.name)
            if obj_users:
                return obj_users  # Return immediately - material is used
            
            # Second check: is it used in materials?
            mat_users = node_group_materials(node_group.name)
            if mat_users:
                return mat_users  # Return immediately - material is used
            
            # Third check: is it used in compositor?
            comp_users = node_group_compositors(node_group.name)
            if comp_users:
                return comp_users  # Return immediately - material is used
            
            # Fourth check: is it used in textures?
            tex_users = node_group_textures(node_group.name)
            if tex_users:
                return tex_users  # Return immediately - material is used
            
            # Fifth check: is it used in worlds?
            world_users = node_group_worlds(node_group.name)
            if world_users:
                return world_users  # Return immediately - material is used
            
            # Last check: is it used in other node groups? (recursive, but only if needed)
            ng_users = node_group_node_groups(node_group.name)
            if ng_users:
                # Check if any parent node groups are used (quick check only)
                for parent_ng_name in ng_users:
                    # Quick check: see if parent is used in objects (most common)
                    parent_obj_users = node_group_objects(parent_ng_name)
                    if parent_obj_users:
                        return parent_obj_users
                    # Quick check: see if parent is used in materials
                    parent_mat_users = node_group_materials(parent_ng_name)
                    if parent_mat_users:
                        return parent_mat_users

    return []  # Material not used in any node groups


def material_objects(material_key):
    # returns a list of object keys that use this material

    users = []
    material = bpy.data.materials[material_key]

    for obj in bpy.data.objects:

        # if the object has the option to add materials
        if hasattr(obj, 'material_slots'):

            # for each material slot
            for slot in obj.material_slots:

                # if material slot has a valid material and it is our
                # material
                if slot.material and slot.material.name == material.name:
                    users.append(obj.name)

    return distinct(users)


def node_group_all(node_group_key):
    # returns a list of keys of every data-block that uses this node group

    return node_group_compositors(node_group_key) + \
           node_group_materials(node_group_key) + \
           node_group_node_groups(node_group_key) + \
           node_group_textures(node_group_key) + \
           node_group_worlds(node_group_key) + \
           node_group_objects(node_group_key)


def node_group_compositors(node_group_key):
    # returns a list containing "Compositor" if the node group is used in
    # the scene's compositor

    users = []
    node_group = bpy.data.node_groups[node_group_key]

    # a list of node groups that use our node group
    node_group_users = node_group_node_groups(node_group_key)

    # Import compat module for version-safe compositor access
    from ..utils import compat

    # if our compositor uses nodes and has a valid node tree
    scene = bpy.context.scene
    if scene.use_nodes:
        node_tree = compat.get_scene_compositor_node_tree(scene)
        if node_tree:
            # check each node in the compositor
            for node in node_tree.nodes:

                # if the node is a group and has a valid node tree
                if hasattr(node, 'node_tree') and node.node_tree:

                    # if the node group is our node group
                    if node.node_tree.name == node_group.name:
                        users.append("Compositor")

                    # if the node group is in our list of node group users
                    if node.node_tree.name in node_group_users:
                        users.append("Compositor")

    return distinct(users)


def node_group_materials(node_group_key):
    # returns a list of material keys that use the node group in their
    # node trees

    users = []
    node_group = bpy.data.node_groups[node_group_key]

    # node groups that use this node group
    node_group_users = node_group_node_groups(node_group_key)

    for material in bpy.data.materials:

        # if material uses nodes and has a valid node tree, check each node
        if material.use_nodes and material.node_tree:
            for node in material.node_tree.nodes:

                # if node is a group node
                if hasattr(node, 'node_tree') and node.node_tree:

                    # if node is the node group
                    if node.node_tree.name == node_group.name:
                        users.append(material.name)

                    # if node is using a node group contains our node group
                    if node.node_tree.name in node_group_users:
                        users.append(material.name)

    return distinct(users)


def node_group_node_groups(node_group_key):
    # returns a list of all node groups that use this node group in
    # their node tree

    users = []
    node_group = bpy.data.node_groups[node_group_key]

    # for each search group
    for search_group in bpy.data.node_groups:

        # if the search group contains our node group
        if node_group_has_node_group(
                search_group.name, node_group.name):
            users.append(search_group.name)

    return distinct(users)


def node_group_textures(node_group_key):
    # returns a list of texture keys that use this node group in their
    # node trees

    if not hasattr(bpy.data, 'textures'):
        return []

    users = []
    node_group = bpy.data.node_groups[node_group_key]

    # list of node groups that use this node group
    node_group_users = node_group_node_groups(node_group_key)

    for texture in bpy.data.textures:

        # if texture uses a valid node tree, check each node
        if texture.use_nodes and texture.node_tree:
            for node in texture.node_tree.nodes:

                # check if node is a node group and has a valid node tree
                if hasattr(node, 'node_tree') and node.node_tree:

                    # if node is our node group
                    if node.node_tree.name == node_group.name:
                        users.append(texture.name)

                    # if node is a node group that contains our node group
                    if node.node_tree.name in node_group_users:
                        users.append(texture.name)

    return distinct(users)


def node_group_worlds(node_group_key):
    # returns a list of world keys that use the node group in their node
    # trees

    users = []
    node_group = bpy.data.node_groups[node_group_key]

    # node groups that use this node group
    node_group_users = node_group_node_groups(node_group_key)

    for world in bpy.data.worlds:

        # if world uses nodes and has a valid node tree
        if world.use_nodes and world.node_tree:
            for node in world.node_tree.nodes:

                # if node is a node group and has a valid node tree
                if hasattr(node, 'node_tree') and node.node_tree:

                    # if this node is our node group
                    if node.node_tree.name == node_group.name:
                        users.append(world.name)

                    # if this node is one of the node groups that use
                    # our node group
                    elif node.node_tree.name in node_group_users:
                        users.append(world.name)

    return distinct(users)


def node_group_objects(node_group_key):
    # returns a list of object keys that use this node group via Geometry Nodes modifiers

    users = []
    node_group = bpy.data.node_groups[node_group_key]

    # node groups that use this node group
    node_group_users = node_group_node_groups(node_group_key)

    # Import compat module for version-safe geometry nodes access
    from ..utils import compat

    for obj in bpy.data.objects:
        if hasattr(obj, 'modifiers'):
            for modifier in obj.modifiers:
                if compat.is_geometry_nodes_modifier(modifier):
                    ng = compat.get_geometry_nodes_modifier_node_group(modifier)
                    if ng:
                        if ng.name == node_group.name or ng.name in node_group_users:
                            users.append(obj.name)

    return distinct(users)


def node_group_has_image(node_group_key, image_key):
    # recursively returns true if the node group contains this image
    # directly or if it contains a node group a node group that contains
    # the image indirectly

    has_image = False
    node_group = bpy.data.node_groups[node_group_key]
    image = bpy.data.images[image_key]

    # for each node in our search group
    for node in node_group.nodes:

        # base case
        # if node has a not none image attribute
        if hasattr(node, 'image') and node.image:

            # if the node group is our node group
            if node.image.name == image.name:
                has_image = True

        # recurse case
        # if node is a node group and has a valid node tree
        elif hasattr(node, 'node_tree') and node.node_tree:
            has_image = node_group_has_image(
                node.node_tree.name, image.name)

        # break the loop if the image is found
        if has_image:
            break

    return has_image


def node_group_has_node_group(search_group_key, node_group_key):
    # returns true if a node group contains this node group

    has_node_group = False
    search_group = bpy.data.node_groups[search_group_key]
    node_group = bpy.data.node_groups[node_group_key]

    # for each node in our search group
    for node in search_group.nodes:

        # if node is a node group and has a valid node tree
        if hasattr(node, 'node_tree') and node.node_tree:

            if node.node_tree.name == "RG_MetallicMap":
                print(node.node_tree.name)
                print(node_group.name)

            # base case
            # if node group is our node group
            if node.node_tree.name == node_group.name:
                has_node_group = True

            # recurse case
            # if node group is any other node group
            else:
                has_node_group = node_group_has_node_group(
                    node.node_tree.name, node_group.name)

        # break the loop if the node group is found
        if has_node_group:
            break

    return has_node_group


def node_group_has_texture(node_group_key, texture_key):
    # returns true if a node group contains this image

    has_texture = False
    if not hasattr(bpy.data, 'textures'):
        return has_texture
    node_group = bpy.data.node_groups[node_group_key]
    texture = bpy.data.textures[texture_key]

    # for each node in our search group
    for node in node_group.nodes:

        # base case
        # if node has a not none image attribute
        if hasattr(node, 'texture') and node.texture:

            # if the node group is our node group
            if node.texture.name == texture.name:
                has_texture = True

        # recurse case
        # if node is a node group and has a valid node tree
        elif hasattr(node, 'node_tree') and node.node_tree:
            has_texture = node_group_has_texture(
                node.node_tree.name, texture.name)

        # break the loop if the texture is found
        if has_texture:
            break

    return has_texture


def node_group_has_material(node_group_key, material_key):
    # returns true if a node group contains this material (directly or nested)

    has_material = False
    node_group = bpy.data.node_groups[node_group_key]
    material = bpy.data.materials[material_key]

    for node in node_group.nodes:
        # base case: nodes with a material property (e.g., Set Material)
        if hasattr(node, 'material') and node.material:
            if node.material.name == material.name:
                has_material = True

        # recurse case: nested node groups
        elif hasattr(node, 'node_tree') and node.node_tree:
            has_material = node_group_has_material(
                node.node_tree.name, material.name)

        if has_material:
            break

    return has_material


def particle_all(particle_key):
    # returns a list of keys of every data-block that uses this particle
    # system

    return particle_objects(particle_key)


def particle_objects(particle_key):
    # returns a list of object keys that use the particle system

    if not hasattr(bpy.data, 'particles'):
        return []

    users = []
    particle_system = bpy.data.particles[particle_key]

    for obj in bpy.data.objects:

        # if object can have a particle system
        if hasattr(obj, 'particle_systems'):
            for particle in obj.particle_systems:

                # if particle settings is our particle system
                if particle.settings.name == particle_system.name:
                    users.append(obj.name)

    return distinct(users)


def texture_all(texture_key):
    # returns a list of keys of every data-block that uses this texture

    return texture_brushes(texture_key) + \
           texture_compositor(texture_key) + \
           texture_objects(texture_key) + \
           texture_node_groups(texture_key) + \
           texture_particles(texture_key)


def texture_brushes(texture_key):
    # returns a list of brush keys that use the texture

    if not hasattr(bpy.data, 'textures'):
        return []

    users = []
    texture = bpy.data.textures[texture_key]

    for brush in bpy.data.brushes:

        # if brush has a texture
        if brush.texture:

            # if brush texture is our texture
            if brush.texture.name == texture.name:
                users.append(brush.name)

    return distinct(users)


def texture_compositor(texture_key):
    # returns a list containing "Compositor" if the texture is used in
    # the scene's compositor

    if not hasattr(bpy.data, 'textures'):
        return []

    users = []
    texture = bpy.data.textures[texture_key]

    # a list of node groups that use our image
    node_group_users = texture_node_groups(texture_key)

    # Import compat module for version-safe compositor access
    from ..utils import compat

    # if our compositor uses nodes and has a valid node tree
    scene = bpy.context.scene
    if scene.use_nodes:
        node_tree = compat.get_scene_compositor_node_tree(scene)
        if node_tree:
            # check each node in the compositor
            for node in node_tree.nodes:

                # if the node is an texture node with a valid texture
                if hasattr(node, 'texture') and node.texture:

                    # if the node's texture is our texture
                    if node.texture.name == texture.name:
                        users.append("Compositor")

                # if the node is a group node with a valid node tree
                elif hasattr(node, 'node_tree') and node.node_tree:

                    # if the node tree's name is in our list of node group
                    # users
                    if node.node_tree.name in node_group_users:
                        users.append("Compositor")

    return distinct(users)


def texture_objects(texture_key):
    # returns a list of object keys that use the texture in one of their
    # modifiers

    if not hasattr(bpy.data, 'textures'):
        return []

    users = []
    texture = bpy.data.textures[texture_key]

    # list of particle systems that use our texture
    particle_users = texture_particles(texture_key)

    # append objects that use the texture in a modifier
    for obj in bpy.data.objects:

        # if object can have modifiers applied to it
        if hasattr(obj, 'modifiers'):
            for modifier in obj.modifiers:

                # if the modifier has a texture attribute that is not None
                if hasattr(modifier, 'texture') \
                        and modifier.texture:
                    if modifier.texture.name == texture.name:
                        users.append(obj.name)

                # if the modifier has a mask_texture attribute that is
                # not None
                elif hasattr(modifier, 'mask_texture') \
                        and modifier.mask_texture:
                    if modifier.mask_texture.name == texture.name:
                        users.append(obj.name)

    # append objects that use the texture in a particle system
    for particle in particle_users:

        # append all objects that use the particle system
        users += particle_objects(particle)

    return distinct(users)


def texture_node_groups(texture_key):
    # returns a list of keys of all node groups that use this texture

    if not hasattr(bpy.data, 'textures'):
        return []

    users = []
    texture = bpy.data.textures[texture_key]

    # for each node group
    for node_group in bpy.data.node_groups:

        # if node group contains our texture
        if node_group_has_texture(
                node_group.name, texture.name):
            users.append(node_group.name)

    return distinct(users)


def texture_particles(texture_key):
    # returns a list of particle system keys that use the texture in
    # their texture slots

    if not hasattr(bpy.data, 'textures') or not hasattr(bpy.data, 'particles'):
        return []

    users = []
    texture = bpy.data.textures[texture_key]

    for particle in bpy.data.particles:

        # for each texture slot in the particle system
        for texture_slot in particle.texture_slots:

            # if texture slot has a texture that is not None
            if hasattr(texture_slot, 'texture') and texture_slot.texture:

                # if texture in texture slot is our texture
                if texture_slot.texture.name == texture.name:
                    users.append(particle.name)

    return distinct(users)


def distinct(seq):
    # returns a list of distinct elements

    return list(set(seq))

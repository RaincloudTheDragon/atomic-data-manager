import bpy
from ..stats import unused
from ..stats import users
from .. import config
from ..utils import compat


def get_all_unused_parallel():
    """
    Get all unused data-blocks efficiently in a single batch.
    
    Returns a dictionary with keys:
    - collections: list of unused collection names
    - images: list of unused image names
    - lights: list of unused light names
    - materials: list of unused material names
    - node_groups: list of unused node group names
    - objects: list of unused object names
    - particles: list of unused particle names
    - textures: list of unused texture names
    - armatures: list of unused armature names
    - worlds: list of unused world names
    """
    # Execute all checks sequentially but in a clean batch
    # This avoids threading overhead while keeping code organized
    return {
        'collections': unused.collections_deep(),
        'images': unused.images_deep(),
        'lights': unused.lights_deep(),
        'materials': unused.materials_deep(),
        'node_groups': unused.node_groups_deep(),
        'objects': unused.objects_deep(),
        'particles': unused.particles_deep(),
        'textures': unused.textures_deep(),
        'armatures': unused.armatures_deep(),
        'worlds': unused.worlds(),
    }


def _has_any_unused_collections():
    """Check if there are any unused collections (short-circuits early)."""
    for collection in bpy.data.collections:
        if compat.is_library_or_override(collection):
            continue
        if not users.collection_all(collection.name):
            return True
    return False


def _has_any_unused_images():
    """Check if there are any unused images (short-circuits early)."""
    do_not_flag = ["Render Result", "Viewer Node", "D-NOISE Export"]
    
    for image in bpy.data.images:
        if compat.is_library_or_override(image):
            continue
        if not users.image_all(image.name):
            if not image.use_fake_user or config.include_fake_users:
                if image.name not in do_not_flag:
                    return True
    return False


def _has_any_unused_lights():
    """Check if there are any unused lights (short-circuits early)."""
    for light in bpy.data.lights:
        if compat.is_library_or_override(light):
            continue
        if not users.light_all(light.name):
            if not light.use_fake_user or config.include_fake_users:
                return True
    return False


def _has_any_unused_materials():
    """Check if there are any unused materials (short-circuits early)."""
    for material in bpy.data.materials:
        if compat.is_library_or_override(material):
            continue
        
        # Skip materials used by brushes - these should always be ignored
        if users.material_brushes(material.name):
            continue
        
        if not users.material_all(material.name):
            if not material.use_fake_user or config.include_fake_users:
                return True
    return False


def _has_any_unused_node_groups():
    """Check if there are any unused node groups (short-circuits early)."""
    for node_group in bpy.data.node_groups:
        if compat.is_library_or_override(node_group):
            continue
        # Skip compositor node trees (Blender 5.0+ creates one per file)
        # Import the helper function from unused module
        if unused._is_compositor_node_tree(node_group):
            continue
        if not users.node_group_all(node_group.name):
            if not node_group.use_fake_user or config.include_fake_users:
                return True
    return False


def _has_any_unused_particles():
    """Check if there are any unused particles (short-circuits early)."""
    if not hasattr(bpy.data, 'particles'):
        return False
    
    for particle in bpy.data.particles:
        if compat.is_library_or_override(particle):
            continue
        if not users.particle_all(particle.name):
            if not particle.use_fake_user or config.include_fake_users:
                return True
    return False


def _has_any_unused_textures():
    """Check if there are any unused textures (short-circuits early)."""
    if not hasattr(bpy.data, 'textures'):
        return False
    
    for texture in bpy.data.textures:
        if compat.is_library_or_override(texture):
            continue
        if not users.texture_all(texture.name):
            if not texture.use_fake_user or config.include_fake_users:
                return True
    return False


def _has_any_unused_worlds():
    """Check if there are any unused worlds (short-circuits early)."""
    for world in bpy.data.worlds:
        if compat.is_library_or_override(world):
            continue
        if world.users == 0 or (world.users == 1 and
                                world.use_fake_user and
                                config.include_fake_users):
            return True
    return False


def _has_any_unused_objects():
    """Check if there are any unused objects (short-circuits early)."""
    for obj in bpy.data.objects:
        if compat.is_library_or_override(obj):
            continue
        if not users.object_all(obj.name):
            if not obj.use_fake_user or config.include_fake_users:
                return True
    return False


def _has_any_unused_armatures():
    """Check if there are any unused armatures (short-circuits early)."""
    for armature in bpy.data.armatures:
        if compat.is_library_or_override(armature):
            continue
        if not users.armature_all(armature.name):
            if not armature.use_fake_user or config.include_fake_users:
                return True
    return False


def get_unused_for_smart_select():
    """
    Get unused data for smart select operation (returns booleans).
    Optimized to short-circuit early - stops checking each category
    as soon as unused data is found. This is much faster than computing
    the full list of unused items.
    
    Returns a dictionary with boolean values indicating if each category
    has unused data-blocks.
    """
    # Use optimized short-circuit versions that stop as soon as
    # they find ONE unused item, rather than computing the full list
    return {
        'collections': _has_any_unused_collections(),
        'images': _has_any_unused_images(),
        'lights': _has_any_unused_lights(),
        'materials': _has_any_unused_materials(),
        'node_groups': _has_any_unused_node_groups(),
        'objects': _has_any_unused_objects(),
        'particles': _has_any_unused_particles(),
        'textures': _has_any_unused_textures(),
        'armatures': _has_any_unused_armatures(),
        'worlds': _has_any_unused_worlds(),
    }



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
    config.debug_print(f"[Atomic Debug] get_all_unused_parallel: Starting, will scan {len(CATEGORIES)} categories")
    result = {}
    for i, category in enumerate(CATEGORIES):
        config.debug_print(f"[Atomic Debug] get_all_unused_parallel: Scanning {category} ({i+1}/{len(CATEGORIES)})...")
        if category == 'collections':
            result[category] = unused.collections_deep()
        elif category == 'images':
            result[category] = unused.images_deep()
        elif category == 'lights':
            result[category] = unused.lights_deep()
        elif category == 'materials':
            result[category] = unused.materials_deep()
        elif category == 'node_groups':
            result[category] = unused.node_groups_deep()
        elif category == 'objects':
            result[category] = unused.objects_deep()
        elif category == 'particles':
            result[category] = unused.particles_deep()
        elif category == 'textures':
            result[category] = unused.textures_deep()
        elif category == 'armatures':
            result[category] = unused.armatures_deep()
        elif category == 'worlds':
            config.debug_print(f"[Atomic Debug] get_all_unused_parallel: Calling unused.worlds()...")
            result[category] = unused.worlds()
            config.debug_print(f"[Atomic Debug] get_all_unused_parallel: unused.worlds() returned {len(result[category])} unused worlds")
        config.debug_print(f"[Atomic Debug] get_all_unused_parallel: Finished {category}")
    config.debug_print(f"[Atomic Debug] get_all_unused_parallel: Complete, returning results")
    return result


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
        
        # First check: standard unused detection
        if not users.image_all(image.name):
            if not image.use_fake_user or config.include_fake_users:
                if image.name not in do_not_flag:
                    return True
        else:
            # Second check: image is used, but check if it's ONLY used by unused objects
            # This fixes issue #5: images used by unused objects should be marked as unused
            objects_using_image = []
            
            # Check materials that use the image
            for mat_name in users.image_materials(image.name):
                objects_using_image.extend(users.material_objects(mat_name))
                objects_using_image.extend(users.material_geometry_nodes(mat_name))
            
            # Check Geometry Nodes directly
            objects_using_image.extend(users.image_geometry_nodes(image.name))
            
            # Remove duplicates
            objects_using_image = list(set(objects_using_image))
            
            # If image is only used by objects, and ALL those objects are unused, mark image as unused
            if objects_using_image:
                all_objects_unused = all(not users.object_all(obj_name) for obj_name in objects_using_image)
                if all_objects_unused:
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
        
        # First check: standard unused detection
        if not users.material_all(material.name):
            if not material.use_fake_user or config.include_fake_users:
                return True
        else:
            # Second check: material is used, but check if it's ONLY used by unused objects
            # This fixes issue #5: materials used by unused objects should be marked as unused
            objects_using_material = []
            objects_using_material.extend(users.material_objects(material.name))
            objects_using_material.extend(users.material_geometry_nodes(material.name))
            
            # Remove duplicates
            objects_using_material = list(set(objects_using_material))
            
            # If material is only used by objects, and ALL those objects are unused, mark material as unused
            if objects_using_material:
                all_objects_unused = all(not users.object_all(obj_name) for obj_name in objects_using_material)
                if all_objects_unused:
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
    config.debug_print(f"[Atomic Debug] _has_any_unused_worlds: Starting, total worlds: {len(bpy.data.worlds)}")
    checked = 0
    for world in bpy.data.worlds:
        if compat.is_library_or_override(world):
            continue
        checked += 1
        config.debug_print(f"[Atomic Debug] _has_any_unused_worlds: Checking world '{world.name}' (users={world.users}, fake_user={world.use_fake_user}, include_fake_users={config.include_fake_users})")
        if world.users == 0 or (world.users == 1 and
                                world.use_fake_user and
                                config.include_fake_users):
            config.debug_print(f"[Atomic Debug] _has_any_unused_worlds: Found unused world '{world.name}', returning True")
            return True
    config.debug_print(f"[Atomic Debug] _has_any_unused_worlds: Checked {checked} worlds, none unused, returning False")
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


# Category order for progress tracking
CATEGORIES = ['collections', 'images', 'lights', 'materials', 'node_groups', 
              'objects', 'particles', 'textures', 'armatures', 'worlds']

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
    result = {}
    for category in CATEGORIES:
        if category == 'collections':
            result[category] = _has_any_unused_collections()
        elif category == 'images':
            result[category] = _has_any_unused_images()
        elif category == 'lights':
            result[category] = _has_any_unused_lights()
        elif category == 'materials':
            result[category] = _has_any_unused_materials()
        elif category == 'node_groups':
            result[category] = _has_any_unused_node_groups()
        elif category == 'objects':
            result[category] = _has_any_unused_objects()
        elif category == 'particles':
            result[category] = _has_any_unused_particles()
        elif category == 'textures':
            result[category] = _has_any_unused_textures()
        elif category == 'armatures':
            result[category] = _has_any_unused_armatures()
        elif category == 'worlds':
            result[category] = _has_any_unused_worlds()
    
    return result



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

This file contains the main operators found in the main panel of the
Atomic Data Manager interface. This includes nuke, clean, undo, and the
various selection operations.

"""

import bpy
from bpy.utils import register_class
from ..utils import compat
from ..stats import unused
from ..stats import unused_parallel
from .. import config
from .utils import clean
from .utils import nuke
from ..ui.utils import ui_layouts

# Cache for unused data-blocks to avoid recalculation
# This is invalidated when undo steps occur or after cleaning
_unused_cache = None
_cache_valid = False

# Store reference to clean operator instance for dialog invocation
_clean_operator_instance = None

# Module-level state for timer-based operations
_smart_select_state = {
    'current_category_index': 0,
    'unused_flags': {},
    'all_unused': None,
    'detected_categories': [],
    'counting_category_index': 0,  # For incremental counting in Step 2
    'counting_all_unused': {},  # For incremental counting results
    'counting_status_updated': False,  # Track if status was updated for current category
    'counting_images_list': None,  # List of images to check incrementally
    'counting_images_index': 0,  # Current image index
    'counting_images_unused': [],  # Unused images found so far
    'counting_images_executor': None,  # ThreadPoolExecutor for parallel processing
    'counting_images_futures': []  # List of futures for tracking parallel work
}

_clean_invoke_state = {
    'current_category_index': 0,
    'all_unused': None,
    'selected_categories': [],
    'found_items': {},
    'current_world_index': 0,  # For incremental world scanning
    'worlds_list': None  # Cache of worlds to scan
}

_clean_execute_state = {
    'categories_to_clean': [],
    'total_items': 0,
    'current_category_index': 0,
    'current_item_index': 0,
    'deleted_count': 0
}


def _invalidate_cache():
    """Invalidate the unused data cache."""
    global _unused_cache, _cache_valid
    _unused_cache = None
    _cache_valid = False


# Cache for expensive operations during image scanning
_image_scan_cache = {
    'image_all_results': {},  # image_name -> bool (True if used, False if unused)
    'image_materials_results': {},  # image_name -> list of material names
    'material_objects_results': {},  # material_name -> list of object names
    'object_all_results': {},  # object_name -> list of scene names (empty if unused)
}

def _clear_image_scan_cache():
    """Clear the image scan cache"""
    global _image_scan_cache
    _image_scan_cache = {
        'image_all_results': {},
        'image_materials_results': {},
        'material_objects_results': {},
        'object_all_results': {},
    }


def _check_single_image(image):
    """Check if a single image is unused. Returns True if unused, False otherwise.
    Uses caching to avoid redundant expensive scans."""
    from ..stats import users
    
    do_not_flag = ["Render Result", "Viewer Node", "D-NOISE Export"]
    
    # Skip library-linked and override datablocks
    if compat.is_library_or_override(image):
        return False
    
    # Fast early check: Use Blender's built-in users count
    # This is much faster than scanning the entire scene
    image_users = image.users
    has_fake_user = image.use_fake_user
    
    # Fast path 1: Image has no users at all → definitely unused
    if image_users == 0:
        if image.name not in do_not_flag:
            return True
        return False
    
    # Fast path 2: Only fake user and we're ignoring fake users → unused
    if image_users == 1 and has_fake_user and config.include_fake_users:
        if image.name not in do_not_flag:
            return True
        return False
    
    # Fast path 3: Only fake user and we're NOT ignoring fake users → used (skip deep check)
    if image_users == 1 and has_fake_user and not config.include_fake_users:
        return False
    
    image_name = image.name
    
    # Deep check: standard unused detection (use cache)
    if image_name not in _image_scan_cache['image_all_results']:
        # Cache the result of image_all() - this is expensive
        _image_scan_cache['image_all_results'][image_name] = bool(users.image_all(image_name))
    
    if not _image_scan_cache['image_all_results'][image_name]:
        # check if image has a fake user or if ignore fake users is enabled
        if not has_fake_user or config.include_fake_users:
            # if image is not in our do not flag list
            if image_name not in do_not_flag:
                return True
        return False
    
    # Second check: image is used, but check if it's ONLY used by unused objects
    # This fixes issue #5: images used by unused objects should be marked as unused
    # Get all objects that use this image (directly or indirectly) - use cache
    if image_name not in _image_scan_cache['image_materials_results']:
        _image_scan_cache['image_materials_results'][image_name] = users.image_materials(image_name)
    
    objects_using_image = []
    
    # Check materials that use the image (use cached result)
    for mat_name in _image_scan_cache['image_materials_results'][image_name]:
        # Get objects using this material (use cache)
        if mat_name not in _image_scan_cache['material_objects_results']:
            _image_scan_cache['material_objects_results'][mat_name] = users.material_objects(mat_name)
        objects_using_image.extend(_image_scan_cache['material_objects_results'][mat_name])
        
        # Also check Geometry Nodes usage
        objects_using_image.extend(users.material_geometry_nodes(mat_name))
    
    # Check Geometry Nodes directly
    objects_using_image.extend(users.image_geometry_nodes(image_name))
    
    # Remove duplicates
    objects_using_image = list(set(objects_using_image))
    
    # If image is only used by objects, and ALL those objects are unused, mark image as unused
    # Check each object individually to avoid recursion issues (use cache)
    if objects_using_image:
        all_objects_unused = True
        for obj_name in objects_using_image:
            if obj_name not in _image_scan_cache['object_all_results']:
                _image_scan_cache['object_all_results'][obj_name] = users.object_all(obj_name)
            if _image_scan_cache['object_all_results'][obj_name]:
                all_objects_unused = False
                break
        
        if all_objects_unused:
            # Check if image has a fake user or if ignore fake users is enabled
            if not image.use_fake_user or config.include_fake_users:
                # if image is not in our do not flag list
                if image_name not in do_not_flag:
                    return True
    
    return False


# Atomic Data Manager Clear Cache Operator
class ATOMIC_OT_clear_cache(bpy.types.Operator):
    """Clear the unused data cache"""
    bl_idname = "atomic.clear_cache"
    bl_label = "Clear Cache"
    bl_description = "Manually clear the unused data cache. This forces a fresh scan on the next Smart Select or Clean operation"

    def execute(self, context):
        _invalidate_cache()
        print("[Atomic] Cache cleared manually")
        return {'FINISHED'}


# Atomic Data Manager Cancel Operation Operator
class ATOMIC_OT_cancel_operation(bpy.types.Operator):
    """Cancel the current operation"""
    bl_idname = "atomic.cancel_operation"
    bl_label = "Cancel Operation"
    bl_description = "Cancel the currently running operation"

    def execute(self, context):
        atom = context.scene.atomic
        atom.cancel_operation = True
        return {'FINISHED'}


# Atomic Data Manager Nuke Operator
class ATOMIC_OT_nuke(bpy.types.Operator):
    """Remove all data-blocks from the selected categories"""
    bl_idname = "atomic.nuke"
    bl_label = "CAUTION!"

    def draw(self, context):
        atom = bpy.context.scene.atomic
        layout = self.layout

        col = layout.column()
        col.label(text="Remove the following data-blocks?")

        # No Data Section
        if not (atom.collections or atom.images or atom.lights or
                atom.materials or atom.node_groups or atom.particles or
                atom.textures or atom.worlds):

            ui_layouts.box_list(
                layout=layout,
            )

        # display when the main panel collections property is toggled
        if atom.collections:
            from ..utils import compat
            collections = sorted([c.name for c in bpy.data.collections 
                                 if not compat.is_library_or_override(c)])
            ui_layouts.box_list(
                layout=layout,
                title="Collections",
                items=collections,
                icon="OUTLINER_OB_GROUP_INSTANCE"
            )

        # display when the main panel images property is toggled
        if atom.images:
            from ..utils import compat
            images = sorted([i.name for i in bpy.data.images 
                            if not compat.is_library_or_override(i)])
            ui_layouts.box_list(
                layout=layout,
                title="Images",
                items=images,
                icon="IMAGE_DATA"
            )

        # display when the main panel lights property is toggled
        if atom.lights:
            from ..utils import compat
            lights = sorted([l.name for l in bpy.data.lights 
                           if not compat.is_library_or_override(l)])
            ui_layouts.box_list(
                layout=layout,
                title="Lights",
                items=lights,
                icon="OUTLINER_OB_LIGHT"
            )

        # display when the main panel materials property is toggled
        if atom.materials:
            from ..utils import compat
            materials = sorted([m.name for m in bpy.data.materials 
                               if not compat.is_library_or_override(m)])
            ui_layouts.box_list(
                layout=layout,
                title="Materials",
                items=materials,
                icon="MATERIAL"
            )

        # display when the main panel node groups property is toggled
        if atom.node_groups:
            from ..utils import compat
            node_groups = sorted([ng.name for ng in bpy.data.node_groups 
                                 if not compat.is_library_or_override(ng)])
            ui_layouts.box_list(
                layout=layout,
                title="Node Groups",
                items=node_groups,
                icon="NODETREE"
            )

        # display when the main panel particle systems property is toggled
        if atom.particles:
            from ..utils import compat
            particles = sorted([p.name for p in bpy.data.particles 
                               if not compat.is_library_or_override(p)])
            ui_layouts.box_list(
                layout=layout,
                title="Particle Systems",
                items=particles,
                icon="PARTICLES"
            )

        # display when the main panel textures property is toggled
        if atom.textures:
            from ..utils import compat
            textures = sorted([t.name for t in bpy.data.textures 
                              if not compat.is_library_or_override(t)])
            ui_layouts.box_list(
                layout=layout,
                title="Textures",
                items=textures,
                icon="TEXTURE"
            )

        # display when the main panel worlds property is toggled
        if atom.worlds:
            from ..utils import compat
            worlds = sorted([w.name for w in bpy.data.worlds 
                           if not compat.is_library_or_override(w)])
            ui_layouts.box_list(
                layout=layout,
                title="Worlds",
                items=worlds,
                icon="WORLD"
            )

        row = layout.row()  # extra spacing

    def execute(self, context):
        atom = bpy.context.scene.atomic

        if atom.collections:
            nuke.collections()

        if atom.images:
            nuke.images()

        if atom.lights:
            nuke.lights()

        if atom.materials:
            nuke.materials()

        if atom.node_groups:
            nuke.node_groups()

        if atom.particles:
            nuke.particles()

        if atom.textures:
            nuke.textures()

        if atom.worlds:
            nuke.worlds()

        bpy.ops.atomic.deselect_all()

        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)


# Atomic Data Manager Clean Operator
class ATOMIC_OT_clean(bpy.types.Operator):
    """Remove all unused data-blocks from the selected categories"""
    bl_idname = "atomic.clean"
    bl_label = "Clean"

    # Use None as sentinel to indicate "not yet calculated"
    # Empty lists [] indicate "calculated and found nothing"
    unused_collections = None
    unused_images = None
    unused_lights = None
    unused_materials = None
    unused_node_groups = None
    unused_objects = None
    unused_particles = None
    unused_textures = None
    unused_armatures = None
    unused_worlds = None

    def draw(self, context):
        atom = bpy.context.scene.atomic
        layout = self.layout

        col = layout.column()
        col.label(text="Remove the following data-blocks?")

        # display if no main panel properties are toggled
        if not (atom.collections or atom.images or atom.lights or
                atom.materials or atom.node_groups or atom.objects or
                atom.particles or atom.textures or atom.armatures or
                atom.worlds):

            ui_layouts.box_list(
                layout=layout,
            )

        # display when the main panel collections property is toggled
        if atom.collections:
            ui_layouts.box_list(
                layout=layout,
                title="Collections",
                items=self.unused_collections,
                icon="OUTLINER_OB_GROUP_INSTANCE"
            )

        # display when the main panel images property is toggled
        if atom.images:
            ui_layouts.box_list(
                layout=layout,
                title="Images",
                items=self.unused_images,
                icon="IMAGE_DATA"
            )

        # display when the main panel lights property is toggled
        if atom.lights:
            ui_layouts.box_list(
                layout=layout,
                title="Lights",
                items=self.unused_lights,
                icon="OUTLINER_OB_LIGHT"
            )

        # display when the main panel materials property is toggled
        if atom.materials:
            ui_layouts.box_list(
                layout=layout,
                title="Materials",
                items=self.unused_materials,
                icon="MATERIAL"
            )

        # display when the main panel node groups property is toggled
        if atom.node_groups:
            ui_layouts.box_list(
                layout=layout,
                title="Node Groups",
                items=self.unused_node_groups,
                icon="NODETREE"
            )

        # display when the main panel objects property is toggled
        if atom.objects:
            ui_layouts.box_list(
                layout=layout,
                title="Objects",
                items=self.unused_objects,
                icon="OBJECT_DATA"
            )

        # display when the main panel particle systems property is toggled
        if atom.particles:
            ui_layouts.box_list(
                layout=layout,
                title="Particle Systems",
                items=self.unused_particles,
                icon="PARTICLES"
            )

        # display when the main panel textures property is toggled
        if atom.textures:
            ui_layouts.box_list(
                layout=layout,
                title="Textures",
                items=self.unused_textures,
                icon="TEXTURE"
            )

        # display when the main panel armatures property is toggled
        if atom.armatures:
            ui_layouts.box_list(
                layout=layout,
                title="Armatures",
                items=self.unused_armatures,
                icon="ARMATURE_DATA"
            )

        # display when the main panel worlds property is toggled
        if atom.worlds:
            ui_layouts.box_list(
                layout=layout,
                title="Worlds",
                items=self.unused_worlds,
                icon="WORLD"
            )

        row = layout.row()  # extra spacing

    def execute(self, context):
        atom = context.scene.atomic

        # Count total items to delete
        total_items = 0
        categories_to_clean = []
        
        if atom.collections and self.unused_collections:
            total_items += len(self.unused_collections)
            categories_to_clean.append(('collections', self.unused_collections))
        if atom.images and self.unused_images:
            total_items += len(self.unused_images)
            categories_to_clean.append(('images', self.unused_images))
        if atom.lights and self.unused_lights:
            total_items += len(self.unused_lights)
            categories_to_clean.append(('lights', self.unused_lights))
        if atom.materials and self.unused_materials:
            total_items += len(self.unused_materials)
            categories_to_clean.append(('materials', self.unused_materials))
        if atom.node_groups and self.unused_node_groups:
            total_items += len(self.unused_node_groups)
            categories_to_clean.append(('node_groups', self.unused_node_groups))
        if atom.objects and self.unused_objects:
            total_items += len(self.unused_objects)
            categories_to_clean.append(('objects', self.unused_objects))
        if atom.particles and self.unused_particles:
            total_items += len(self.unused_particles)
            categories_to_clean.append(('particles', self.unused_particles))
        if atom.textures and self.unused_textures:
            total_items += len(self.unused_textures)
            categories_to_clean.append(('textures', self.unused_textures))
        if atom.armatures and self.unused_armatures:
            total_items += len(self.unused_armatures)
            categories_to_clean.append(('armatures', self.unused_armatures))
        if atom.worlds and self.unused_worlds:
            total_items += len(self.unused_worlds)
            categories_to_clean.append(('worlds', self.unused_worlds))

        if total_items == 0:
            # Nothing to delete
            bpy.ops.atomic.deselect_all()
            return {'FINISHED'}

        # Initialize progress tracking
        atom.is_operation_running = True
        atom.operation_progress = 0.0
        atom.operation_status = "Initializing deletion..."
        atom.cancel_operation = False
        
        # Initialize module-level state for timer processing
        global _clean_execute_state
        _clean_execute_state = {
            'categories_to_clean': categories_to_clean,
            'total_items': total_items,
            'current_category_index': 0,
            'current_item_index': 0,
            'deleted_count': 0
        }
        
        # Start timer for processing
        bpy.app.timers.register(_process_clean_execute_step)
        
        return {'FINISHED'}


def _process_clean_execute_step():
    """Process Clean execute (deletion) in steps to avoid blocking the UI"""
    atom = bpy.context.scene.atomic
    global _clean_execute_state
    
    # Check for cancellation
    if atom.cancel_operation:
        atom.is_operation_running = False
        atom.operation_progress = 0.0
        atom.operation_status = "Operation cancelled"
        atom.cancel_operation = False
        _clean_execute_state = None
        # Force UI update
        for area in bpy.context.screen.areas:
            area.tag_redraw()
        return None
    
    # Process categories one by one
    if _clean_execute_state['current_category_index'] < len(_clean_execute_state['categories_to_clean']):
        category, unused_list = _clean_execute_state['categories_to_clean'][_clean_execute_state['current_category_index']]
        
        if unused_list and _clean_execute_state['current_item_index'] < len(unused_list):
            # Delete current item
            item_key = unused_list[_clean_execute_state['current_item_index']]
            atom.operation_status = f"Removing {category}: {item_key}..."
            
            try:
                if category == 'collections':
                    if item_key in bpy.data.collections:
                        bpy.data.collections.remove(bpy.data.collections[item_key])
                elif category == 'images':
                    if item_key in bpy.data.images:
                        bpy.data.images.remove(bpy.data.images[item_key])
                elif category == 'lights':
                    if item_key in bpy.data.lights:
                        bpy.data.lights.remove(bpy.data.lights[item_key])
                elif category == 'materials':
                    if item_key in bpy.data.materials:
                        bpy.data.materials.remove(bpy.data.materials[item_key])
                elif category == 'node_groups':
                    if item_key in bpy.data.node_groups:
                        bpy.data.node_groups.remove(bpy.data.node_groups[item_key])
                elif category == 'objects':
                    if item_key in bpy.data.objects:
                        bpy.data.objects.remove(bpy.data.objects[item_key])
                elif category == 'particles':
                    if item_key in bpy.data.particles:
                        bpy.data.particles.remove(bpy.data.particles[item_key])
                elif category == 'textures':
                    if item_key in bpy.data.textures:
                        bpy.data.textures.remove(bpy.data.textures[item_key])
                elif category == 'armatures':
                    if item_key in bpy.data.armatures:
                        bpy.data.armatures.remove(bpy.data.armatures[item_key])
                elif category == 'worlds':
                    if item_key in bpy.data.worlds:
                        bpy.data.worlds.remove(bpy.data.worlds[item_key])
                
                _clean_execute_state['deleted_count'] += 1
            except:
                pass  # Item may have been deleted already or doesn't exist
            
            _clean_execute_state['current_item_index'] += 1
            progress = (_clean_execute_state['deleted_count'] / _clean_execute_state['total_items']) * 100.0
            atom.operation_progress = progress
            
            # Force UI update
            for area in bpy.context.screen.areas:
                area.tag_redraw()
            
            return 0.01  # Continue processing
        else:
            # Move to next category
            _clean_execute_state['current_category_index'] += 1
            _clean_execute_state['current_item_index'] = 0
            return 0.01  # Continue to next category
    
    # All items deleted
    deleted_count = _clean_execute_state['deleted_count']
    atom.is_operation_running = False
    atom.operation_progress = 100.0
    atom.operation_status = f"Complete! Removed {deleted_count} unused data-blocks"
    
    # Clear state
    _clean_execute_state = None
    
    # Invalidate cache after cleaning (data has changed)
    _invalidate_cache()
    
    # Deselect all
    bpy.ops.atomic.deselect_all()
    
    # Force UI update
    for area in bpy.context.screen.areas:
        area.tag_redraw()
    
    return None  # Stop timer

    def invoke(self, context, event):
        atom = context.scene.atomic
        
        # Check if cache is valid and we can show dialog immediately
        global _unused_cache, _cache_valid, _clean_operator_instance
        if _cache_valid and _unused_cache is not None:
            # Use cached results immediately
            all_unused = _unused_cache
            _populate_unused_lists(self, atom, all_unused)
            return context.window_manager.invoke_props_dialog(self)
        
        # Need to scan - initialize progress tracking
        atom.is_operation_running = True
        atom.operation_progress = 0.0
        atom.operation_status = "Initializing Clean scan..."
        atom.cancel_operation = False
        
        # Store operator instance for dialog invocation
        _clean_operator_instance = self
        
        # Initialize module-level state for timer processing
        global _clean_invoke_state
        _clean_invoke_state = {
            'current_category_index': 0,
            'all_unused': None,
            'selected_categories': [],
            'found_items': {},
            'operator_instance': self,
            'current_world_index': 0,
            'worlds_list': None
        }
        
        # Check which categories are selected
        if atom.collections:
            _clean_invoke_state['selected_categories'].append('collections')
        if atom.images:
            _clean_invoke_state['selected_categories'].append('images')
        if atom.lights:
            _clean_invoke_state['selected_categories'].append('lights')
        if atom.materials:
            _clean_invoke_state['selected_categories'].append('materials')
        if atom.node_groups:
            _clean_invoke_state['selected_categories'].append('node_groups')
        if atom.objects:
            _clean_invoke_state['selected_categories'].append('objects')
        if atom.particles:
            _clean_invoke_state['selected_categories'].append('particles')
        if atom.textures:
            _clean_invoke_state['selected_categories'].append('textures')
        if atom.armatures:
            _clean_invoke_state['selected_categories'].append('armatures')
        if atom.worlds:
            _clean_invoke_state['selected_categories'].append('worlds')
        
        # Start timer for processing
        bpy.app.timers.register(_process_clean_invoke_step)
        
        return {'FINISHED'}


def _populate_unused_lists(operator_instance, atom, all_unused):
    """Helper to populate unused lists from all_unused dict"""
    if atom.collections:
        operator_instance.unused_collections = all_unused.get('collections', [])
    if atom.images:
        operator_instance.unused_images = all_unused.get('images', [])
    if atom.lights:
        operator_instance.unused_lights = all_unused.get('lights', [])
    if atom.materials:
        operator_instance.unused_materials = all_unused.get('materials', [])
    if atom.node_groups:
        operator_instance.unused_node_groups = all_unused.get('node_groups', [])
    if atom.objects:
        operator_instance.unused_objects = all_unused.get('objects', [])
    if atom.particles:
        operator_instance.unused_particles = all_unused.get('particles', [])
    if atom.textures:
        operator_instance.unused_textures = all_unused.get('textures', [])
    if atom.armatures:
        operator_instance.unused_armatures = all_unused.get('armatures', [])
    if atom.worlds:
        operator_instance.unused_worlds = all_unused.get('worlds', [])


def _process_clean_invoke_step():
    """Process Clean invoke in steps to avoid blocking the UI"""
    atom = bpy.context.scene.atomic
    global _clean_invoke_state, _unused_cache, _cache_valid, _clean_operator_instance
    
    # Check for cancellation
    if atom.cancel_operation:
        atom.is_operation_running = False
        atom.operation_progress = 0.0
        atom.operation_status = "Operation cancelled"
        atom.cancel_operation = False
        _clean_invoke_state = None
        # Force UI update
        for area in bpy.context.screen.areas:
            area.tag_redraw()
        return None
    
    # Step 1: Check if cache is valid, otherwise recalculate
    if _cache_valid and _unused_cache is not None:
        _clean_invoke_state['all_unused'] = _unused_cache
        atom.operation_progress = 50.0
        atom.operation_status = "Using cached results..."
    else:
        # Scan categories one by one
        total_categories = len(unused_parallel.CATEGORIES)
        if _clean_invoke_state['current_category_index'] < total_categories:
            category = unused_parallel.CATEGORIES[_clean_invoke_state['current_category_index']]
            atom.operation_status = f"Scanning {category}..."
            
            # Get unused items for this category
            if category == 'collections':
                unused_list = unused.collections_deep()
            elif category == 'images':
                unused_list = unused.images_deep()
            elif category == 'lights':
                unused_list = unused.lights_deep()
            elif category == 'materials':
                unused_list = unused.materials_deep()
            elif category == 'node_groups':
                unused_list = unused.node_groups_deep()
            elif category == 'objects':
                unused_list = unused.objects_deep()
            elif category == 'particles':
                unused_list = unused.particles_deep()
            elif category == 'textures':
                unused_list = unused.textures_deep()
            elif category == 'armatures':
                unused_list = unused.armatures_deep()
            elif category == 'worlds':
                # Process worlds incrementally to keep UI responsive
                if _clean_invoke_state['worlds_list'] is None:
                    # First time - get list of all worlds to scan
                    from ..utils import compat
                    print(f"[Atomic Debug] Starting world scan, total worlds in bpy.data.worlds: {len(bpy.data.worlds)}")
                    _clean_invoke_state['worlds_list'] = [
                        w for w in bpy.data.worlds 
                        if not compat.is_library_or_override(w)
                    ]
                    print(f"[Atomic Debug] Filtered worlds list (excluding library/override): {len(_clean_invoke_state['worlds_list'])}")
                    _clean_invoke_state['current_world_index'] = 0
                    if _clean_invoke_state['all_unused'] is None:
                        _clean_invoke_state['all_unused'] = {}
                    _clean_invoke_state['all_unused'][category] = []
                
                # Process one world per timer callback
                worlds_list = _clean_invoke_state['worlds_list']
                if _clean_invoke_state['current_world_index'] < len(worlds_list):
                    world = worlds_list[_clean_invoke_state['current_world_index']]
                    print(f"[Atomic Debug] Checking world {_clean_invoke_state['current_world_index'] + 1}/{len(worlds_list)}: '{world.name}' (users={world.users}, fake_user={world.use_fake_user})")
                    
                    # Check if world is unused
                    is_unused = world.users == 0 or (world.users == 1 and
                                                     world.use_fake_user and
                                                     config.include_fake_users)
                    if is_unused:
                        print(f"[Atomic Debug] World '{world.name}' is unused, adding to list")
                        _clean_invoke_state['all_unused'][category].append(world.name)
                    else:
                        print(f"[Atomic Debug] World '{world.name}' is used (users={world.users})")
                    
                    _clean_invoke_state['current_world_index'] += 1
                    progress_base = (_clean_invoke_state['current_category_index'] / total_categories) * 50.0
                    world_progress = (_clean_invoke_state['current_world_index'] / len(worlds_list)) * (50.0 / total_categories)
                    atom.operation_progress = progress_base + world_progress
                    
                    # Force UI update
                    for area in bpy.context.screen.areas:
                        area.tag_redraw()
                    
                    return 0.01  # Continue processing this category
                else:
                    # Finished scanning worlds, move to next category
                    unused_list = _clean_invoke_state['all_unused'][category]
                    print(f"[Atomic Debug] Finished scanning worlds. Found {len(unused_list)} unused: {unused_list}")
                    _clean_invoke_state['worlds_list'] = None
                    _clean_invoke_state['current_world_index'] = 0
            else:
                unused_list = []
            
            if category != 'worlds':  # Worlds already handled incrementally above
                if _clean_invoke_state['all_unused'] is None:
                    _clean_invoke_state['all_unused'] = {}
                _clean_invoke_state['all_unused'][category] = unused_list
            
            if category != 'worlds':  # Only increment if we finished the category
                _clean_invoke_state['current_category_index'] += 1
                progress = (_clean_invoke_state['current_category_index'] / total_categories) * 50.0
                atom.operation_progress = progress
                
                # Force UI update
                for area in bpy.context.screen.areas:
                    area.tag_redraw()
                
                return 0.01  # Continue processing
            else:
                # Worlds category finished, move to next
                _clean_invoke_state['current_category_index'] += 1
                progress = (_clean_invoke_state['current_category_index'] / total_categories) * 50.0
                atom.operation_progress = progress
                
                # Force UI update
                for area in bpy.context.screen.areas:
                    area.tag_redraw()
                
                return 0.01  # Continue to next category
        
        # Cache the results
        _unused_cache = _clean_invoke_state['all_unused']
        _cache_valid = True
        atom.operation_progress = 50.0
        atom.operation_status = "Scan complete, processing results..."
    
    # Step 2: Populate operator properties with unused items
    operator_instance = _clean_invoke_state.get('operator_instance')
    if operator_instance:
        _populate_unused_lists(operator_instance, atom, _clean_invoke_state['all_unused'])
    
    # Calculate found items for debug
    for category in _clean_invoke_state['selected_categories']:
        unused_list = _clean_invoke_state['all_unused'].get(category, [])
        if unused_list:
            _clean_invoke_state['found_items'][category] = len(unused_list)

    # Debug output
    if _clean_invoke_state['selected_categories']:
        if _clean_invoke_state['found_items']:
            print(f"[Atomic Clean] Selected categories: {', '.join(_clean_invoke_state['selected_categories'])}")
            print(f"[Atomic Clean] Found unused items: {_clean_invoke_state['found_items']}")
        else:
            print(f"[Atomic Clean] Selected categories: {', '.join(_clean_invoke_state['selected_categories'])}")
            print(f"[Atomic Clean] WARNING: No unused items found in selected categories!")
    
    # Operation complete - show dialog
    atom.is_operation_running = False
    atom.operation_progress = 100.0
    atom.operation_status = ""
    
    # Force UI update
    for area in bpy.context.screen.areas:
        area.tag_redraw()
    
    # Use a timer to invoke the dialog
    def show_dialog():
        try:
            if _clean_operator_instance is not None:
                wm = bpy.context.window_manager
                wm.invoke_props_dialog(_clean_operator_instance)
                _clean_operator_instance = None
        except:
            pass  # Dialog may fail if context is invalid
        return None  # Run once
    
    # Clear state
    _clean_invoke_state = None
    
    bpy.app.timers.register(show_dialog, first_interval=0.1)
    
    return None  # Stop timer


# Atomic Data Manager Undo Operator
class ATOMIC_OT_undo(bpy.types.Operator):
    """Undo the previous action"""
    bl_idname = "atomic.undo"
    bl_label = "Undo"

    def execute(self, context):
        bpy.ops.ed.undo()
        # Invalidate cache after undo
        _invalidate_cache()
        return {'FINISHED'}


# Atomic Data Manager Smart Select Operator
class ATOMIC_OT_smart_select(bpy.types.Operator):
    """Auto-select categories with unused data"""
    bl_idname = "atomic.smart_select"
    bl_label = "Smart Select"

    def execute(self, context):
        atom = context.scene.atomic
        
        # Initialize progress tracking
        atom.is_operation_running = True
        atom.operation_progress = 0.0
        atom.operation_status = "Initializing Smart Select..."
        atom.cancel_operation = False
        
        # Initialize module-level state for timer processing
        global _smart_select_state
        _smart_select_state = {
            'current_category_index': 0,
            'unused_flags': {},
            'all_unused': None,
            'detected_categories': [],
            'counting_category_index': 0,
            'counting_all_unused': {},
            'counting_status_updated': False,
            'counting_images_list': None,
            'counting_images_index': 0,
            'counting_images_unused': []
        }
        
        # Start timer for processing
        bpy.app.timers.register(_process_smart_select_step)
        
        return {'FINISHED'}


def _process_smart_select_step():
    """Process Smart Select in steps to avoid blocking the UI"""
    atom = bpy.context.scene.atomic
    global _smart_select_state
    
    # Check for cancellation
    if atom.cancel_operation:
        atom.is_operation_running = False
        atom.operation_progress = 0.0
        atom.operation_status = "Operation cancelled"
        atom.cancel_operation = False
        _smart_select_state = None
        # Force UI update
        for area in bpy.context.screen.areas:
            area.tag_redraw()
        return None
    
    total_categories = len(unused_parallel.CATEGORIES)
    
    # Step 1: Check each category for unused items
    if _smart_select_state['current_category_index'] < total_categories:
        category = unused_parallel.CATEGORIES[_smart_select_state['current_category_index']]
        atom.operation_status = f"Scanning {category}..."
        
        # Check if this category has unused items
        if category == 'collections':
            _smart_select_state['unused_flags'][category] = unused_parallel._has_any_unused_collections()
        elif category == 'images':
            _smart_select_state['unused_flags'][category] = unused_parallel._has_any_unused_images()
        elif category == 'lights':
            _smart_select_state['unused_flags'][category] = unused_parallel._has_any_unused_lights()
        elif category == 'materials':
            _smart_select_state['unused_flags'][category] = unused_parallel._has_any_unused_materials()
        elif category == 'node_groups':
            _smart_select_state['unused_flags'][category] = unused_parallel._has_any_unused_node_groups()
        elif category == 'objects':
            _smart_select_state['unused_flags'][category] = unused_parallel._has_any_unused_objects()
        elif category == 'particles':
            _smart_select_state['unused_flags'][category] = unused_parallel._has_any_unused_particles()
        elif category == 'textures':
            _smart_select_state['unused_flags'][category] = unused_parallel._has_any_unused_textures()
        elif category == 'armatures':
            _smart_select_state['unused_flags'][category] = unused_parallel._has_any_unused_armatures()
        elif category == 'worlds':
            print(f"[Atomic Debug] Smart Select: Starting world scan...")
            print(f"[Atomic Debug] Smart Select: Total worlds in bpy.data.worlds: {len(bpy.data.worlds)}")
            _smart_select_state['unused_flags'][category] = unused_parallel._has_any_unused_worlds()
            print(f"[Atomic Debug] Smart Select: World scan complete, has_unused={_smart_select_state['unused_flags'][category]}")
        
        if _smart_select_state['unused_flags'][category]:
            _smart_select_state['detected_categories'].append(category)
        
        _smart_select_state['current_category_index'] += 1
        progress = (_smart_select_state['current_category_index'] / total_categories) * 50.0  # First 50% for scanning
        atom.operation_progress = progress
        
        print(f"[Atomic Debug] Smart Select: Finished {category}, index now {_smart_select_state['current_category_index']}/{total_categories}, progress={progress}%")
        
        # Force UI update
        for area in bpy.context.screen.areas:
            area.tag_redraw()
        
        return 0.01  # Continue processing
    
    # Step 2: Get full counts if categories were detected (incremental)
    if _smart_select_state['detected_categories'] and _smart_select_state['all_unused'] is None:
        # Initialize counting_all_unused dict if not initialized
        if 'counting_all_unused' not in _smart_select_state:
            print(f"[Atomic Debug] Smart Select: All categories scanned. detected_categories={_smart_select_state['detected_categories']}, all_unused={_smart_select_state['all_unused']}")
            print(f"[Atomic Debug] Smart Select: Step 2 - Starting incremental counting...")
            atom.operation_status = "Counting unused items..."
            atom.operation_progress = 55.0
            # Initialize counting_all_unused dict
            _smart_select_state['counting_all_unused'] = {}
            # Force UI update and return to let UI refresh
            for area in bpy.context.screen.areas:
                area.tag_redraw()
            return 0.01  # Return to let UI update before starting counting
        
        # Process one category at a time
        total_categories = len(unused_parallel.CATEGORIES)
        if _smart_select_state['counting_category_index'] < total_categories:
            category = unused_parallel.CATEGORIES[_smart_select_state['counting_category_index']]
            
            # Update status first, then return to let UI refresh
            if not _smart_select_state['counting_status_updated']:
                atom.operation_status = f"Counting {category}..."
                print(f"[Atomic Debug] Smart Select: Step 2 - Counting {category} ({_smart_select_state['counting_category_index'] + 1}/{total_categories})...")
                _smart_select_state['counting_status_updated'] = True
                # Force UI update and return to let it refresh
                for area in bpy.context.screen.areas:
                    area.tag_redraw()
                return 0.01  # Return to let UI update
            
            # Handle images incrementally (batch process multiple images per callback for better performance)
            if category == 'images':
                # Initialize image list if not done
                if _smart_select_state['counting_images_list'] is None:
                    _smart_select_state['counting_images_list'] = [img for img in bpy.data.images if not compat.is_library_or_override(img)]
                    _smart_select_state['counting_images_index'] = 0
                    _smart_select_state['counting_images_unused'] = []
                    _clear_image_scan_cache()  # Clear cache at start of image scanning
                    print(f"[Atomic Debug] Smart Select: Initialized image list, {len(_smart_select_state['counting_images_list'])} images to check")
                
                # Process multiple images per callback (batch size: 5 images)
                # This reduces timer overhead while keeping UI responsive
                BATCH_SIZE = 5
                total_images = len(_smart_select_state['counting_images_list'])
                
                if _smart_select_state['counting_images_index'] < total_images:
                    # Check for cancellation before processing batch
                    if atom.cancel_operation:
                        atom.is_operation_running = False
                        atom.operation_progress = 0.0
                        atom.operation_status = "Operation cancelled"
                        atom.cancel_operation = False
                        _smart_select_state = None
                        for area in bpy.context.screen.areas:
                            area.tag_redraw()
                        return None
                    
                    # Process a batch of images
                    batch_end = min(_smart_select_state['counting_images_index'] + BATCH_SIZE, total_images)
                    current_index = _smart_select_state['counting_images_index'] + 1
                    
                    # Update status with current image
                    current_image = _smart_select_state['counting_images_list'][_smart_select_state['counting_images_index']]
                    atom.operation_status = f"Checking images {current_index}-{batch_end}/{total_images}: {current_image.name[:25]}..."
                    
                    # Process batch
                    for i in range(_smart_select_state['counting_images_index'], batch_end):
                        image = _smart_select_state['counting_images_list'][i]
                        if _check_single_image(image):
                            _smart_select_state['counting_images_unused'].append(image.name)
                    
                    _smart_select_state['counting_images_index'] = batch_end
                    
                    # Update progress within images category
                    category_progress = (_smart_select_state['counting_images_index'] / total_images) * (1.0 / total_categories)
                    base_progress = 55.0 + (_smart_select_state['counting_category_index'] / total_categories) * 20.0
                    atom.operation_progress = base_progress + category_progress * 20.0
                    
                    # Force UI update
                    for area in bpy.context.screen.areas:
                        area.tag_redraw()
                    
                    return 0.01  # Continue processing images
                
                # All images processed, store result and move to next category
                _smart_select_state['counting_all_unused'][category] = _smart_select_state['counting_images_unused']
                _smart_select_state['counting_images_list'] = None
                _smart_select_state['counting_images_index'] = 0
                _smart_select_state['counting_images_unused'] = []
                print(f"[Atomic Debug] Smart Select: Finished counting images, found {len(_smart_select_state['counting_all_unused'][category])} unused")
            
            # Get unused items for other categories (these may still block, but are typically faster)
            elif category == 'collections':
                _smart_select_state['counting_all_unused'][category] = unused.collections_deep()
            elif category == 'lights':
                _smart_select_state['counting_all_unused'][category] = unused.lights_deep()
            elif category == 'materials':
                _smart_select_state['counting_all_unused'][category] = unused.materials_deep()
            elif category == 'node_groups':
                _smart_select_state['counting_all_unused'][category] = unused.node_groups_deep()
            elif category == 'objects':
                _smart_select_state['counting_all_unused'][category] = unused.objects_deep()
            elif category == 'particles':
                _smart_select_state['counting_all_unused'][category] = unused.particles_deep()
            elif category == 'textures':
                _smart_select_state['counting_all_unused'][category] = unused.textures_deep()
            elif category == 'armatures':
                _smart_select_state['counting_all_unused'][category] = unused.armatures_deep()
            elif category == 'worlds':
                _smart_select_state['counting_all_unused'][category] = unused.worlds()
            
            # Move to next category
            _smart_select_state['counting_category_index'] += 1
            _smart_select_state['counting_status_updated'] = False  # Reset for next category
            progress = 55.0 + (_smart_select_state['counting_category_index'] / total_categories) * 20.0
            atom.operation_progress = progress
            
            # Force UI update after counting
            for area in bpy.context.screen.areas:
                area.tag_redraw()
            
            return 0.01  # Continue counting
        
        # Counting complete
        _smart_select_state['all_unused'] = _smart_select_state['counting_all_unused']
        _smart_select_state['counting_all_unused'] = {}
        _smart_select_state['counting_category_index'] = 0
        print(f"[Atomic Debug] Smart Select: Step 2 - Counting complete")
        
        # Debug output
        print(f"[Atomic Smart Select] Detected unused items in: {', '.join(_smart_select_state['detected_categories'])}")
        for category in _smart_select_state['detected_categories']:
            count = len(_smart_select_state['all_unused'].get(category, []))
            if count > 0:
                print(f"  - {category}: {count} unused items")
        
        atom.operation_progress = 75.0
        
        # Force UI update
        for area in bpy.context.screen.areas:
            area.tag_redraw()
        
        return 0.01  # Continue to cache step
    
    # Step 3: Populate cache if needed
    global _unused_cache, _cache_valid
    if not _cache_valid or _unused_cache is None:
        if _smart_select_state['all_unused'] is None:
            atom.operation_status = "Caching results..."
            _smart_select_state['all_unused'] = unused_parallel.get_all_unused_parallel()
        _unused_cache = _smart_select_state['all_unused']
        _cache_valid = True
        atom.operation_progress = 90.0
        
        # Force UI update
        for area in bpy.context.screen.areas:
            area.tag_redraw()
        
        return 0.01  # Continue to final step
    
    # Step 4: Update selection and finish
    atom.operation_status = "Updating selection..."
    atom.collections = _smart_select_state['unused_flags'].get('collections', False)
    atom.images = _smart_select_state['unused_flags'].get('images', False)
    atom.lights = _smart_select_state['unused_flags'].get('lights', False)
    atom.materials = _smart_select_state['unused_flags'].get('materials', False)
    atom.node_groups = _smart_select_state['unused_flags'].get('node_groups', False)
    atom.objects = _smart_select_state['unused_flags'].get('objects', False)
    atom.particles = _smart_select_state['unused_flags'].get('particles', False)
    atom.textures = _smart_select_state['unused_flags'].get('textures', False)
    atom.armatures = _smart_select_state['unused_flags'].get('armatures', False)
    atom.worlds = _smart_select_state['unused_flags'].get('worlds', False)
    
    # Operation complete
    atom.is_operation_running = False
    atom.operation_progress = 100.0
    atom.operation_status = f"Complete! Found unused items in {len(_smart_select_state['detected_categories'])} categories"
    
    # Clear state
    _smart_select_state = None
    
    # Force UI update
    for area in bpy.context.screen.areas:
        area.tag_redraw()
    
    return None  # Stop timer


# Atomic Data Manager Select All Operator
class ATOMIC_OT_select_all(bpy.types.Operator):
    """Select all categories"""
    bl_idname = "atomic.select_all"
    bl_label = "Select All"

    def execute(self, context):
        bpy.context.scene.atomic.collections = True
        bpy.context.scene.atomic.images = True
        bpy.context.scene.atomic.lights = True
        bpy.context.scene.atomic.materials = True
        bpy.context.scene.atomic.node_groups = True
        bpy.context.scene.atomic.objects = True
        bpy.context.scene.atomic.particles = True
        bpy.context.scene.atomic.textures = True
        bpy.context.scene.atomic.armatures = True
        bpy.context.scene.atomic.worlds = True
        return {'FINISHED'}


# Atomic Data Manager Deselect All Operator
class ATOMIC_OT_deselect_all(bpy.types.Operator):
    """Deselect all categories"""
    bl_idname = "atomic.deselect_all"
    bl_label = "Deselect All"

    def execute(self, context):
        bpy.context.scene.atomic.collections = False
        bpy.context.scene.atomic.images = False
        bpy.context.scene.atomic.lights = False
        bpy.context.scene.atomic.materials = False
        bpy.context.scene.atomic.node_groups = False
        bpy.context.scene.atomic.objects = False
        bpy.context.scene.atomic.particles = False
        bpy.context.scene.atomic.textures = False
        bpy.context.scene.atomic.armatures = False
        bpy.context.scene.atomic.worlds = False

        return {'FINISHED'}


reg_list = [
    ATOMIC_OT_clear_cache,
    ATOMIC_OT_cancel_operation,
    ATOMIC_OT_nuke,
    ATOMIC_OT_clean,
    ATOMIC_OT_undo,
    ATOMIC_OT_smart_select,
    ATOMIC_OT_select_all,
    ATOMIC_OT_deselect_all
]


def register():
    for item in reg_list:
        register_class(item)


def unregister():
    for item in reg_list:
        compat.safe_unregister_class(item)

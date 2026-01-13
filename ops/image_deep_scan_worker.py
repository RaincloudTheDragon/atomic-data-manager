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

Standalone script for headless Blender instances to perform image deep scanning.
This script is executed by worker Blender processes launched from the main instance.

Usage:
    blender.exe -b blendfile.blend --python image_deep_scan_worker.py -- job_index image_list_json output_json

"""

import bpy
import sys
import json
import os

# Parse command-line arguments
# Format: -- job_index image_list_json output_json
if '--' not in sys.argv:
    print("[Atomic Error] Missing '--' separator in arguments")
    sys.exit(1)

arg_index = sys.argv.index('--')
if len(sys.argv) < arg_index + 4:
    print("[Atomic Error] Missing required arguments")
    sys.exit(1)

job_index = int(sys.argv[arg_index + 1])
image_list_json_path = sys.argv[arg_index + 2]
output_json_path = sys.argv[arg_index + 3]

try:
    # Load image list from JSON
    with open(image_list_json_path, 'r', encoding='utf-8') as f:
        image_list_data = json.load(f)
    
    image_names = image_list_data.get('images', [])
    blend_file_path = image_list_data.get('blend_file', '')
    
    # Import necessary modules
    # We need to find the addon path - try multiple methods
    addon_dir = None
    
    # Method 1: Same directory as this script (standard installation)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    potential_addon_dir = os.path.dirname(script_dir)  # Go up one level from ops/
    if os.path.exists(os.path.join(potential_addon_dir, "__init__.py")):
        addon_dir = potential_addon_dir
    
    # Method 2: Check if addon is already loaded in sys.modules
    if not addon_dir:
        for module_name in sys.modules:
            if 'atomic_data_manager' in module_name and not module_name.startswith('bl_ext'):
                module = sys.modules[module_name]
                if hasattr(module, '__file__') and module.__file__:
                    potential_dir = os.path.dirname(module.__file__)
                    if os.path.exists(os.path.join(potential_dir, "stats")):
                        addon_dir = potential_dir
                        break
    
    # Method 3: Search sys.modules for loaded addon
    if not addon_dir:
        for module_name, module in sys.modules.items():
            if 'atomic_data_manager' in module_name and hasattr(module, '__file__') and module.__file__:
                potential_dir = os.path.dirname(module.__file__)
                if os.path.exists(os.path.join(potential_dir, "stats")):
                    addon_dir = potential_dir
                    break
    
    if not addon_dir:
        print("[Atomic Error] Could not find atomic_data_manager addon directory")
        sys.exit(1)
    
    # Add addon directory to path if not already there
    if addon_dir not in sys.path:
        sys.path.insert(0, addon_dir)
    
    # Import addon modules
    try:
        from stats import users
        from utils import compat
        import config
    except ImportError as e:
        print(f"[Atomic Error] Failed to import addon modules: {e}")
        print(f"[Atomic Debug] Addon directory: {addon_dir}")
        print(f"[Atomic Debug] sys.path: {sys.path[:5]}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # Initialize config (worker instances need this)
    # Try to get preferences, but use defaults if unavailable
    config.include_fake_users = False  # Default
    try:
        # Try multiple ways to get the addon preferences
        addon_name = 'atomic_data_manager'
        prefs = bpy.context.preferences.addons.get(addon_name)
        if prefs and hasattr(prefs, 'preferences'):
            config.include_fake_users = getattr(prefs.preferences, 'include_fake_users', False)
        else:
            # Try iterating through addons
            for addon in bpy.context.preferences.addons.values():
                if hasattr(addon, 'preferences') and hasattr(addon.preferences, 'include_fake_users'):
                    config.include_fake_users = addon.preferences.include_fake_users
                    break
    except Exception as e:
        print(f"[Atomic Debug] Could not load preferences, using defaults: {e}")
        pass  # Use defaults
    
    # Process images
    unused_images = []
    image_scan_cache = {
        'image_all_results': {},
        'image_materials_results': {},
        'material_objects_results': {},
        'object_all_results': {},
    }
    
    do_not_flag = ["Render Result", "Viewer Node", "D-NOISE Export"]
    
    for image_name in image_names:
        if image_name not in bpy.data.images:
            print(f"[Atomic Warning] Image '{image_name}' not found in blend file")
            continue
        
        image = bpy.data.images[image_name]
        
        # Skip library-linked and override datablocks
        if compat.is_library_or_override(image):
            continue
        
        # Fast early check: Use Blender's built-in users count
        image_users = image.users
        has_fake_user = image.use_fake_user
        
        # Fast path 1: Image has no users at all → definitely unused
        if image_users == 0:
            if image_name not in do_not_flag:
                unused_images.append(image_name)
                continue
        
        # Fast path 2: Only fake user and we're ignoring fake users → unused
        if image_users == 1 and has_fake_user and config.include_fake_users:
            if image_name not in do_not_flag:
                unused_images.append(image_name)
                continue
        
        # Fast path 3: Only fake user and we're NOT ignoring fake users → used (skip deep check)
        if image_users == 1 and has_fake_user and not config.include_fake_users:
            continue
        
        # Deep check: standard unused detection (use cache)
        if image_name not in image_scan_cache['image_all_results']:
            image_scan_cache['image_all_results'][image_name] = bool(users.image_all(image_name))
        
        if not image_scan_cache['image_all_results'][image_name]:
            # check if image has a fake user or if ignore fake users is enabled
            if not has_fake_user or config.include_fake_users:
                if image_name not in do_not_flag:
                    unused_images.append(image_name)
                    continue
        
        # Second check: image is used, but check if it's ONLY used by unused objects
        if image_name not in image_scan_cache['image_materials_results']:
            image_scan_cache['image_materials_results'][image_name] = users.image_materials(image_name)
        
        objects_using_image = []
        
        # Check materials that use the image
        for mat_name in image_scan_cache['image_materials_results'][image_name]:
            if mat_name not in image_scan_cache['material_objects_results']:
                image_scan_cache['material_objects_results'][mat_name] = users.material_objects(mat_name)
            objects_using_image.extend(image_scan_cache['material_objects_results'][mat_name])
            
            # Also check Geometry Nodes usage
            objects_using_image.extend(users.material_geometry_nodes(mat_name))
        
        # Check Geometry Nodes directly
        objects_using_image.extend(users.image_geometry_nodes(image_name))
        
        # Remove duplicates
        objects_using_image = list(set(objects_using_image))
        
        # If image is only used by objects, and ALL those objects are unused, mark image as unused
        if objects_using_image:
            all_objects_unused = True
            for obj_name in objects_using_image:
                if obj_name not in image_scan_cache['object_all_results']:
                    image_scan_cache['object_all_results'][obj_name] = users.object_all(obj_name)
                if image_scan_cache['object_all_results'][obj_name]:
                    all_objects_unused = False
                    break
            
            if all_objects_unused:
                if not image.use_fake_user or config.include_fake_users:
                    if image_name not in do_not_flag:
                        unused_images.append(image_name)
    
    # Write results to JSON
    result_data = {
        'job_index': job_index,
        'blend_file': blend_file_path,
        'unused_images': unused_images,
        'image_scan_cache': image_scan_cache,
        'success': True
    }
    
    # Atomic write
    temp_path = output_json_path + '.tmp'
    with open(temp_path, 'w', encoding='utf-8') as f:
        json.dump(result_data, f, indent=2)
    
    # Rename temp to final
    if os.path.exists(output_json_path):
        os.remove(output_json_path)
    os.rename(temp_path, output_json_path)
    
    print(f"[Atomic Worker] Job {job_index} completed: {len(unused_images)} unused images found")
    sys.exit(0)

except Exception as e:
    print(f"[Atomic Error] Worker job {job_index} failed: {e}")
    import traceback
    traceback.print_exc()
    
    # Write error result
    try:
        error_data = {
            'job_index': job_index,
            'success': False,
            'error': str(e)
        }
        temp_path = output_json_path + '.tmp'
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(error_data, f, indent=2)
        if os.path.exists(output_json_path):
            os.remove(output_json_path)
        os.rename(temp_path, output_json_path)
    except:
        pass
    
    sys.exit(1)

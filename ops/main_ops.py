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
import os
import json
import tempfile
import glob
import time
import re
import subprocess
import math
from bpy.utils import register_class
from ..utils import compat
from ..stats import unused
from ..stats import unused_parallel
from .. import config
from .utils import clean
from .utils import nuke
from .utils import safe_delete
from ..ui.utils import ui_layouts


def _safe_set_atom_property(atom, prop_name, value):
    """
    Safely set an atom property, catching errors when Blender is in read-only state.
    
    Args:
        atom: The atomic property group instance
        prop_name: Name of the property to set
        value: Value to set
    
    Returns:
        bool: True if successful, False if failed (read-only context)
    """
    if atom is None:
        return False
    try:
        setattr(atom, prop_name, value)
        return True
    except (AttributeError, RuntimeError) as e:
        # Blender is in read-only state (e.g., during file loading, drawing/rendering)
        # AttributeError: Writing to ID classes in this context is not allowed
        # RuntimeError: cannot modify blend data in this state
        config.debug_print(f"[Atomic Debug] Cannot set {prop_name} in read-only context: {e}")
        return False
    except Exception as e:
        # Catch any other unexpected errors
        config.debug_print(f"[Atomic Debug] Unexpected error setting {prop_name}: {e}")
        return False


# Cache for unused data-blocks to avoid recalculation
# This is invalidated when undo steps occur or after cleaning
_unused_cache = None
_cache_valid = False

# Store reference to clean operator instance for dialog invocation
_clean_operator_instance = None

# Store scan results for dialog invocation (when operator instance is invalidated)
_clean_pending_results = None
_clean_pending_categories = None

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
    'worlds_list': None,  # Cache of worlds to scan
    'status_updated': False  # Track if status was updated for current category
}

_clean_execute_state = {
    'categories_to_clean': [],
    'total_items': 0,
    'current_category_index': 0,
    'current_item_index': 0,
    'deleted_count': 0,
    'safe_clean_active': False,
}

# Unified scanning state for both Smart Select and Clean
_scan_state = {
    'mode': None,  # 'quick' or 'full'
    'categories_to_scan': [],  # List of categories to scan
    'current_category_index': 0,
    'results': {},  # Quick scan: {category: bool}, Full scan: {category: [items]}
    'status_updated': False,
    'callback': None,  # Function to call when scan completes
    'callback_data': {},  # Data to pass to callback
    'graph_build_state': None,  # Incremental RNA dump / graph build
    'ng_analysis_state': None,  # Batched node_groups cleanability pass
    'material_session_ready': False,  # Material fallback indices for current category
    'mat_session_build_state': None,  # Incremental material session build
    'mat_analysis_state': None,  # Batched materials unused pass
    'cat_analysis_state': None,  # Batched generic graph category pass (actions, objects, ...)
    'progress_start': 0.0,
    'progress_end': 100.0,
}


# Smart Select: quick scan uses the first slice; full scan continues to completion.
SMART_SELECT_QUICK_SCAN_PROGRESS_END = 30.0
SCAN_PROGRESS_FINISH = 98.0  # scan phase tops out here; callbacks set 100%

# Progress budget: reference graph build, then per-category scanning.
REFERENCE_GRAPH_PROGRESS_SLICE = 0.18
CATEGORY_PROGRESS_SLICE = 1.0 - REFERENCE_GRAPH_PROGRESS_SLICE

# Share of a category's progress slice used while building material fallback indices.
MATERIAL_SESSION_PROGRESS_SLICE = 0.55


def _format_elapsed_mmss(elapsed_seconds):
    """Format elapsed wall time as M:SS (or MM:SS for longer runs)."""
    total = max(0, int(round(elapsed_seconds)))
    minutes, seconds = divmod(total, 60)
    return f"{minutes}:{seconds:02d}"


def _analysis_done_status(scan_state, suffix=None, started_at=None):
    """Build 'Analysis done in M:SS' from scan_state.started_at (monotonic clock)."""
    if started_at is None and scan_state:
        started_at = scan_state.get('started_at')
    if started_at is None:
        message = "Analysis done"
    else:
        elapsed = time.monotonic() - started_at
        message = f"Analysis done in {_format_elapsed_mmss(elapsed)}"
    if suffix:
        message = f"{message} — {suffix}"
    return message


def _finish_scan_operation(atom, progress=100.0):
    """Hide the progress UI after a scan completes."""
    _safe_set_atom_property(atom, 'is_operation_running', False)
    _safe_set_atom_property(atom, 'operation_progress', progress)
    _safe_set_atom_property(atom, 'operation_status', "")


def _report_scan_analysis_info(scan_state, suffix=None):
    """
    Post scan elapsed time as a normal Blender INFO report (blue status banner).

    Deferred with a full window/area override so Operator.report() actually shows
    from timer/callback stacks (plain status_text_set is the wrong UI).
    """
    message = _analysis_done_status(scan_state, suffix)
    config.debug_print(f"[Atomic Debug] {message}")

    def _post_report():
        try:
            # Clear any leftover workspace status text from older builds.
            workspace = bpy.context.workspace
            if workspace is not None:
                workspace.status_text_set(None)

            windows = bpy.context.window_manager.windows
            if not windows:
                print(f"Info: {message}")
                return None

            window = windows[0]
            screen = window.screen
            area = screen.areas[0] if screen.areas else None
            region = None
            if area is not None:
                for candidate in area.regions:
                    if candidate.type == 'WINDOW':
                        region = candidate
                        break

            override = {'window': window, 'screen': screen}
            if area is not None:
                override['area'] = area
            if region is not None:
                override['region'] = region

            with bpy.context.temp_override(**override):
                bpy.ops.atomic.report_info('INVOKE_DEFAULT', message=message)
        except (RuntimeError, AttributeError, TypeError) as err:
            config.debug_print(f"[Atomic Debug] Could not post Info report: {err}")
            print(f"Info: {message}")
        return None

    bpy.app.timers.register(_post_report, first_interval=0.05)


def _reference_graph_scan_progress(atom, scan_state, build_state, step_label=None):
    """
    Update progress during incremental RNA reference graph build.

    Uses a display counter so the bar advances each timer tick even when individual
    dump steps are a small fraction of the overall scan (quick scan, many types).
    """
    progress_start = float(scan_state.get('progress_start', 0.0))
    progress_end = float(scan_state.get('progress_end', 100.0))
    progress_span = max(progress_end - progress_start, 0.0)
    graph_slice = REFERENCE_GRAPH_PROGRESS_SLICE

    from ..stats import rna_analysis

    actual_sub = rna_analysis.reference_graph_build_fraction(build_state)
    display_sub = scan_state.get('_ref_graph_display_sub', 0.0)
    tick_step = 0.05  # ~5% of the graph slice per active tick
    display_sub = min(1.0, max(actual_sub, display_sub + tick_step))
    scan_state['_ref_graph_display_sub'] = display_sub

    frac = display_sub * graph_slice
    progress = progress_start + frac * progress_span
    progress = min(progress, progress_end - 0.5)

    try:
        current = float(atom.operation_progress)
        progress = max(current, progress)
    except (AttributeError, TypeError, ValueError):
        pass

    phase = build_state.get('phase', 'dump')
    type_index = build_state.get('type_index', 0)
    type_total = len(build_state.get('type_names', []))
    if phase == 'dump' and step_label:
        status = (
            f"Building reference graph: {step_label} "
            f"({type_index}/{type_total})..."
        )
    elif phase == 'finalize':
        status = "Building reference graph: resolving references..."
    elif phase == 'graph':
        status = "Building reference graph: linking dependencies..."
    else:
        label = step_label or 'graph'
        status = f"Building reference graph: {label}..."

    _safe_set_atom_property(atom, 'operation_status', status)
    _safe_set_atom_property(atom, 'operation_progress', progress)


def _material_session_scan_progress(
    atom, scan_state, session_frac, done_objs, total_objs, phase_name, phase_key=None
):
    """
    Update progress during incremental material session build.

    Uses a dedicated display counter so the bar moves each timer tick even when
    session_frac steps are tiny (quick scan, many categories, large object counts).

    Session progress is split: material slots 0–50%, geometry nodes 50–100%.
    """
    progress_start = float(scan_state.get('progress_start', 0.0))
    progress_end = float(scan_state.get('progress_end', 100.0))
    progress_span = max(progress_end - progress_start, 0.0)
    total_categories = max(len(scan_state.get('categories_to_scan', [])), 1)
    cat_idx = scan_state.get('current_category_index', 0)
    category_span = CATEGORY_PROGRESS_SLICE / total_categories
    session_slice = MATERIAL_SESSION_PROGRESS_SLICE

    actual_sub = session_frac * session_slice
    last_phase = scan_state.get('_mat_session_phase')
    if last_phase == 'slots' and phase_key == 'gn':
        # Slots used 0–50% of session_frac; sync display when gn phase begins.
        scan_state['_mat_session_display_sub'] = actual_sub
    if phase_key is not None:
        scan_state['_mat_session_phase'] = phase_key

    display_sub = scan_state.get('_mat_session_display_sub', 0.0)
    # GN batches are heavier — nudge the bar a bit more per tick in that phase.
    tick_step = 0.06 if phase_key == 'gn' else 0.04
    display_sub = max(actual_sub, display_sub + tick_step)
    # Stay within one tick of actual work so the bar does not pin early.
    display_sub = min(session_slice, display_sub, actual_sub + tick_step)
    scan_state['_mat_session_display_sub'] = display_sub

    frac = REFERENCE_GRAPH_PROGRESS_SLICE + cat_idx * category_span + display_sub * category_span
    progress = progress_start + min(frac, 1.0) * progress_span
    progress = min(progress, progress_end - 0.5)

    try:
        current = float(atom.operation_progress)
        progress = max(current, progress)
    except (AttributeError, TypeError, ValueError):
        pass

    status = (
        f"Building usage indices ({done_objs}/{total_objs}, {phase_name})..."
    )
    _safe_set_atom_property(atom, 'operation_status', status)
    _safe_set_atom_property(atom, 'operation_progress', progress)


def _unified_scan_progress(atom, scan_state, sub_fraction=0.0, status_text=None):
    """
    Map scan sub-steps to operation_progress within scan_state's progress range.

    scan_state may set progress_start / progress_end (defaults 0–100).
    Reference graph build uses _reference_graph_scan_progress(); categories use
    the remaining slice. Progress never moves backward within a scan phase.
    """
    if status_text:
        _safe_set_atom_property(atom, 'operation_status', status_text)

    progress_start = float(scan_state.get('progress_start', 0.0))
    progress_end = float(scan_state.get('progress_end', 100.0))
    progress_span = max(progress_end - progress_start, 0.0)

    total_categories = max(len(scan_state.get('categories_to_scan', [])), 1)
    cat_idx = scan_state.get('current_category_index', 0)
    category_span = CATEGORY_PROGRESS_SLICE / total_categories
    if sub_fraction == 0.0:
        scan_state['_display_sub_fraction'] = 0.0
    else:
        display_sub = scan_state.get('_display_sub_fraction', 0.0)
        if sub_fraction > display_sub:
            # Each active tick advances at least ~1.5% of the category slice so
            # the PERCENTAGE bar moves on heavy files with many small batches.
            min_step = 0.015
            delta = sub_fraction - display_sub
            display_sub = min(sub_fraction, display_sub + max(min_step, delta))
            scan_state['_display_sub_fraction'] = display_sub
        sub_fraction = scan_state.get('_display_sub_fraction', sub_fraction)
    frac = REFERENCE_GRAPH_PROGRESS_SLICE + cat_idx * category_span + sub_fraction * category_span
    frac = min(frac, 1.0)

    progress = progress_start + frac * progress_span
    progress = min(progress, progress_end - 0.5)

    try:
        current = float(atom.operation_progress)
        progress = max(current, progress)
    except (AttributeError, TypeError, ValueError):
        pass

    _safe_set_atom_property(atom, 'operation_progress', progress)


def _unified_scan_redraw():
    if bpy.context.screen:
        for area in bpy.context.screen.areas:
            area.tag_redraw()


_DEBUG_LIST_SAMPLE = 3


def _debug_summarize_list(items, sample=_DEBUG_LIST_SAMPLE):
    """Truncate long lists for debug output."""
    if not isinstance(items, list):
        return items
    if len(items) <= sample:
        return items
    return items[:sample] + [f"... +{len(items) - sample} more"]


def _debug_summarize_results(results, sample=_DEBUG_LIST_SAMPLE):
    """Summarize scan results without dumping every unused item name."""
    if results is None:
        return None
    summary = {}
    for category, value in results.items():
        if isinstance(value, bool):
            summary[category] = value
        elif isinstance(value, list):
            entry = {'count': len(value)}
            if value:
                entry['sample'] = _debug_summarize_list(value, sample)
            summary[category] = entry
        else:
            summary[category] = value
    return summary


def _debug_summarize_graph_indices(indices):
    """Counts-only summary of node-group graph indices (not full adjacency maps)."""
    if not indices:
        return None
    return {
        'compositor_ngs': len(indices.get('compositor_ngs', ())),
        'in_scene_objects': len(indices.get('in_scene_objects', ())),
        'ng_to_materials': len(indices.get('ng_to_materials', {})),
        'ng_to_objects': len(indices.get('ng_to_objects', {})),
        'ng_parents': len(indices.get('ng_parents', {})),
        'mat_objects': len(indices.get('mat_objects', {})),
        'mat_gn_objects': len(indices.get('mat_gn_objects', {})),
        'referenced_by_count': len(indices.get('referenced_by_count', {})),
    }


def _debug_summarize_ng_analysis_state(ng_state):
    if not ng_state:
        return None
    index_build = ng_state.get('index_build_state') or {}
    obj_total = len(index_build.get('object_names', []))
    obj_done = index_build.get('obj_index', 0)
    return {
        'checked': ng_state.get('index'),
        'total': len(ng_state.get('names', [])),
        'unused_so_far': len(ng_state.get('unused', [])),
        'indices_ready': ng_state.get('indices') is not None,
        'scene_objects': f"{obj_done}/{obj_total}",
        'indices': _debug_summarize_graph_indices(ng_state.get('indices')),
    }


def _debug_summarize_scan_state(scan_state):
    """Compact scan state for debug logs (avoids huge results/index dict dumps)."""
    if not scan_state:
        return None
    graph_build = scan_state.get('graph_build_state')
    mat_state = scan_state.get('mat_analysis_state')
    return {
        'mode': scan_state.get('mode'),
        'current_category_index': scan_state.get('current_category_index'),
        'categories_to_scan': scan_state.get('categories_to_scan'),
        'status_updated': scan_state.get('status_updated'),
        'results': _debug_summarize_results(scan_state.get('results')),
        'graph_build': (
            {
                'phase': graph_build.get('phase'),
                'type_index': graph_build.get('type_index'),
                'type_total': len(graph_build.get('type_names', [])),
            }
            if graph_build else None
        ),
        'ng_analysis': _debug_summarize_ng_analysis_state(
            scan_state.get('ng_analysis_state')
        ),
        'material_session_ready': scan_state.get('material_session_ready'),
        'mat_session_build': (
            {
                'phase': mat_build.get('phase'),
                'obj_index': mat_build.get('obj_index'),
                'gn_obj_index': mat_build.get('gn_obj_index'),
                'total_objects': len(mat_build.get('object_names', [])),
            }
            if (mat_build := scan_state.get('mat_session_build_state')) else None
        ),
        'mat_analysis': (
            {
                'checked': mat_state.get('index'),
                'total': len(mat_state.get('names', [])),
                'unused_so_far': len(mat_state.get('unused', [])),
            }
            if mat_state else None
        ),
        'cat_analysis': (
            {
                'category': cat_state.get('category'),
                'checked': cat_state.get('index'),
                'total': len(cat_state.get('names', [])),
                'unused_so_far': len(cat_state.get('unused', [])),
                'used_ready': cat_state.get('used') is not None,
            }
            if (cat_state := scan_state.get('cat_analysis_state')) else None
        ),
    }


def _cleanup_old_job_files():
    """Clean up old temporary job files from previous deep scan runs."""
    temp_dir = tempfile.gettempdir()
    patterns = [
        'atomic_job_*_images.json',
        'atomic_job_*_result.json',
        'atomic_job_*_result.json.tmp',
        'atomic_job_*_stdout.log',
        'atomic_job_*_stderr.log',
        'atomic_job_*_launcher.bat'
    ]
    cleaned_count = 0
    for pattern in patterns:
        for file_path in glob.glob(os.path.join(temp_dir, pattern)):
            try:
                os.remove(file_path)
                cleaned_count += 1
            except Exception as e:
                config.debug_print(f"[Atomic Debug] Could not remove {file_path}: {e}")
    if cleaned_count > 0:
        config.debug_print(f"[Atomic Debug] Cleaned up {cleaned_count} old job files")


def _invalidate_cache():
    """Invalidate the unused data cache."""
    global _unused_cache, _cache_valid
    _unused_cache = None
    _cache_valid = False
    # Clear RNA graph cache if it exists
    if hasattr(_process_unified_scan_step, '_rna_graph'):
        delattr(_process_unified_scan_step, '_rna_graph')
    if hasattr(_process_unified_scan_step, '_rna_data'):
        delattr(_process_unified_scan_step, '_rna_data')
    from ..stats import rna_analysis
    rna_analysis.clear_graph_used_cache()
    # Optionally clear disk cache on invalidation
    # (We keep it for now to allow cache reuse across sessions)


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


def _get_cache_filepath():
    """Get the cache file path based on the current blend file name"""
    if not bpy.data.filepath:
        return None
    
    # Get blend filename and make it filesystem-safe
    blend_path = bpy.data.filepath
    blend_filename = os.path.basename(blend_path)
    # Remove extension and make safe for filename
    blend_name = os.path.splitext(blend_filename)[0]
    # Replace invalid filename characters
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', blend_name)
    
    # Create cache filename
    cache_filename = f"atomic_cache_{safe_name}.json"
    cache_path = os.path.join(tempfile.gettempdir(), cache_filename)
    
    return cache_path


def _load_cache_from_disk():
    """Load cache from JSON file if it exists and is valid"""
    cache_path = _get_cache_filepath()
    if not cache_path or not os.path.exists(cache_path):
        return None
    
    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)
        
        # Validate cache
        if cache_data.get('blend_file') != bpy.data.filepath:
            config.debug_print("[Atomic Debug] Cache file is for different blend file, ignoring")
            return None
        
        # Check cache version
        if cache_data.get('cache_version') != '1.0':
            config.debug_print("[Atomic Debug] Cache version mismatch, ignoring")
            return None
        
        # Optionally check cache age (e.g., invalidate if > 1 hour old)
        timestamp = cache_data.get('timestamp', 0)
        if timestamp and (time.time() - timestamp) > 3600:  # 1 hour
            config.debug_print("[Atomic Debug] Cache is too old, ignoring")
            return None
        
        return cache_data
    except (json.JSONDecodeError, IOError, OSError) as e:
        config.debug_print(f"[Atomic Error] Failed to load cache: {e}")
        return None


def _save_cache_to_disk(results, image_scan_cache):
    """Save cache to JSON file"""
    cache_path = _get_cache_filepath()
    if not cache_path:
        return False
    
    try:
        cache_data = {
            'blend_file': bpy.data.filepath,
            'timestamp': time.time(),
            'cache_version': '1.0',
            'results': results,
            'image_scan_cache': image_scan_cache
        }
        
        # Atomic write: write to temp file first, then rename
        temp_path = cache_path + '.tmp'
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, indent=2)
        
        # Rename temp file to final file (atomic on most filesystems)
        try:
            if os.path.exists(cache_path):
                os.remove(cache_path)
            os.rename(temp_path, cache_path)
        except (OSError, IOError) as e:
            # If rename fails, try to clean up temp file
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except:
                pass
            config.debug_print(f"[Atomic Error] Failed to save cache: {e}")
            return False
        
        config.debug_print(f"[Atomic Debug] Cache saved to {cache_path}")
        return True
    except (IOError, OSError, TypeError) as e:
        config.debug_print(f"[Atomic Error] Failed to save cache: {e}")
        return False


# Worker process system removed - now using RNA-based analysis


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
        _cleanup_old_job_files()
        config.debug_print("[Atomic Debug] Cache cleared manually, old job files cleaned up")
        return {'FINISHED'}


# Atomic Data Manager Report Info Operator
class ATOMIC_OT_report_info(bpy.types.Operator):
    """Show a normal Blender INFO report (blue report banner) from timer paths."""
    bl_idname = "atomic.report_info"
    bl_label = "Report Info"
    # No REGISTER — avoids bpy.ops spam in Scripting > Info.
    bl_options = {'INTERNAL'}

    message: bpy.props.StringProperty()
    _timer = None

    def invoke(self, context, event):
        # Modal + timer so the report banner is shown (direct execute from
        # app timers often only prints to the system console).
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.0, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        try:
            self.report({'INFO'}, self.message)
        finally:
            if self._timer is not None:
                context.window_manager.event_timer_remove(self._timer)
                self._timer = None
        return {'FINISHED'}

    def execute(self, context):
        self.report({'INFO'}, self.message)
        return {'FINISHED'}


# Atomic Data Manager Cancel Operation Operator
class ATOMIC_OT_cancel_operation(bpy.types.Operator):
    """Cancel the current operation"""
    bl_idname = "atomic.cancel_operation"
    bl_label = "Cancel Operation"
    bl_description = "Cancel the currently running operation"

    def execute(self, context):
        atom = context.scene.atomic
        config.debug_print("[Atomic Debug] Cancel button pressed, setting cancel_operation = True")
        _safe_set_atom_property(atom, 'cancel_operation', True)
        config.debug_print(f"[Atomic Debug] After setting: atom.cancel_operation = {atom.cancel_operation}")
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

        # One empty-scene session for the whole Nuke (nuke.* helpers nest re-entrantly)
        with safe_delete.safe_datablock_removal():
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
        return wm.invoke_props_dialog(self, width=1000)


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
    unused_actions = None
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
                atom.actions or atom.worlds):

            ui_layouts.box_list(
                layout=layout,
            )

        # display when the main panel collections property is toggled
        if atom.collections:
            ui_layouts.box_list(
                layout=layout,
                title="Collections",
                items=self.unused_collections,
                icon="OUTLINER_OB_GROUP_INSTANCE",
                columns=4
            )

        # display when the main panel images property is toggled
        if atom.images:
            ui_layouts.box_list(
                layout=layout,
                title="Images",
                items=self.unused_images,
                icon="IMAGE_DATA",
                columns=4
            )

        # display when the main panel lights property is toggled
        if atom.lights:
            ui_layouts.box_list(
                layout=layout,
                title="Lights",
                items=self.unused_lights,
                icon="OUTLINER_OB_LIGHT",
                columns=4
            )

        # display when the main panel materials property is toggled
        if atom.materials:
            ui_layouts.box_list(
                layout=layout,
                title="Materials",
                items=self.unused_materials,
                icon="MATERIAL",
                columns=4
            )

        # display when the main panel node groups property is toggled
        if atom.node_groups:
            ui_layouts.box_list(
                layout=layout,
                title="Node Groups",
                items=self.unused_node_groups,
                icon="NODETREE",
                columns=4
            )

        # display when the main panel objects property is toggled
        if atom.objects:
            ui_layouts.box_list(
                layout=layout,
                title="Objects",
                items=self.unused_objects,
                icon="OBJECT_DATA",
                columns=4
            )

        # display when the main panel particle systems property is toggled
        if atom.particles:
            ui_layouts.box_list(
                layout=layout,
                title="Particle Systems",
                items=self.unused_particles,
                icon="PARTICLES",
                columns=4
            )

        # display when the main panel textures property is toggled
        if atom.textures:
            ui_layouts.box_list(
                layout=layout,
                title="Textures",
                items=self.unused_textures,
                icon="TEXTURE",
                columns=4
            )

        # display when the main panel armatures property is toggled
        if atom.armatures:
            ui_layouts.box_list(
                layout=layout,
                title="Armatures",
                items=self.unused_armatures,
                icon="ARMATURE_DATA",
                columns=4
            )

        # display when the main panel actions property is toggled
        if atom.actions:
            ui_layouts.box_list(
                layout=layout,
                title="Actions",
                items=self.unused_actions,
                icon="ACTION",
                columns=4
            )

        # display when the main panel worlds property is toggled
        if atom.worlds:
            ui_layouts.box_list(
                layout=layout,
                title="Worlds",
                items=self.unused_worlds,
                icon="WORLD",
                columns=4
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
        if atom.actions and self.unused_actions:
            total_items += len(self.unused_actions)
            categories_to_clean.append(('actions', self.unused_actions))
        if atom.worlds and self.unused_worlds:
            total_items += len(self.unused_worlds)
            categories_to_clean.append(('worlds', self.unused_worlds))

        if total_items == 0:
            # Nothing to delete
            bpy.ops.atomic.deselect_all()
            return {'FINISHED'}

        # Keep in-scene objects that are parented to / deformed by objects we delete
        if atom.objects and self.unused_objects:
            from .utils import clean as clean_utils
            for msg in clean_utils.detach_scene_objects_from_removal_targets(
                set(self.unused_objects)
            ):
                self.report({'INFO'}, msg)

        # Delete all items synchronously (empty-scene Safe Clean wraps the purge)
        deleted_count = 0
        with safe_delete.safe_datablock_removal():
            for category, unused_list in categories_to_clean:
                if not unused_list:
                    continue

                for item_key in unused_list:
                    try:
                        if category == 'collections':
                            if item_key in bpy.data.collections:
                                bpy.data.collections.remove(bpy.data.collections[item_key])
                                deleted_count += 1
                        elif category == 'images':
                            if item_key in bpy.data.images:
                                bpy.data.images.remove(bpy.data.images[item_key])
                                deleted_count += 1
                        elif category == 'lights':
                            if item_key in bpy.data.lights:
                                bpy.data.lights.remove(bpy.data.lights[item_key])
                                deleted_count += 1
                        elif category == 'materials':
                            if item_key in bpy.data.materials:
                                bpy.data.materials.remove(bpy.data.materials[item_key])
                                deleted_count += 1
                        elif category == 'node_groups':
                            if item_key in bpy.data.node_groups:
                                bpy.data.node_groups.remove(bpy.data.node_groups[item_key])
                                deleted_count += 1
                        elif category == 'objects':
                            if item_key in bpy.data.objects:
                                bpy.data.objects.remove(bpy.data.objects[item_key])
                                deleted_count += 1
                        elif category == 'particles':
                            if item_key in bpy.data.particles:
                                bpy.data.particles.remove(bpy.data.particles[item_key])
                                deleted_count += 1
                        elif category == 'textures':
                            if item_key in bpy.data.textures:
                                bpy.data.textures.remove(bpy.data.textures[item_key])
                                deleted_count += 1
                        elif category == 'armatures':
                            if item_key in bpy.data.armatures:
                                bpy.data.armatures.remove(bpy.data.armatures[item_key])
                                deleted_count += 1
                        elif category == 'actions':
                            if hasattr(bpy.data, 'actions') and item_key in bpy.data.actions:
                                bpy.data.actions.remove(bpy.data.actions[item_key])
                                deleted_count += 1
                        elif category == 'worlds':
                            if item_key in bpy.data.worlds:
                                bpy.data.worlds.remove(bpy.data.worlds[item_key])
                                deleted_count += 1
                    except Exception:
                        pass  # Item may have been deleted already or doesn't exist

        # Invalidate cache after cleaning (data has changed)
        global _cache_valid
        _cache_valid = False

        # Deselect all
        bpy.ops.atomic.deselect_all()

        return {'FINISHED'}

    def invoke(self, context, event):
        atom = context.scene.atomic
        
        # Store operator instance for dialog invocation
        global _clean_operator_instance, _clean_pending_results, _clean_pending_categories
        _clean_operator_instance = self
        
        # Check if there are pending results from a completed scan
        if _clean_pending_results is not None:
            # Populate from pending results and show dialog
            _populate_unused_lists(self, atom, _clean_pending_results)
            # Clear pending results
            _clean_pending_results = None
            _clean_pending_categories = None
            return context.window_manager.invoke_props_dialog(self, width=1000)
        
        # Determine which categories are selected
        selected_categories = []
        if atom.collections:
            selected_categories.append('collections')
        if atom.images:
            selected_categories.append('images')
        if atom.lights:
            selected_categories.append('lights')
        if atom.materials:
            selected_categories.append('materials')
        if atom.node_groups:
            selected_categories.append('node_groups')
        if atom.objects:
            selected_categories.append('objects')
        if atom.particles:
            selected_categories.append('particles')
        if atom.textures:
            selected_categories.append('textures')
        if atom.armatures:
            selected_categories.append('armatures')
        if atom.actions:
            selected_categories.append('actions')
        if atom.worlds:
            selected_categories.append('worlds')
        
        # Check if cache is valid and contains all selected categories
        global _unused_cache, _cache_valid
        if _cache_valid and _unused_cache is not None:
            # Check if cache has all selected categories
            cache_has_all = all(cat in _unused_cache for cat in selected_categories)
            if cache_has_all:
                # Use cached results immediately
                _populate_unused_lists(self, atom, _unused_cache)
                return context.window_manager.invoke_props_dialog(self, width=1000)
        
        # Need to scan - initialize progress tracking
        _safe_set_atom_property(atom, 'is_operation_running', True)
        _safe_set_atom_property(atom, 'operation_progress', 0.0)
        _safe_set_atom_property(atom, 'operation_status', "Initializing Clean scan...")
        _safe_set_atom_property(atom, 'cancel_operation', False)
        
        # Initialize module-level state for timer processing
        global _clean_invoke_state
        _clean_invoke_state = {
            'selected_categories': selected_categories,
            'operator_instance': self,
            'scan_started': False
        }
        
        # Start timer for processing
        bpy.app.timers.register(_process_clean_invoke_step)

        return {'FINISHED'}


def _process_clean_execute_step():
    """Process Clean execute (deletion) in steps to avoid blocking the UI"""
    global _clean_execute_state

    # Enter empty-scene session once; remember which scene owns Atomic UI state
    if _clean_execute_state and not _clean_execute_state.get('safe_clean_active'):
        try:
            _clean_execute_state['atom_scene_name'] = bpy.context.scene.name
        except (AttributeError, ReferenceError, RuntimeError):
            _clean_execute_state['atom_scene_name'] = None
        safe_delete.begin_safe_datablock_removal()
        _clean_execute_state['safe_clean_active'] = True

    # Prefer the user's scene for progress/cancel (not the throwaway empty scene)
    atom_scene_name = (
        _clean_execute_state.get('atom_scene_name') if _clean_execute_state else None
    )
    atom_scene = (
        bpy.data.scenes.get(atom_scene_name) if atom_scene_name else None
    )
    atom = (
        atom_scene.atomic
        if atom_scene is not None and hasattr(atom_scene, 'atomic')
        else bpy.context.scene.atomic
    )

    # Check for cancellation
    if atom.cancel_operation:
        # Leave empty-scene session if we had entered it
        if _clean_execute_state and _clean_execute_state.get('safe_clean_active'):
            safe_delete.end_safe_datablock_removal()
            _clean_execute_state['safe_clean_active'] = False
        _safe_set_atom_property(atom, 'is_operation_running', False)
        _safe_set_atom_property(atom, 'operation_progress', 0.0)
        _safe_set_atom_property(atom, 'operation_status', "Operation cancelled")
        _safe_set_atom_property(atom, 'cancel_operation', False)
        _clean_execute_state = None
        safe_delete.tag_atomic_ui_redraw()
        return None

    # Process categories one by one
    if _clean_execute_state['current_category_index'] < len(_clean_execute_state['categories_to_clean']):
        category, unused_list = _clean_execute_state['categories_to_clean'][_clean_execute_state['current_category_index']]

        if unused_list and _clean_execute_state['current_item_index'] < len(unused_list):
            # Delete current item
            item_key = unused_list[_clean_execute_state['current_item_index']]
            _safe_set_atom_property(atom, 'operation_status', f"Removing {category}: {item_key}...")

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
                elif category == 'actions':
                    if hasattr(bpy.data, 'actions') and item_key in bpy.data.actions:
                        bpy.data.actions.remove(bpy.data.actions[item_key])
                elif category == 'worlds':
                    if item_key in bpy.data.worlds:
                        bpy.data.worlds.remove(bpy.data.worlds[item_key])

                _clean_execute_state['deleted_count'] += 1
            except Exception:
                pass  # Item may have been deleted already or doesn't exist

            _clean_execute_state['current_item_index'] += 1
            progress = (_clean_execute_state['deleted_count'] / _clean_execute_state['total_items']) * 100.0
            _safe_set_atom_property(atom, 'operation_progress', progress)

            # Do not blanket tag_redraw here — forces View3D sync mid-purge
            return 0.01  # Continue processing
        else:
            # Move to next category
            _clean_execute_state['current_category_index'] += 1
            _clean_execute_state['current_item_index'] = 0
            return 0.01  # Continue to next category

    # All items deleted — leave empty-scene session before UI/redraw
    if _clean_execute_state.get('safe_clean_active'):
        safe_delete.end_safe_datablock_removal()
        _clean_execute_state['safe_clean_active'] = False

    deleted_count = _clean_execute_state['deleted_count']
    _safe_set_atom_property(atom, 'is_operation_running', False)
    _safe_set_atom_property(atom, 'operation_progress', 100.0)
    _safe_set_atom_property(atom, 'operation_status', f"Complete! Removed {deleted_count} unused data-blocks")

    # Clear state
    _clean_execute_state = None

    # Invalidate cache after cleaning (data has changed)
    _invalidate_cache()

    # Deselect all
    bpy.ops.atomic.deselect_all()

    safe_delete.tag_atomic_ui_redraw()

    return None  # Stop timer


def _on_smart_select_quick_scan_complete(results, **kwargs):
    """Callback for Smart Select quick scan completion.
    Processes quick scan results and triggers full scan for detected categories."""
    global _smart_select_state, _scan_state
    
    # Process quick scan results
    detected_categories = []
    for category, has_unused in results.items():
        if has_unused:
            detected_categories.append(category)
        _smart_select_state['unused_flags'][category] = has_unused
    
    _smart_select_state['detected_categories'] = detected_categories
    
    # If no categories detected, finish early
    if not detected_categories:
        atom = bpy.context.scene.atomic
        _finish_scan_operation(atom)
        _report_scan_analysis_info(_scan_state, "no unused items found")
        _smart_select_state = None
        for area in bpy.context.screen.areas:
            area.tag_redraw()
        return
    
    # Start full scan for detected categories
    scan_started_at = (
        _scan_state.get('started_at', time.monotonic()) if _scan_state else time.monotonic()
    )
    config.debug_print(f"[Atomic Debug] Smart Select: Quick scan complete, starting full scan for categories: {detected_categories}")
    _scan_state = {
        'mode': 'full',
        'categories_to_scan': detected_categories,
        'current_category_index': 0,
        'results': None,
        'status_updated': False,
        'callback': _on_smart_select_full_scan_complete,
        'callback_data': {},
        'graph_build_state': None,
        'ng_analysis_state': None,
        'material_session_ready': False,
        'mat_session_build_state': None,
        'mat_analysis_state': None,
        'cat_analysis_state': None,
        'progress_start': SMART_SELECT_QUICK_SCAN_PROGRESS_END,
        'progress_end': SCAN_PROGRESS_FINISH,
        'started_at': scan_started_at,
    }
    
    bpy.app.timers.register(_process_unified_scan_step)
    _process_unified_scan_step()  # Call immediately to start processing


def _on_smart_select_full_scan_complete(results, **kwargs):
    """Callback for Smart Select full scan completion.
    Processes full scan results, caches them, and updates UI toggles."""
    global _smart_select_state, _unused_cache, _cache_valid, _scan_state
    
    # Store results
    _smart_select_state['all_unused'] = results
    
    # Cache the results
    _unused_cache = results
    _cache_valid = True
    
    atom = bpy.context.scene.atomic
    _safe_set_atom_property(atom, 'operation_progress', SCAN_PROGRESS_FINISH)

    # Update UI toggles
    _safe_set_atom_property(atom, 'operation_status', "Updating selection...")
    atom.collections = _smart_select_state['unused_flags'].get('collections', False)
    atom.images = _smart_select_state['unused_flags'].get('images', False)
    atom.lights = _smart_select_state['unused_flags'].get('lights', False)
    atom.materials = _smart_select_state['unused_flags'].get('materials', False)
    atom.node_groups = _smart_select_state['unused_flags'].get('node_groups', False)
    atom.objects = _smart_select_state['unused_flags'].get('objects', False)
    atom.particles = _smart_select_state['unused_flags'].get('particles', False)
    atom.textures = _smart_select_state['unused_flags'].get('textures', False)
    atom.armatures = _smart_select_state['unused_flags'].get('armatures', False)
    atom.actions = _smart_select_state['unused_flags'].get('actions', False)
    atom.worlds = _smart_select_state['unused_flags'].get('worlds', False)
    
    # Operation complete
    category_count = len(_smart_select_state['detected_categories'])
    _finish_scan_operation(atom, progress=100.0)
    _report_scan_analysis_info(
        _scan_state,
        f"unused in {category_count} categor{'y' if category_count == 1 else 'ies'}",
    )
    
    # Clear state
    _smart_select_state = None
    
    # Force UI update
    for area in bpy.context.screen.areas:
        area.tag_redraw()


def _on_clean_scan_complete(results, **kwargs):
    """Callback for Clean scan completion.
    Populates operator properties and shows dialog."""
    global _clean_operator_instance, _clean_invoke_state, _clean_pending_results, _clean_pending_categories, _scan_state
    
    atom = bpy.context.scene.atomic
    
    # Store results for later use (operator instance may be invalidated)
    scan_results = results
    selected_categories = _clean_invoke_state.get('selected_categories', [])
    
    # Debug: Log all results
    config.debug_print(f"[Atomic Clean] Scan complete, results keys: {list(results.keys()) if results else 'None'}")
    for category in selected_categories:
        category_results = results.get(category, [])
        config.debug_print(f"[Atomic Clean] Category '{category}': {len(category_results) if category_results else 'None'} items")
    
    # Calculate found items for debug
    found_items = {}
    for category in selected_categories:
        unused_list = results.get(category, [])
        if unused_list:
            found_items[category] = len(unused_list)
    
    # Debug output
    if selected_categories:
        if found_items:
            config.debug_print(f"[Atomic Clean] Selected categories: {', '.join(selected_categories)}")
            config.debug_print(f"[Atomic Clean] Found unused items: {found_items}")
        else:
            config.debug_print(f"[Atomic Clean] Selected categories: {', '.join(selected_categories)}")
            config.debug_print(f"[Atomic Clean] WARNING: No unused items found in selected categories!")
    
    # Operation complete — show dialog; elapsed time goes to Info only.
    _finish_scan_operation(atom, progress=100.0)
    _report_scan_analysis_info(_scan_state)
    
    # Force UI update
    for area in bpy.context.screen.areas:
        area.tag_redraw()
    
    # Use a timer to invoke the dialog
    def show_dialog():
        global _clean_operator_instance, _clean_pending_results, _clean_pending_categories
        try:
            # Try to use stored operator instance first
            operator_instance = None
            if _clean_operator_instance is not None:
                try:
                    # Check if operator instance is still valid by accessing a property
                    _ = _clean_operator_instance.bl_idname
                    operator_instance = _clean_operator_instance
                except (ReferenceError, AttributeError, TypeError) as e:
                    # Operator instance invalidated
                    _clean_operator_instance = None
                    config.debug_print(f"[Atomic Debug] Clean: Stored operator instance invalidated: {e}")
            
            # If we have a valid operator instance, populate and show dialog
            if operator_instance:
                try:
                    _populate_unused_lists(operator_instance, atom, scan_results)
                    wm = bpy.context.window_manager
                    wm.invoke_props_dialog(operator_instance, width=1000)
                    _clean_operator_instance = None
                except (ReferenceError, AttributeError, TypeError) as e:
                    config.debug_print(f"[Atomic Error] Clean: Failed to populate/show dialog: {e}")
                    # Fall through to pending results approach
                    operator_instance = None
            
            # If operator instance is invalid, store results and invoke new operator
            if not operator_instance:
                # Store results for new operator invocation
                _clean_pending_results = scan_results
                _clean_pending_categories = selected_categories
                # Invoke a new operator instance
                bpy.ops.atomic.clean('INVOKE_DEFAULT')
        except Exception as e:
            config.debug_print(f"[Atomic Error] Clean: Failed to show dialog: {e}")
        return None  # Run once
    
    # Clear state
    _clean_invoke_state = None
    
    bpy.app.timers.register(show_dialog, first_interval=0.1)


def _process_unified_scan_step():
    """Unified scanning function that handles both quick and full scans with incremental support.
    Works for both Smart Select and Clean operations."""
    config.debug_print("[Atomic Debug] Unified Scanner: _process_unified_scan_step() called")
    try:
        # Check if context is valid
        if not hasattr(bpy.context, 'scene') or bpy.context.scene is None:
            config.debug_print("[Atomic Debug] Unified Scanner: Invalid context, returning")
            return None
        from ..stats import unused, unused_parallel, rna_analysis
        atom = bpy.context.scene.atomic
        global _scan_state, _unused_cache, _cache_valid
        
        config.debug_print(
            f"[Atomic Debug] Unified Scanner: _scan_state = "
            f"{_debug_summarize_scan_state(_scan_state)}"
        )
        
        # Check if scan state is initialized (mode should be set)
        if _scan_state is None or _scan_state.get('mode') is None:
            config.debug_print("[Atomic Debug] Unified Scanner: _scan_state is not initialized, returning")
            return None  # No scan in progress
        
        config.debug_print(f"[Atomic Debug] Unified Scanner: mode = {_scan_state.get('mode')}, current_category_index = {_scan_state.get('current_category_index')}, categories_to_scan = {_scan_state.get('categories_to_scan')}")
        
        # Check for cancellation
        if atom.cancel_operation:
            config.debug_print("[Atomic Debug] Unified Scanner: Operation cancelled")
            _safe_set_atom_property(atom, 'is_operation_running', False)
            _safe_set_atom_property(atom, 'operation_progress', 0.0)
            _safe_set_atom_property(atom, 'operation_status', "Operation cancelled")
            _safe_set_atom_property(atom, 'cancel_operation', False)
            _scan_state = None
            # Invalidate cache when scan is cancelled
            _invalidate_cache()
            config.debug_print("[Atomic Debug] Cache invalidated due to cancellation")
            for area in bpy.context.screen.areas:
                area.tag_redraw()
            return None
        
        # Check cache first (only for full scans)
        if _scan_state['mode'] == 'full' and _cache_valid and _unused_cache is not None:
            config.debug_print("[Atomic Debug] Unified Scanner: Using cached results")
            # Check if cache has all requested categories
            cache_has_all = all(cat in _unused_cache for cat in _scan_state['categories_to_scan'])
            if cache_has_all:
                # Filter cache to only include requested categories
                filtered_results = {cat: _unused_cache[cat] for cat in _scan_state['categories_to_scan']}
                _scan_state['results'] = filtered_results
                progress_end = float(_scan_state.get('progress_end', SCAN_PROGRESS_FINISH))
                _safe_set_atom_property(atom, 'operation_progress', progress_end)
                _safe_set_atom_property(atom, 'operation_status', "Using cached results...")
                config.debug_print("[Atomic Debug] Unified Scanner: Using cached results")
                # Call callback with cached results
                if _scan_state['callback']:
                    _scan_state['callback'](_scan_state['results'], **_scan_state['callback_data'])
                _scan_state = None
                for area in bpy.context.screen.areas:
                    area.tag_redraw()
                return None
        
        # Process categories one by one (sequentially, not in parallel)
        # NOTE: Categories are processed sequentially to avoid race conditions with Blender's data API.
        # This means materials will wait for images to finish scanning, which can appear as "stuck"
        # when images are doing a deep scan. This is intentional for thread-safety.
        total_categories = len(_scan_state['categories_to_scan'])
        current_idx = _scan_state['current_category_index']
        config.debug_print(f"[Atomic Debug] Unified Scanner: Processing category {current_idx + 1}/{total_categories} (index {current_idx})")
        config.debug_print(f"[Atomic Debug] Unified Scanner: Condition check: {current_idx} < {total_categories} = {current_idx < total_categories}")
        if current_idx < total_categories:
            category = _scan_state['categories_to_scan'][_scan_state['current_category_index']]
            config.debug_print(f"[Atomic Debug] Unified Scanner: Current category = {category}")
            
            # Update status first, then return to let UI refresh
            if not _scan_state['status_updated']:
                config.debug_print(f"[Atomic Debug] Unified Scanner: Updating status for {category}")
                status = (
                    f"Scanning {category}..."
                    if _scan_state['mode'] == 'quick'
                    else f"Counting {category}..."
                )
                _unified_scan_progress(
                    atom,
                    _scan_state,
                    sub_fraction=0.0,
                    status_text=status,
                )
                _scan_state['status_updated'] = True
                # Force UI update and return to let it refresh
                for area in bpy.context.screen.areas:
                    area.tag_redraw()
                return 0.01  # Return to let UI update
            
            config.debug_print(f"[Atomic Debug] Unified Scanner: Status already updated, processing category '{category}' (mode={_scan_state['mode']})")
            
            # Initialize results dict if needed
            if _scan_state['results'] is None:
                _scan_state['results'] = {}
            
            current_filepath = bpy.data.filepath
            cached_filepath = getattr(_process_unified_scan_step, '_rna_graph_filepath', None)
            graph_cached = (
                hasattr(_process_unified_scan_step, '_rna_graph')
                and current_filepath == cached_filepath
            )

            # Incremental RNA graph build (one data-block type per timer tick)
            if not graph_cached:
                if _scan_state['graph_build_state'] is None:
                    _scan_state['graph_build_state'] = rna_analysis.begin_rna_graph_build()
                    _scan_state['_ref_graph_display_sub'] = 0.0
                build_state = _scan_state['graph_build_state']
                if build_state.get('phase') == 'graph':
                    _reference_graph_scan_progress(
                        atom, _scan_state, build_state, step_label='graph'
                    )
                done, graph, step_label = rna_analysis.step_rna_graph_build(build_state)
                _reference_graph_scan_progress(
                    atom, _scan_state, build_state, step_label=step_label
                )
                if not done:
                    _unified_scan_redraw()
                    return 0.01
                if config.enable_debug_prints and _scan_state['graph_build_state'] is not None:
                    rna_dump_path = os.path.join(
                        tempfile.gettempdir(),
                        f"atomic_rna_dump_{int(time.time())}.json",
                    )
                    try:
                        with open(rna_dump_path, 'w', encoding='utf-8') as dump_file:
                            json.dump(
                                _scan_state['graph_build_state'].get('rna_data', {}),
                                dump_file,
                                indent=2,
                            )
                        config.debug_print(
                            f"[Atomic Debug] Unified Scanner: RNA data dumped to {rna_dump_path}"
                        )
                    except Exception as dump_err:
                        config.debug_print(
                            f"[Atomic Debug] Unified Scanner: RNA dump failed: {dump_err}"
                        )
                _process_unified_scan_step._rna_graph = graph
                _process_unified_scan_step._rna_graph_filepath = current_filepath
                if _scan_state['graph_build_state'] is not None:
                    _process_unified_scan_step._rna_data = _scan_state[
                        'graph_build_state'
                    ].get('rna_data', {})
                _scan_state['graph_build_state'] = None
                _scan_state['_display_sub_fraction'] = 0.0
                config.debug_print("[Atomic Debug] Unified Scanner: RNA dependency graph built")

            # node_groups: scan pre-built RNA graph indices (node_groups_deep parity)
            if category == 'node_groups':
                if _scan_state['ng_analysis_state'] is None:
                    _scan_state['ng_analysis_state'] = rna_analysis.begin_node_groups_analysis(
                        _process_unified_scan_step._rna_graph,
                        short_circuit=_scan_state['mode'] == 'quick',
                    )
                ng_state = _scan_state['ng_analysis_state']
                done, unused_list, ng_frac, current_ng = rna_analysis.step_node_groups_analysis(
                    ng_state
                )
                if ng_state['indices'] is None:
                    built = ng_state['index_build_state']['obj_index']
                    total_objs = len(ng_state['index_build_state']['object_names'])
                    _unified_scan_progress(
                        atom,
                        _scan_state,
                        sub_fraction=ng_frac,
                        status_text=(
                            f"Indexing scene objects ({built}/{total_objs})..."
                        ),
                    )
                else:
                    checked = ng_state['index']
                    total_ng = len(ng_state['names'])
                    ng_label = current_ng or ''
                    status = f"Checking node groups ({checked}/{total_ng})"
                    if ng_label:
                        status = f"{status}: {ng_label}"
                    _unified_scan_progress(
                        atom,
                        _scan_state,
                        sub_fraction=ng_frac,
                        status_text=f"{status}...",
                    )
                if not done:
                    _unified_scan_redraw()
                    return 0.01
                if _scan_state['mode'] == 'quick':
                    _scan_state['results'][category] = len(unused_list) > 0
                else:
                    _scan_state['results'][category] = unused_list
                _scan_state['ng_analysis_state'] = None

            elif category == 'materials':
                from ..stats import users as users_stats

                if not _scan_state['material_session_ready']:
                    if _scan_state['mat_session_build_state'] is None:
                        users_stats.clear_material_scan_caches()
                        _scan_state['mat_session_build_state'] = (
                            users_stats.begin_material_session_build()
                        )
                        _scan_state['_mat_session_display_sub'] = 0.0
                        _scan_state['_mat_session_phase'] = None
                    session_done, session_frac = users_stats.step_material_session_build(
                        _scan_state['mat_session_build_state']
                    )
                    build_state = _scan_state['mat_session_build_state']
                    total_objs = max(len(build_state['object_names']), 1)
                    if build_state['phase'] == 'slots':
                        done_objs = build_state['obj_index']
                        phase_name = 'material slots'
                    else:
                        done_objs = build_state['gn_obj_index']
                        phase_name = 'geometry nodes'
                    _material_session_scan_progress(
                        atom,
                        _scan_state,
                        session_frac,
                        done_objs,
                        total_objs,
                        phase_name,
                        phase_key=build_state['phase'],
                    )
                    if not session_done:
                        _unified_scan_redraw()
                        return 0.01
                    _scan_state['mat_session_build_state'] = None
                    _scan_state['material_session_ready'] = True
                    _scan_state['_display_sub_fraction'] = MATERIAL_SESSION_PROGRESS_SLICE
                    _unified_scan_redraw()
                    return 0.01

                if _scan_state['mat_analysis_state'] is None:
                    _scan_state['mat_analysis_state'] = rna_analysis.begin_materials_analysis(
                        _process_unified_scan_step._rna_graph,
                        short_circuit=_scan_state['mode'] == 'quick',
                    )
                mat_state = _scan_state['mat_analysis_state']
                done, unused_list, mat_frac, current_mat = rna_analysis.step_materials_analysis(
                    mat_state
                )
                checked = mat_state['index']
                total_mats = len(mat_state['names'])
                mat_label = current_mat or ''
                status = f"Analyzing materials ({checked}/{total_mats})"
                if mat_label:
                    status = f"{status}: {mat_label}"
                mat_slice = MATERIAL_SESSION_PROGRESS_SLICE
                _unified_scan_progress(
                    atom,
                    _scan_state,
                    sub_fraction=mat_slice + mat_frac * (1.0 - mat_slice),
                    status_text=f"{status}...",
                )
                if not done:
                    _unified_scan_redraw()
                    return 0.01
                if _scan_state['mode'] == 'quick':
                    _scan_state['results'][category] = len(unused_list) > 0
                else:
                    _scan_state['results'][category] = unused_list
                _scan_state['mat_analysis_state'] = None
                _scan_state['material_session_ready'] = False

            else:
                if _scan_state['cat_analysis_state'] is None or (
                    _scan_state['cat_analysis_state'].get('category') != category
                ):
                    if _scan_state['mode'] == 'quick':
                        config.debug_print(
                            f"[Atomic Debug] Unified Scanner: Quick scan for "
                            f"'{category}' using RNA analysis"
                        )
                    else:
                        config.debug_print(
                            f"[Atomic Debug] Unified Scanner: Full scan for "
                            f"'{category}' using RNA analysis"
                        )
                    _scan_state['cat_analysis_state'] = (
                        rna_analysis.begin_graph_category_analysis(
                            _process_unified_scan_step._rna_graph,
                            category,
                            short_circuit=_scan_state['mode'] == 'quick',
                        )
                    )
                cat_state = _scan_state['cat_analysis_state']
                done, unused_list, cat_frac, current_item = (
                    rna_analysis.step_graph_category_analysis(cat_state)
                )
                checked = cat_state['index']
                total_items = len(cat_state['names'])
                item_label = current_item or ''
                if cat_state['used'] is None:
                    status = f"Building usage graph for {category}..."
                else:
                    status = f"Analyzing {category} ({checked}/{total_items})"
                    if item_label:
                        status = f"{status}: {item_label}"
                _unified_scan_progress(
                    atom,
                    _scan_state,
                    sub_fraction=cat_frac,
                    status_text=f"{status}...",
                )
                if not done:
                    _unified_scan_redraw()
                    return 0.01
                if _scan_state['mode'] == 'quick':
                    _scan_state['results'][category] = len(unused_list) > 0
                else:
                    _scan_state['results'][category] = unused_list
                    config.debug_print(
                        f"[Atomic Debug] Unified Scanner: RNA analysis found "
                        f"{len(unused_list)} unused {category}"
                    )
                _scan_state['cat_analysis_state'] = None
            
            # Move to next category
            _scan_state['current_category_index'] += 1
            _scan_state['status_updated'] = False
            _scan_state['material_session_ready'] = False
            _scan_state['ng_analysis_state'] = None
            _scan_state['mat_session_build_state'] = None
            _scan_state['mat_analysis_state'] = None
            _scan_state['cat_analysis_state'] = None
            _unified_scan_progress(atom, _scan_state, sub_fraction=0.0)
            config.debug_print(
                f"[Atomic Debug] Unified Scanner: Finished '{category}', moved to index "
                f"{_scan_state['current_category_index']}/{total_categories}, "
                f"results: {_debug_summarize_results(_scan_state['results'])}"
            )
            
            _unified_scan_redraw()
            return 0.01
        
        # All categories scanned
        config.debug_print(
            f"[Atomic Debug] Unified Scanner: All categories scanned! "
            f"current_index={_scan_state.get('current_category_index')}, "
            f"total={total_categories}, "
            f"results={_debug_summarize_results(_scan_state.get('results'))}"
        )
        progress_end = float(_scan_state.get('progress_end', SCAN_PROGRESS_FINISH))
        _safe_set_atom_property(atom, 'operation_progress', progress_end)
        _safe_set_atom_property(atom, 'operation_status', "Scan complete, processing results...")
        
        # Ensure results is a dictionary, not None
        if _scan_state['results'] is None:
            _scan_state['results'] = {}
        
        # Cache results if full scan
        if _scan_state['mode'] == 'full':
            _unused_cache = _scan_state['results']
            _cache_valid = True
        
        # Call callback function with results
        if _scan_state['callback']:
            config.debug_print(
                f"[Atomic Debug] Unified Scanner: Calling callback with results: "
                f"{_debug_summarize_results(_scan_state['results'])}"
            )
            old_mode = _scan_state.get('mode')
            old_categories = _scan_state.get('categories_to_scan', [])[:]  # Copy list
            _scan_state['callback'](_scan_state['results'], **_scan_state['callback_data'])
            
            # Check if callback started a new scan (callback may have set up new _scan_state)
            # If _scan_state still exists and has different mode/categories, callback started new scan
            if _scan_state is not None:
                new_mode = _scan_state.get('mode')
                new_categories = _scan_state.get('categories_to_scan', [])
                if (new_mode != old_mode or new_categories != old_categories):
                    # Different scan started by callback, keep it and continue
                    config.debug_print(f"[Atomic Debug] Unified Scanner: Callback started new scan (old: {old_mode}/{old_categories}, new: {new_mode}/{new_categories}), keeping _scan_state")
                    # Force UI update
                    for area in bpy.context.screen.areas:
                        area.tag_redraw()
                    return 0.01  # Continue with new scan
                else:
                    # Same scan, clear it
                    _scan_state = None
            # else: callback cleared _scan_state itself, which is fine
        
        # Clear state (only if not already cleared or replaced by callback)
        if _scan_state is not None:
            _scan_state = None
        
        # Force UI update
        for area in bpy.context.screen.areas:
            area.tag_redraw()
        
        return None  # Stop timer
    except Exception as e:
        # Handle any errors
        import traceback
        config.debug_print(f"[Atomic Error] Unified scan step failed: {e}")
        traceback.print_exc()
        try:
            atom = bpy.context.scene.atomic
            _safe_set_atom_property(atom, 'is_operation_running', False)
            _safe_set_atom_property(atom, 'operation_progress', 0.0)
            _safe_set_atom_property(atom, 'operation_status', f"Error: {str(e)}")
        except:
            pass
        _scan_state = None
        try:
            for area in bpy.context.screen.areas:
                area.tag_redraw()
        except:
            pass
        return None  # Stop timer


def _populate_unused_lists(operator_instance, atom, all_unused):
    """Helper to populate unused lists from all_unused dict"""
    config.debug_print(f"[Atomic Debug] _populate_unused_lists: all_unused keys = {list(all_unused.keys()) if all_unused else 'None'}")
    if atom.collections:
        operator_instance.unused_collections = all_unused.get('collections', [])
    if atom.images:
        images_result = all_unused.get('images', [])
        operator_instance.unused_images = images_result
        config.debug_print(f"[Atomic Debug] _populate_unused_lists: images result = {len(images_result) if images_result else 'None'} items")
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
    if atom.actions:
        operator_instance.unused_actions = all_unused.get('actions', [])
    if atom.worlds:
        operator_instance.unused_worlds = all_unused.get('worlds', [])


def _process_clean_invoke_step():
    """Process Clean invoke in steps to avoid blocking the UI.
    Uses unified scanner for full scan of selected categories only."""
    config.debug_print("[Atomic Debug] Clean: _process_clean_invoke_step() called")
    try:
        # Check if context is valid
        if not hasattr(bpy.context, 'scene') or bpy.context.scene is None:
            config.debug_print("[Atomic Debug] Clean: Invalid context, returning")
            return None
        atom = bpy.context.scene.atomic
        global _clean_invoke_state, _scan_state
        
        config.debug_print(f"[Atomic Debug] Clean: _clean_invoke_state = {_clean_invoke_state}")
        config.debug_print(f"[Atomic Debug] Clean: _scan_state = {_debug_summarize_scan_state(_scan_state)}")
        
        # Check for cancellation
        if atom.cancel_operation:
            config.debug_print("[Atomic Debug] Clean: Operation cancelled")
            _safe_set_atom_property(atom, 'is_operation_running', False)
            _safe_set_atom_property(atom, 'operation_progress', 0.0)
            _safe_set_atom_property(atom, 'operation_status', "Operation cancelled")
            _safe_set_atom_property(atom, 'cancel_operation', False)
            _clean_invoke_state = None
            _scan_state = None
            # Invalidate cache when scan is cancelled
            _invalidate_cache()
            config.debug_print("[Atomic Debug] Cache invalidated due to cancellation")
            # Force UI update
            for area in bpy.context.screen.areas:
                area.tag_redraw()
            return None
        
        # Check if scan has been started
        scan_started = _clean_invoke_state.get('scan_started', False)
        config.debug_print(f"[Atomic Debug] Clean: scan_started = {scan_started}, condition result = {not scan_started}")
        if not scan_started:
            config.debug_print("[Atomic Debug] Clean: Starting scan initialization")
            # Start unified scanner for selected categories only
            _clean_invoke_state['scan_started'] = True
            config.debug_print(f"[Atomic Debug] Clean: Selected categories: {_clean_invoke_state['selected_categories']}")
            if not _clean_invoke_state['selected_categories']:
                # No categories selected, finish immediately
                _safe_set_atom_property(atom, 'is_operation_running', False)
                _safe_set_atom_property(atom, 'operation_progress', 100.0)
                _safe_set_atom_property(atom, 'operation_status', "No categories selected")
                _clean_invoke_state = None
                for area in bpy.context.screen.areas:
                    area.tag_redraw()
                return None
            _safe_set_atom_property(atom, 'operation_status', f"Starting scan of {len(_clean_invoke_state['selected_categories'])} categories...")
            for area in bpy.context.screen.areas:
                area.tag_redraw()
            config.debug_print("[Atomic Debug] Clean: Creating _scan_state for full scan")
            _scan_state = {
                'mode': 'full',
                'categories_to_scan': _clean_invoke_state['selected_categories'],
                'current_category_index': 0,
                'results': None,
                'status_updated': False,
                'callback': _on_clean_scan_complete,
                'callback_data': {},
                'graph_build_state': None,
                'ng_analysis_state': None,
                'material_session_ready': False,
                'mat_session_build_state': None,
                'mat_analysis_state': None,
                'cat_analysis_state': None,
                'progress_start': 0.0,
                'progress_end': SCAN_PROGRESS_FINISH,
                'started_at': time.monotonic(),
            }
            # Start unified scanner and stop this timer (unified scanner will handle everything)
            # Always register the timer - it will handle its own lifecycle
            config.debug_print("[Atomic Debug] Clean: Registering unified scanner timer")
            bpy.app.timers.register(_process_unified_scan_step)
            config.debug_print("[Atomic Debug] Clean: Calling _process_unified_scan_step() immediately")
            result = _process_unified_scan_step()
            config.debug_print(f"[Atomic Debug] Clean: _process_unified_scan_step() returned: {result}")
            return None  # Stop this timer
        
        # If we get here, something went wrong (shouldn't happen)
        config.debug_print("[Atomic Debug] Clean: Reached end of function unexpectedly")
        return None
    except Exception as e:
        import traceback
        config.debug_print(f"[Atomic Error] Clean invoke step failed: {e}")
        traceback.print_exc()
        try:
            if hasattr(bpy.context, 'scene') and bpy.context.scene is not None:
                atom = bpy.context.scene.atomic
                _safe_set_atom_property(atom, 'is_operation_running', False)
                _safe_set_atom_property(atom, 'operation_progress', 0.0)
                _safe_set_atom_property(atom, 'operation_status', f"Error: {str(e)}")
        except:
            pass
        return None


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
        _safe_set_atom_property(atom, 'is_operation_running', True)
        _safe_set_atom_property(atom, 'operation_progress', 0.0)
        _safe_set_atom_property(atom, 'operation_status', "Initializing Smart Select...")
        _safe_set_atom_property(atom, 'cancel_operation', False)
        
        # Initialize module-level state for timer processing
        global _smart_select_state
        _smart_select_state = {
            'unused_flags': {},  # Quick scan results: {category: bool}
            'all_unused': None,  # Full scan results: {category: [items]}
            'detected_categories': [],  # Categories with unused items
            'quick_scan_started': False  # Track if quick scan has started
        }
        
        # Start timer for processing
        bpy.app.timers.register(_process_smart_select_step)
        
        return {'FINISHED'}


def _process_smart_select_step():
    """Process Smart Select in steps to avoid blocking the UI.
    Uses unified scanner for both quick and full scans."""
    config.debug_print("[Atomic Debug] Smart Select: _process_smart_select_step() called")
    try:
        # Check if context is valid
        if not hasattr(bpy.context, 'scene') or bpy.context.scene is None:
            config.debug_print("[Atomic Debug] Smart Select: Invalid context, returning")
            return None
        atom = bpy.context.scene.atomic
        global _smart_select_state, _scan_state
        
        config.debug_print(f"[Atomic Debug] Smart Select: _smart_select_state = {_smart_select_state}")
        config.debug_print(f"[Atomic Debug] Smart Select: _scan_state = {_debug_summarize_scan_state(_scan_state)}")
        
        # Check for cancellation
        if atom.cancel_operation:
            _safe_set_atom_property(atom, 'is_operation_running', False)
            _safe_set_atom_property(atom, 'operation_progress', 0.0)
            _safe_set_atom_property(atom, 'operation_status', "Operation cancelled")
            _safe_set_atom_property(atom, 'cancel_operation', False)
            _smart_select_state = None
            _scan_state = None
            # Force UI update
            for area in bpy.context.screen.areas:
                area.tag_redraw()
            return None
        
        # Step 1: Quick scan to detect categories with unused items
        if not _smart_select_state.get('quick_scan_started', False):
            config.debug_print("[Atomic Debug] Smart Select: Starting quick scan initialization")
            # Start quick scan
            _smart_select_state['quick_scan_started'] = True
            _safe_set_atom_property(atom, 'operation_status', "Starting quick scan...")
            for area in bpy.context.screen.areas:
                area.tag_redraw()
            config.debug_print("[Atomic Debug] Smart Select: Creating _scan_state for quick scan")
            _scan_state = {
                'mode': 'quick',
                'categories_to_scan': list(unused_parallel.CATEGORIES),
                'current_category_index': 0,
                'results': None,
                'status_updated': False,
                'callback': _on_smart_select_quick_scan_complete,
                'callback_data': {},
                'graph_build_state': None,
                'ng_analysis_state': None,
                'material_session_ready': False,
                'mat_session_build_state': None,
                'mat_analysis_state': None,
                'cat_analysis_state': None,
                'progress_start': 0.0,
                'progress_end': SMART_SELECT_QUICK_SCAN_PROGRESS_END,
                'started_at': time.monotonic(),
            }
            # Start unified scanner and stop this timer (unified scanner will handle everything)
            # Always register the timer - it will handle its own lifecycle
            config.debug_print("[Atomic Debug] Smart Select: Registering unified scanner timer")
            bpy.app.timers.register(_process_unified_scan_step)
            config.debug_print("[Atomic Debug] Smart Select: Calling _process_unified_scan_step() immediately")
            result = _process_unified_scan_step()
            config.debug_print(f"[Atomic Debug] Smart Select: _process_unified_scan_step() returned: {result}")
            return None  # Stop this timer
        
        # If we get here, something went wrong (shouldn't happen)
        config.debug_print("[Atomic Debug] Smart Select: Reached end of function unexpectedly")
        return None
    except Exception as e:
        import traceback
        config.debug_print(f"[Atomic Error] Smart Select step failed: {e}")
        traceback.print_exc()
        try:
            if hasattr(bpy.context, 'scene') and bpy.context.scene is not None:
                atom = bpy.context.scene.atomic
                _safe_set_atom_property(atom, 'is_operation_running', False)
                _safe_set_atom_property(atom, 'operation_progress', 0.0)
                _safe_set_atom_property(atom, 'operation_status', f"Error: {str(e)}")
        except:
            pass
        return None


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
        bpy.context.scene.atomic.actions = True
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
        bpy.context.scene.atomic.actions = False
        bpy.context.scene.atomic.worlds = False

        return {'FINISHED'}


reg_list = [
    ATOMIC_OT_clear_cache,
    ATOMIC_OT_report_info,
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

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

This file contains operations for missing file handling. This includes
the option to reload, remove, replace, and search for these missing files.

It also contains the post-reload report dialog that appears after
attempting to reload missing project files.

"""

import bpy
import os
import sys
import types
import threading
import queue
from bpy.utils import register_class
from ..utils import compat
from ..utils import wm_progress
from ..stats import missing
from ..ui.utils import ui_layouts
from .. import config


# Atomic Data Manager Reload Missing Files Operator
class ATOMIC_OT_reload_missing(bpy.types.Operator):
    """Reload missing files"""
    bl_idname = "atomic.reload_missing"
    bl_label = "Reload Missing Files"

    def execute(self, context):
        # reload images
        for image in bpy.data.images:
            try:
                image.reload()
            except (RuntimeError, OSError) as e:
                config.debug_print(f"[Atomic Debug] Failed to reload image '{image.name}': {e}")
                continue

        # reload libraries
        for library in bpy.data.libraries:
            try:
                library.reload()
            except (RuntimeError, OSError) as e:
                config.debug_print(f"[Atomic Debug] Failed to reload library '{library.name}': {e}")
                continue

        # call reload report
        bpy.ops.atomic.reload_report('INVOKE_DEFAULT')
        return {'FINISHED'}


# Atomic Data Manager Reload Missing Files Report Operator
class ATOMIC_OT_reload_report(bpy.types.Operator):
    """Reload report for missing files"""
    bl_idname = "atomic.reload_report"
    bl_label = "Missing File Reload Report"

    def draw(self, context):
        layout = self.layout
        missing_images = missing.images()
        missing_libraries = missing.libraries()

        if missing_images or missing_libraries:
            row = layout.row()
            row.label(
                text="Atomic was unable to reload the following files:"
            )

            if missing_images:
                ui_layouts.box_list(
                    layout=self.layout,
                    items=missing_images,
                    icon='IMAGE_DATA',
                    columns=4
                )

            if missing_libraries:
                ui_layouts.box_list(
                    layout=self.layout,
                    items=missing_libraries,
                    icon='LIBRARY_DATA_DIRECT',
                    columns=4
                )

        else:
            row = layout.row()
            row.label(text="All files successfully reloaded!")

        row = layout.row()  # extra space

    def execute(self, context):
        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self, width=800)


# Atomic Data Manager Remove Missing Files Operator
class ATOMIC_OT_remove_missing(bpy.types.Operator):
    """Remove all missing files from this project"""
    bl_idname = "atomic.remove_missing"
    bl_label = "Remove Missing Files"

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.label(text="Remove the following data-blocks?")

        ui_layouts.box_list(
            layout=layout,
            items=missing.images(),
            icon="IMAGE_DATA",
            columns=2
        )

        row = layout.row()  # extra space

    def execute(self, context):
        for image_key in missing.images():
            bpy.data.images.remove(bpy.data.images[image_key])

        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)


# Module-level state for missing-file search (libraries + images)
_library_search_state = {
    'is_searching': False,
    'found_blend_files': [],
    'found_image_files': [],
    'matches': {},  # library_key -> match info
    'image_matches': {},  # image_key -> match info
    'progress': 0.0,
    'status': '',
    'search_thread': None,
    'progress_queue': None,
    'search_complete': False,
    'search_error': None
}

# Process-lifetime Search Missing file index (.blend + images).
# NOT bpy.app.driver_namespace — that is wiped on open/revert/load_post.
# NOT a module global — F8 / vscode addon reload re-executes this module.
# A dedicated sys.modules entry survives both until Blender quits.
_PROCESS_STORE_NAME = "atomic_data_manager._process_lifetime"
_FILE_SEARCH_INDEX_ATTR = "file_search_index"
# Legacy DNS key from b40c4df; migrate once then drop (DNS dies on file load).
_FILE_SEARCH_INDEX_DNS_LEGACY = "atomic_data_manager_file_search_index"


def _process_store():
    """Return the Blender-executable singleton module used for process caches."""
    mod = sys.modules.get(_PROCESS_STORE_NAME)
    if mod is None:
        mod = types.ModuleType(_PROCESS_STORE_NAME)
        sys.modules[_PROCESS_STORE_NAME] = mod
    return mod


def _empty_file_search_index():
    """Fresh empty index payload."""
    return {
        'roots_key': None,  # tuple of uppercased normalized roots
        'blends': [],
        'images': [],
    }


def _file_search_index():
    """
    Session file index (.blend + images) for this Blender executable.

    Survives blend open/revert and addon reloads; cleared on Blender quit,
    root changes, or manual Clear Search Index / Clear Cache.
    """
    store = _process_store()
    idx = getattr(store, _FILE_SEARCH_INDEX_ATTR, None)
    if not isinstance(idx, dict):
        # One-shot migrate from pre-fix DNS storage if still present.
        legacy = bpy.app.driver_namespace.pop(_FILE_SEARCH_INDEX_DNS_LEGACY, None)
        idx = legacy if isinstance(legacy, dict) else _empty_file_search_index()
        setattr(store, _FILE_SEARCH_INDEX_ATTR, idx)
    return idx


# Common Blender-loadable still/sequence image + movie extensions
_IMAGE_EXTENSIONS = frozenset({
    '.png', '.jpg', '.jpeg', '.jpe', '.jp2', '.j2c',
    '.exr', '.hdr', '.tif', '.tiff', '.tga', '.bmp', '.webp',
    '.psd', '.dpx', '.cin', '.rgb', '.rgba', '.sgi', '.iff',
    '.pict', '.dds',
    '.mp4', '.mov', '.avi', '.mkv', '.webm', '.mpg', '.mpeg', '.ogg', '.ogv',
})


def clear_file_search_index():
    """Drop the session search index (manual clear / Clear Cache / root change)."""
    setattr(_process_store(), _FILE_SEARCH_INDEX_ATTR, _empty_file_search_index())
    bpy.app.driver_namespace.pop(_FILE_SEARCH_INDEX_DNS_LEGACY, None)
    config.debug_print("[Atomic Debug] File search index cleared")


def clear_blend_search_index():
    """Alias for clear_file_search_index (callers / Clear Cache)."""
    clear_file_search_index()


def _search_roots_key(directories):
    """Stable key for a set of search-root directories."""
    return tuple(sorted(d.upper() for d in (directories or []) if d))


def _cached_files_for_roots(directories):
    """
    Return (blends, images) copies when roots match the session index.

    None if cache miss.
    """
    idx = _file_search_index()
    key = _search_roots_key(directories)
    if (
        key
        and idx.get('roots_key') == key
        and idx.get('blends') is not None
        and idx.get('images') is not None
    ):
        return (
            list(idx['blends']),
            list(idx['images']),
        )
    return None


def _store_file_search_index(directories, blends, images):
    """Remember walk results for this Blender executable session."""
    idx = {
        'roots_key': _search_roots_key(directories),
        'blends': list(blends or []),
        'images': list(images or []),
    }
    setattr(_process_store(), _FILE_SEARCH_INDEX_ATTR, idx)
    bpy.app.driver_namespace.pop(_FILE_SEARCH_INDEX_DNS_LEGACY, None)
    config.debug_print(
        f"[Atomic Debug] File search index stored: "
        f"{len(idx['blends'])} blends, "
        f"{len(idx['images'])} images for "
        f"{len(idx['roots_key'] or ())} roots"
    )


def _file_ext(filename):
    """Lowercase extension including the leading dot."""
    return os.path.splitext(filename)[1].lower()


def _is_image_filename(filename):
    """True if filename looks like a still/sequence/movie Blender can load."""
    return _file_ext(filename) in _IMAGE_EXTENSIONS


def _is_blend_filename(filename):
    return _file_ext(filename) == '.blend'


# Popup regions from Search dialog draw() — area.tag_redraw alone does not
# refresh invoke_props_dialog; need Region.tag_refresh_ui (Blender 4.2+).
# See bpy.types.Context.region_popup / Region.tag_refresh_ui.
_search_popup_regions = set()


def _region_still_exists(region):
    """
    True if region is still a live UI region.

    Matches Blender's bl_pkg notify helper: temp_override(region=...) fails
    with TypeError once the popup is gone.
    """
    if region is None:
        return False
    try:
        with bpy.context.temp_override(region=region):
            return True
    except (TypeError, ReferenceError, AttributeError):
        return False


def _remember_search_popup_region(context):
    """Cache the Search props-dialog region for timer-driven refresh."""
    region = getattr(context, "region_popup", None)
    if region is not None:
        _search_popup_regions.add(region)


def _tag_search_ui_redraw():
    """
    Force Search dialog + screen areas to redraw during background search.

    Mouse-move over the popup works because it marks the popup region dirty;
    timers must call tag_refresh_ui on context.region_popup explicitly.
    """
    # Drop freed popup regions
    for region in list(_search_popup_regions):
        if not _region_still_exists(region):
            _search_popup_regions.discard(region)
            continue
        try:
            region.tag_redraw()
            if hasattr(region, "tag_refresh_ui"):
                region.tag_refresh_ui()
        except (ReferenceError, AttributeError):
            _search_popup_regions.discard(region)

    # Also dirty normal areas (status labels elsewhere, etc.)
    try:
        for window in bpy.context.window_manager.windows:
            screen = window.screen
            if not screen:
                continue
            for area in screen.areas:
                area.tag_redraw()
    except Exception:
        pass


def _normalize_search_directories(directories):
    """Return unique absolute directory paths from a list of path strings."""
    out = []
    seen = set()
    for raw in directories or []:
        if not raw:
            continue
        try:
            abs_dir = bpy.path.abspath(raw)
        except Exception:
            abs_dir = raw
        abs_dir = os.path.normpath(abs_dir)
        key = abs_dir.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(abs_dir)
    return out


def _wm_search_paths(context=None):
    """Active Search-dialog folder collection on WindowManager."""
    wm = (context or bpy.context).window_manager
    return wm.atomic_remap_search_paths


def _wm_search_path_strings(context=None):
    """Non-empty path strings from the Search-dialog folder list."""
    return [
        item.path for item in _wm_search_paths(context)
        if (item.path or "").strip()
    ]


def _fill_wm_search_paths(paths, context=None, ensure_one=True):
    """Replace WM search-path rows from a path list."""
    coll = _wm_search_paths(context)
    coll.clear()
    for path in _normalize_search_directories(paths):
        item = coll.add()
        item.path = path
    if ensure_one and len(coll) == 0:
        coll.add()


def _default_search_directories_from_prefs():
    """Load remap search roots from addon preferences."""
    from ..ui.preferences_ui import get_prefs_search_paths
    return _normalize_search_directories(get_prefs_search_paths())


def _search_files_worker(
    directories, progress_queue, found_blends, found_images, error_queue
):
    """Worker thread: walk roots for .blend and image/movie files (skip .dirs)."""
    try:
        valid_dirs = []
        for directory in directories or []:
            if not os.path.exists(directory):
                progress_queue.put(
                    ('status', f'Skipping missing directory: {directory}')
                )
                continue
            if not os.path.isdir(directory):
                progress_queue.put(
                    ('status', f'Skipping non-directory: {directory}')
                )
                continue
            if not os.access(directory, os.R_OK):
                progress_queue.put(
                    ('status', f'Skipping unreadable directory: {directory}')
                )
                continue
            valid_dirs.append(directory)

        if not valid_dirs:
            error_queue.put("No valid searchable directories")
            return

        total_files = 0
        scanned_dirs = 0

        # First pass: count blend + image files for progress
        try:
            for directory in valid_dirs:
                for root, dirs, files in os.walk(directory):
                    # Filter out directories starting with '.' (e.g., .git, .vscode)
                    dirs[:] = [d for d in dirs if not d.startswith('.')]
                    try:
                        for f in files:
                            if _is_blend_filename(f) or _is_image_filename(f):
                                total_files += 1
                        scanned_dirs += 1
                        if scanned_dirs % 10 == 0:
                            progress_queue.put((
                                'status',
                                f'Scanning directory structure... '
                                f'({scanned_dirs} directories)',
                            ))
                    except (PermissionError, OSError) as e:
                        config.debug_print(
                            f"[Atomic Debug] Cannot access {root}: {e}"
                        )
                        continue
        except Exception as e:
            error_queue.put(f"Error scanning directory structure: {str(e)}")
            return

        # Second pass: collect files
        found_count = 0
        try:
            for directory in valid_dirs:
                for root, dirs, files in os.walk(directory):
                    dirs[:] = [d for d in dirs if not d.startswith('.')]
                    try:
                        for file in files:
                            is_blend = _is_blend_filename(file)
                            is_image = _is_image_filename(file)
                            if not is_blend and not is_image:
                                continue
                            filepath = os.path.join(root, file)
                            try:
                                if not (
                                    os.path.isfile(filepath)
                                    and os.access(filepath, os.R_OK)
                                ):
                                    continue
                                if is_blend:
                                    found_blends.append(filepath)
                                else:
                                    found_images.append(filepath)
                                found_count += 1
                                if total_files > 0:
                                    progress = (
                                        found_count / total_files
                                    ) * 100.0
                                    progress_queue.put(('progress', progress))
                                    progress_queue.put((
                                        'status',
                                        f'Found {found_count}/'
                                        f'{total_files} files...',
                                    ))
                            except (OSError, PermissionError) as e:
                                config.debug_print(
                                    f"[Atomic Debug] Cannot access file "
                                    f"{filepath}: {e}"
                                )
                                continue
                    except (PermissionError, OSError) as e:
                        config.debug_print(
                            f"[Atomic Debug] Cannot access directory "
                            f"{root}: {e}"
                        )
                        continue
        except Exception as e:
            error_queue.put(f"Error collecting files: {str(e)}")
            return

        progress_queue.put(('complete', None))
    except PermissionError as e:
        error_queue.put(f"Permission denied: {str(e)}")
    except Exception as e:
        error_queue.put(f"Search error: {str(e)}")


def _process_library_search_step():
    """Timer callback to process missing-file search progress"""
    global _library_search_state

    atom = bpy.context.scene.atomic
    state = _library_search_state

    if not state['is_searching']:
        return None

    if atom.cancel_operation:
        if state['search_thread'] and state['search_thread'].is_alive():
            pass
        state['is_searching'] = False
        _safe_set_atom_property(atom, 'is_operation_running', False)
        _safe_set_atom_property(atom, 'operation_progress', 0.0)
        _safe_set_atom_property(atom, 'operation_status', "")
        config.debug_print("[Atomic Debug] File search cancelled")
        _tag_search_ui_redraw()
        return None

    if state['progress_queue']:
        try:
            while True:
                try:
                    update_type, value = state['progress_queue'].get_nowait()
                    if update_type == 'progress':
                        state['progress'] = value
                        _safe_set_atom_property(atom, 'operation_progress', value)
                        state['_dialog_needs_redraw'] = True
                    elif update_type == 'status':
                        state['status'] = value
                        _safe_set_atom_property(atom, 'operation_status', value)
                        state['_dialog_needs_redraw'] = True
                    elif update_type == 'complete':
                        state['search_complete'] = True
                        state['_dialog_needs_redraw'] = True
                except queue.Empty:
                    break
        except Exception as e:
            config.debug_print(
                f"[Atomic Error] Error processing progress queue: {e}"
            )

    if state.get('error_queue'):
        try:
            error_msg = state['error_queue'].get_nowait()
            state['search_error'] = error_msg
            state['is_searching'] = False
            _safe_set_atom_property(
                atom, 'operation_status', f"Error: {error_msg}"
            )

            def clear_error_progress():
                _safe_set_atom_property(atom, 'is_operation_running', False)
                _safe_set_atom_property(atom, 'operation_progress', 0.0)
                _safe_set_atom_property(atom, 'operation_status', "")
                _tag_search_ui_redraw()
                return None

            bpy.app.timers.register(clear_error_progress, first_interval=3.0)
            _tag_search_ui_redraw()
            return None
        except queue.Empty:
            pass

    if state['search_complete'] and (
        not state['search_thread'] or not state['search_thread'].is_alive()
    ):
        state['is_searching'] = False
        n_blend = len(state.get('found_blend_files') or [])
        n_img = len(state.get('found_image_files') or [])
        _safe_set_atom_property(atom, 'operation_progress', 100.0)
        _safe_set_atom_property(
            atom,
            'operation_status',
            f"Search complete! Found {n_blend} .blend, {n_img} image file(s)",
        )

        search_dirs = state.get('search_dirs') or []
        if search_dirs:
            _store_file_search_index(
                search_dirs,
                state.get('found_blend_files') or [],
                state.get('found_image_files') or [],
            )

        _match_libraries()
        _match_images()

        def clear_progress():
            # Progress already at 100 — end taskbar first, then zero the panel.
            _safe_set_atom_property(atom, 'is_operation_running', False)
            _safe_set_atom_property(atom, 'operation_progress', 0.0)
            _safe_set_atom_property(atom, 'operation_status', "")
            _tag_search_ui_redraw()
            return None

        if atom.is_operation_running:
            # Short hold at 100% so the bar can paint, then drop the taskbar.
            bpy.app.timers.register(clear_progress, first_interval=0.35)

        _tag_search_ui_redraw()
        return None

    state['_dialog_needs_redraw'] = True
    _tag_search_ui_redraw()
    return 0.1


def _basename_as_udim_template(name):
    """Replace first UDIM tile digits 1000-1099 with <UDIM>."""
    import re
    return re.sub(
        r'(?<!\d)(10[0-9]{2})(?!\d)',
        '<UDIM>',
        name,
        count=1,
        flags=re.IGNORECASE,
    )


def _path_as_udim_template(filepath):
    """Return path with basename tile digits replaced by <UDIM>."""
    import re
    directory = os.path.dirname(filepath)
    base = os.path.basename(filepath)
    templ = _basename_as_udim_template(base)
    templ = re.sub(r'(?i)<udim>', '<UDIM>', templ)
    return os.path.join(directory, templ)


def _udim_template_has_tile(template_path):
    """True if any tile 1001-1099 exists for a <UDIM> filepath template."""
    abs_tmpl = os.path.normpath(bpy.path.abspath(template_path))
    for tile in range(1001, 1100):
        if os.path.isfile(abs_tmpl.replace('<UDIM>', str(tile))):
            return True
    return False


def _match_entry(target_filename, found_files, equiv_map, udim=False):
    """
    Exact (incl. equiv / UDIM tile) then fuzzy substring match.

    Returns dict with exact, via_equivalent, candidates, selected_match, warnings.
    """
    target_filename = (target_filename or "").lower()
    acceptable = set(equiv_map.get(target_filename, {target_filename}))
    if udim:
        expanded = set(acceptable)
        for name in list(acceptable):
            if '<udim>' in name:
                for tile in range(1001, 1100):
                    expanded.add(name.replace('<udim>', str(tile)))
            else:
                tmpl = _basename_as_udim_template(name).lower()
                if '<udim>' in tmpl:
                    for tile in range(1001, 1100):
                        expanded.add(tmpl.replace('<udim>', str(tile)))
        acceptable = expanded

    exact_match = None
    via_equivalent = False
    candidates = []

    for filepath in found_files:
        filename = os.path.basename(filepath).lower()
        if filename in acceptable:
            exact_match = filepath
            via_equivalent = filename != target_filename
            if udim:
                exact_match = _path_as_udim_template(filepath)
                via_equivalent = (
                    os.path.basename(exact_match).lower() != target_filename
                    and filename != target_filename
                )
            break

    if not exact_match:
        target_fuzzy = (
            _basename_as_udim_template(target_filename).lower()
            if udim else target_filename
        )
        for filepath in found_files:
            filename = os.path.basename(filepath).lower()
            cmp_name = (
                _basename_as_udim_template(filename).lower() if udim else filename
            )
            if target_fuzzy in cmp_name or cmp_name in target_fuzzy:
                hit = _path_as_udim_template(filepath) if udim else filepath
                if hit not in candidates:
                    candidates.append(hit)

    selected = exact_match if exact_match else (
        candidates[0] if candidates else None
    )
    return {
        'exact': exact_match,
        'via_equivalent': via_equivalent,
        'candidates': candidates[:10],
        'warnings': [],
        'selected_match': selected,
    }


def _match_libraries():
    """Match missing libraries to found .blend files"""
    global _library_search_state

    from ..ui.preferences_ui import build_filename_equivalence_map

    missing_libs = missing.libraries()
    found_files = _library_search_state.get('found_blend_files') or []
    matches = {}
    equiv_map = build_filename_equivalence_map()

    for lib_key in missing_libs:
        lib_info = missing.get_missing_library_info(lib_key)
        if not lib_info:
            continue
        entry = _match_entry(lib_info['filename'], found_files, equiv_map)
        if entry['selected_match']:
            entry['warnings'] = _validate_replacement_library(
                lib_key, entry['selected_match'], lib_info
            )
        matches[lib_key] = entry

    _library_search_state['matches'] = matches


def _match_images():
    """Match missing images to found image/movie files"""
    global _library_search_state

    from ..ui.preferences_ui import build_filename_equivalence_map

    missing_imgs = missing.images()
    found_files = _library_search_state.get('found_image_files') or []
    matches = {}
    equiv_map = build_filename_equivalence_map()

    for img_key in missing_imgs:
        img_info = missing.get_missing_image_info(img_key)
        if not img_info:
            continue
        entry = _match_entry(
            img_info['filename'],
            found_files,
            equiv_map,
            udim=bool(img_info.get('is_udim')),
        )
        matches[img_key] = entry

    _library_search_state['image_matches'] = matches


def _validate_replacement_library(library_key, replacement_path, original_info):
    """Validate a replacement library and return warnings."""
    warnings = []
    if not replacement_path:
        warnings.append("No replacement path")
        return warnings
    if '<UDIM>' in replacement_path:
        if not _udim_template_has_tile(replacement_path):
            warnings.append(f"No UDIM tiles found for: {replacement_path}")
        return warnings
    if not os.path.exists(replacement_path):
        warnings.append(f"Replacement file does not exist: {replacement_path}")
        return warnings
    try:
        if not os.access(replacement_path, os.R_OK):
            warnings.append(f"Cannot read replacement file: {replacement_path}")
    except Exception as e:
        warnings.append(f"Could not validate library: {str(e)}")
    return warnings


def _image_filepath_exists(image):
    """True when image filepath resolves to an existing file (or UDIM tile)."""
    try:
        fp = image.filepath or ""
        if not fp:
            return False
        abs_path = os.path.normpath(bpy.path.abspath(fp))
        if '<UDIM>' in abs_path:
            return _udim_template_has_tile(abs_path)
        return os.path.isfile(abs_path)
    except Exception:
        return False


def _library_filepath_prefer_relative(abs_path):
    """
    Prefer a blend-relative ``//`` path for library/image filepath (#23).

    Falls back to the absolute path when the blend is unsaved or the hit sits
    on a different drive/root anchor (Windows ``ValueError`` from relpath).
    """
    abs_path = os.path.normpath(abs_path)
    if not bpy.data.filepath:
        return abs_path
    try:
        return bpy.path.relpath(abs_path)
    except (ValueError, OSError, TypeError, RuntimeError):
        return abs_path


def _library_filepath_exists(library):
    """True when the library's resolved filepath is an existing file on disk."""
    try:
        fp = library.filepath or ""
        if not fp:
            return False
        return os.path.isfile(os.path.normpath(bpy.path.abspath(fp)))
    except Exception:
        return False


def _record_relink_equivalent(old_basename, abs_path):
    """Learn rename into permanent prefs map (no-op if same basename)."""
    try:
        from ..ui.preferences_ui import record_filename_equivalent
        record_filename_equivalent(old_basename, abs_path)
    except Exception as e:
        config.debug_print(
            f"[Atomic Debug] record_filename_equivalent skipped: {e}"
        )


def _relink_library(library_key, new_filepath, use_relative_path=True):
    """Relink a library to a new filepath.

    Args:
        library_key: Key of the library to relink
        new_filepath: New filepath (absolute or blend-relative)
        use_relative_path: If True (default), prefer ``//`` relative when the
            blend and hit share an anchor; otherwise store an absolute path.
            If False, always store absolute.

    Returns:
        (success, message). Success means the library is no longer missing
        (path points at an existing .blend), even if reload() warned — detect
        only checks filepath existence.
    """
    if library_key not in bpy.data.libraries:
        # Mid-batch sibling reloads can merge/rename Library IDs.
        return False, "Library not found"

    library = bpy.data.libraries[library_key]

    try:
        if not new_filepath:
            return False, "No filepath provided"

        abs_path = os.path.normpath(bpy.path.abspath(new_filepath))

        if not os.path.exists(abs_path):
            return False, f"File does not exist: {abs_path}"

        if not os.path.isfile(abs_path):
            return False, f"Path is not a file: {abs_path}"

        if not abs_path.lower().endswith('.blend'):
            return False, f"File is not a .blend file: {abs_path}"

        if not os.access(abs_path, os.R_OK):
            return False, f"Cannot read file (permission denied): {abs_path}"

        # Capture old basename before filepath rewrite (for permanent map)
        try:
            old_fp = library.filepath or ""
            old_basename = os.path.basename(
                bpy.path.abspath(old_fp) if old_fp else library_key
            )
        except Exception:
            old_basename = os.path.basename(library_key) if library_key else ""

        try:
            if use_relative_path:
                library.filepath = _library_filepath_prefer_relative(abs_path)
            else:
                library.filepath = abs_path
        except Exception as e:
            return False, f"Error setting library filepath: {str(e)}"

        try:
            library.reload()
        except Exception as e:
            # Path is already written; detect_missing only checks isfile.
            if _library_filepath_exists(library):
                config.debug_print(
                    f"[Atomic Debug] Library '{library_key}' path ok after "
                    f"reload warning: {e}"
                )
                _record_relink_equivalent(old_basename, abs_path)
                return True, f"Path updated (reload warned: {e})"
            return False, f"Library filepath updated but reload failed: {str(e)}"

        _record_relink_equivalent(old_basename, abs_path)
        return True, "Library relinked successfully"
    except Exception as e:
        return False, f"Error relinking library: {str(e)}"


def _relink_library_batch_result(library_key, filepath):
    """
    Relink one library for Relink All.

    Treats mid-batch "Library not found" as resolved when the ID is gone
    (sibling reload merged it) — matches detect_missing afterwards.
    """
    success, message = _relink_library(library_key, filepath)
    if success:
        return True, message
    if (
        message == "Library not found"
        and library_key not in bpy.data.libraries
    ):
        config.debug_print(
            f"[Atomic Debug] Relink All: '{library_key}' already gone "
            f"(resolved by sibling reload)"
        )
        return True, "Already resolved (library ID gone)"
    return False, message


def _relink_image(image_key, new_filepath, use_relative_path=True):
    """Relink an image datablock to a new filepath (file, sequence frame, or UDIM)."""
    if image_key not in bpy.data.images:
        return False, "Image not found"

    image = bpy.data.images[image_key]

    try:
        if not new_filepath:
            return False, "No filepath provided"

        # UDIM templates keep <UDIM> in the stored path
        is_udim = '<UDIM>' in new_filepath
        if is_udim:
            abs_path = os.path.normpath(bpy.path.abspath(new_filepath))
            if not _udim_template_has_tile(abs_path):
                return False, f"No UDIM tiles found for: {abs_path}"
            store_path = abs_path
        else:
            abs_path = os.path.normpath(bpy.path.abspath(new_filepath))
            if not os.path.exists(abs_path):
                return False, f"File does not exist: {abs_path}"
            if not os.path.isfile(abs_path):
                return False, f"Path is not a file: {abs_path}"
            if not os.access(abs_path, os.R_OK):
                return False, f"Cannot read file (permission denied): {abs_path}"
            store_path = abs_path

        try:
            old_fp = image.filepath or ""
            old_basename = os.path.basename(
                bpy.path.abspath(old_fp) if old_fp else image_key
            )
        except Exception:
            old_basename = os.path.basename(image_key) if image_key else ""

        try:
            if use_relative_path:
                # Prefer relative for non-UDIM; UDIM templates need the token kept
                if is_udim and bpy.data.filepath:
                    try:
                        # Relpath of a tile, then restore <UDIM> in basename
                        tile_probe = store_path.replace('<UDIM>', '1001')
                        rel = bpy.path.relpath(tile_probe)
                        image.filepath = _path_as_udim_template(rel)
                    except (ValueError, OSError, TypeError, RuntimeError):
                        image.filepath = store_path
                else:
                    image.filepath = _library_filepath_prefer_relative(store_path)
            else:
                image.filepath = store_path
        except Exception as e:
            return False, f"Error setting image filepath: {str(e)}"

        try:
            image.reload()
        except Exception as e:
            if _image_filepath_exists(image):
                config.debug_print(
                    f"[Atomic Debug] Image '{image_key}' path ok after "
                    f"reload warning: {e}"
                )
                _record_relink_equivalent(old_basename, store_path)
                return True, f"Path updated (reload warned: {e})"
            return False, f"Image filepath updated but reload failed: {str(e)}"

        _record_relink_equivalent(old_basename, store_path)
        return True, "Image relinked successfully"
    except Exception as e:
        return False, f"Error relinking image: {str(e)}"


def _relink_image_batch_result(image_key, filepath):
    """Relink one image for Relink All; treat vanished IDs as resolved."""
    success, message = _relink_image(image_key, filepath)
    if success:
        return True, message
    if message == "Image not found" and image_key not in bpy.data.images:
        return True, "Already resolved (image ID gone)"
    return False, message


def _safe_set_atom_property(atom, prop_name, value):
    """
    Safely set an atom property, catching errors when Blender is in read-only state.

    Also mirrors ``is_operation_running`` / ``operation_progress`` onto the OS
    taskbar (same path as Smart Select / Clean via ``utils.wm_progress``).
    """
    if atom is None:
        return False

    was_running = None
    if prop_name == 'is_operation_running':
        try:
            was_running = bool(getattr(atom, 'is_operation_running', False))
        except (AttributeError, ReferenceError):
            was_running = False

    try:
        setattr(atom, prop_name, value)
    except (AttributeError, RuntimeError) as e:
        config.debug_print(f"[Atomic Debug] Could not set {prop_name}: {e}")
        return False
    except Exception as e:
        config.debug_print(f"[Atomic Debug] Unexpected error setting {prop_name}: {e}")
        return False

    try:
        if prop_name == 'is_operation_running':
            if value and not was_running:
                # Always arm at 0 — leftover progress is often 100 from last run.
                wm_progress.begin()
                wm_progress.set_progress(0.0)
            elif not value and was_running:
                wm_progress.end()
        elif prop_name == 'operation_progress':
            try:
                if not getattr(atom, 'is_operation_running', False):
                    return True
            except (AttributeError, ReferenceError):
                return True
            try:
                percent = float(atom.operation_progress)
            except (AttributeError, TypeError, ValueError):
                percent = value
            wm_progress.set_progress(percent)
    except Exception as e:
        config.debug_print(f"[Atomic Debug] wm_progress sync failed: {e}")

    return True


# Atomic Data Manager Search for Missing Files Operator
class ATOMIC_OT_search_missing(bpy.types.Operator):
    """Search one or more directories for missing library and image files"""
    bl_idname = "atomic.search_missing"
    bl_label = "Search for Missing Files"
    bl_options = {'REGISTER', 'UNDO'}

    # Selected matches for libraries with multiple candidates
    selected_matches: bpy.props.StringProperty(default="")  # JSON-like storage

    def draw(self, context):
        layout = self.layout
        global _library_search_state

        # Capture popup region so the search timer can tag_refresh_ui (#22)
        _remember_search_popup_region(context)

        from ..ui.preferences_ui import (
            draw_remap_search_path_list,
            draw_remap_filename_equivalent_list,
            _get_addon_prefs,
        )

        state = _library_search_state
        atom = context.scene.atomic
        directories = _normalize_search_directories(_wm_search_path_strings(context))

        # Multi-path list — same row UI as addon prefs
        box = layout.box()
        box.label(text="Search Directories:", icon='FILE_FOLDER')
        draw_remap_search_path_list(
            box,
            _wm_search_paths(context),
            add_idname="atomic.search_missing_path_add",
            remove_idname="atomic.search_missing_path_remove",
        )

        row = box.row(align=True)
        row.operator(
            "atomic.search_missing_path_save_defaults",
            text="Save as Defaults",
            icon='PREFERENCES',
        )
        row.operator(
            "atomic.search_missing_path_load_defaults",
            text="Load Defaults",
            icon='FILE_REFRESH',
        )

        # Filename hit equivalents — edits prefs directly (same list as addon prefs)
        prefs = _get_addon_prefs()
        if prefs and hasattr(prefs, "remap_filename_equivalents"):
            box = layout.box()
            box.label(text="Filename Hit Equivalents:", icon='FILE_TICK')
            draw_remap_filename_equivalent_list(
                box,
                prefs.remap_filename_equivalents,
                add_idname="atomic.remap_prefs_equiv_add",
                remove_idname="atomic.remap_prefs_equiv_remove",
            )

        # Search button — relink prefers // relative, falls back to absolute (#23)
        if not state['is_searching']:
            row = layout.row()
            valid = [
                d for d in directories
                if os.path.isdir(d) and os.access(d, os.R_OK)
            ]
            if valid:
                row.operator("atomic.search_missing_start", text="Start Search")
            elif directories:
                row.label(
                    text="Please select valid, readable directories",
                    icon='ERROR',
                )
            else:
                row.label(text="Please add at least one directory", icon='INFO')

            # Session index status + clear (independent of Smart Select dirty)
            idx = _file_search_index()
            n_b = len(idx.get('blends') or [])
            n_i = len(idx.get('images') or [])
            idx_row = layout.row(align=True)
            if n_b or n_i:
                idx_row.label(
                    text=f"Search index: {n_b} .blend, {n_i} image file(s)",
                    icon='DOCUMENTS',
                )
            else:
                idx_row.label(text="Search index: empty", icon='DOCUMENTS')
            idx_row.operator(
                "atomic.search_missing_clear_index",
                text="Clear Search Index",
                icon='TRASH',
            )
        else:
            # Display-only loading bar (not an editable slider).
            factor = max(0.0, min(1.0, float(atom.operation_progress) / 100.0))
            layout.progress(
                factor=factor,
                type='BAR',
                text=f"Progress {int(round(factor * 100.0))}%",
            )

            if atom.operation_status:
                row = layout.row()
                row.label(text=atom.operation_status, icon='TIME')

            row = layout.row()
            row.operator("atomic.search_missing_cancel", text="Cancel Search")

        # Results display
        if state['search_complete'] and not state['is_searching']:
            missing_libs = missing.libraries()
            missing_imgs = missing.images()
            matches = state.get('matches', {})
            image_matches = state.get('image_matches', {})

            if not missing_libs and not missing_imgs:
                row = layout.row()
                row.label(text="No missing files found!", icon='INFO')
                return

            def _draw_match_box(box, item_key, kind, match_info, display_name, icon):
                row = box.row()
                row.label(text=display_name, icon=icon)

                exact_match = match_info.get('exact')
                via_equivalent = match_info.get('via_equivalent', False)
                candidates = match_info.get('candidates', [])
                warnings = match_info.get('warnings', [])
                selected_match = match_info.get('selected_match')
                found_any = bool(
                    state.get('found_blend_files')
                    if kind == 'LIBRARY'
                    else state.get('found_image_files')
                )

                if exact_match:
                    row = box.row()
                    if via_equivalent:
                        row.label(
                            text=(
                                f"✓ Equivalent match: "
                                f"{os.path.basename(exact_match)}"
                            ),
                            icon='CHECKMARK',
                        )
                    else:
                        row.label(
                            text=(
                                f"✓ Exact match: "
                                f"{os.path.basename(exact_match)}"
                            ),
                            icon='CHECKMARK',
                        )
                elif candidates:
                    row = box.row()
                    row.label(
                        text=f"Found {len(candidates)} candidate(s)",
                        icon='QUESTION',
                    )
                    if len(candidates) > 1:
                        row = box.row()
                        row.label(text="Select match:")
                        for candidate in candidates[:5]:
                            candidate_name = os.path.basename(candidate)
                            if len(candidate_name) > 40:
                                candidate_name = candidate_name[:37] + "..."
                            op = row.operator(
                                "atomic.search_missing_select",
                                text=candidate_name,
                            )
                            op.kind = kind
                            op.item_key = item_key
                            op.filepath = candidate
                elif found_any:
                    row = box.row()
                    row.label(text="No match found", icon='ERROR')
                else:
                    row = box.row()
                    row.label(
                        text="No matching files found in search directories",
                        icon='INFO',
                    )

                for warning in warnings:
                    row = box.row()
                    row.label(text=f"⚠ {warning}", icon='ERROR')

                if selected_match:
                    row = box.row()
                    label = (
                        "Relink (Ignore Warnings)" if warnings else "Relink"
                    )
                    op = row.operator(
                        "atomic.search_missing_relink",
                        text=label,
                    )
                    op.kind = kind
                    op.item_key = item_key
                    op.filepath = selected_match
                    op.ignore_warnings = bool(warnings)

            if missing_libs:
                layout.separator()
                row = layout.row()
                row.label(text="Missing Libraries:", icon='LIBRARY_DATA_DIRECT')
                for lib_key in missing_libs:
                    box = layout.box()
                    lib_info = missing.get_missing_library_info(lib_key)
                    lib_name = lib_info['filename'] if lib_info else lib_key
                    _draw_match_box(
                        box,
                        lib_key,
                        'LIBRARY',
                        matches.get(lib_key, {}),
                        lib_name,
                        'LIBRARY_DATA_DIRECT',
                    )

            if missing_imgs:
                layout.separator()
                row = layout.row()
                row.label(text="Missing Images:", icon='IMAGE_DATA')
                for img_key in missing_imgs:
                    box = layout.box()
                    img_info = missing.get_missing_image_info(img_key)
                    img_name = img_info['filename'] if img_info else img_key
                    _draw_match_box(
                        box,
                        img_key,
                        'IMAGE',
                        image_matches.get(img_key, {}),
                        img_name,
                        'IMAGE_DATA',
                    )

            relinkable_libs = [
                k for k in missing_libs
                if (matches.get(k) or {}).get('selected_match')
                or (matches.get(k) or {}).get('exact')
            ]
            relinkable_imgs = [
                k for k in missing_imgs
                if (image_matches.get(k) or {}).get('selected_match')
                or (image_matches.get(k) or {}).get('exact')
            ]
            if relinkable_libs or relinkable_imgs:
                layout.separator()
                row = layout.row()
                row.operator(
                    "atomic.search_missing_relink_all",
                    text="Relink All",
                    icon='LINKED',
                )

        # Error display
        if state.get('search_error'):
            layout.separator()
            row = layout.row()
            row.label(text=f"Error: {state['search_error']}", icon='ERROR')

    def _get_selected_match(self, library_key):
        """Get the currently selected match for a library"""
        global _library_search_state
        matches = _library_search_state.get('matches', {})
        match_info = matches.get(library_key, {})
        return match_info.get('selected_match')

    def check(self, context):
        """Ask Blender to redraw the props dialog when search state changes."""
        # Returning True signals the dialog to redraw (Operator.check docs).
        # Only when flagged, to avoid a draw/check spin while searching.
        return bool(_library_search_state.pop('_dialog_needs_redraw', False))

    def execute(self, context):
        return {'FINISHED'}

    def invoke(self, context, event):
        global _library_search_state

        # Reset dialog/match state only — keep Blender-session file search index
        _search_popup_regions.clear()
        _library_search_state = {
            'is_searching': False,
            'found_blend_files': [],
            'found_image_files': [],
            'matches': {},
            'image_matches': {},
            'progress': 0.0,
            'status': '',
            'search_thread': None,
            'progress_queue': None,
            'search_complete': False,
            'search_error': None
        }
        _fill_wm_search_paths(
            _default_search_directories_from_prefs(),
            context,
            ensure_one=True,
        )

        wm = context.window_manager
        return wm.invoke_props_dialog(self, width=600)


class ATOMIC_OT_search_missing_path_add(bpy.types.Operator):
    """Add a folder row to the multi-path library search list"""
    bl_idname = "atomic.search_missing_path_add"
    bl_label = "Add Search Directory"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        _wm_search_paths(context).add()
        clear_blend_search_index()
        for area in context.screen.areas:
            area.tag_redraw()
        return {'FINISHED'}


class ATOMIC_OT_search_missing_path_remove(bpy.types.Operator):
    """Remove a folder row from the multi-path library search list"""
    bl_idname = "atomic.search_missing_path_remove"
    bl_label = "Remove Search Directory"
    bl_options = {'INTERNAL'}

    index: bpy.props.IntProperty(default=0)

    def execute(self, context):
        coll = _wm_search_paths(context)
        if len(coll) <= 1:
            return {'CANCELLED'}
        if 0 <= self.index < len(coll):
            coll.remove(self.index)
            clear_blend_search_index()
        for area in context.screen.areas:
            area.tag_redraw()
        return {'FINISHED'}


class ATOMIC_OT_search_missing_path_save_defaults(bpy.types.Operator):
    """Save the current search directories as addon preference defaults"""
    bl_idname = "atomic.search_missing_path_save_defaults"
    bl_label = "Save Search Defaults"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        from ..ui.preferences_ui import (
            set_prefs_search_paths,
            copy_prefs_to_config,
            _save_after_pref_change,
        )
        dirs = _normalize_search_directories(_wm_search_path_strings(context))
        if not set_prefs_search_paths(dirs):
            self.report({'ERROR'}, "Could not find Atomic preferences")
            return {'CANCELLED'}
        copy_prefs_to_config(None, None)
        _save_after_pref_change()
        clear_blend_search_index()
        self.report(
            {'INFO'},
            f"Saved {len(dirs)} search root{'s' if len(dirs) != 1 else ''} "
            f"as defaults",
        )
        return {'FINISHED'}


class ATOMIC_OT_search_missing_path_load_defaults(bpy.types.Operator):
    """Reload search directories from addon preference defaults"""
    bl_idname = "atomic.search_missing_path_load_defaults"
    bl_label = "Load Search Defaults"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        defaults = _default_search_directories_from_prefs()
        _fill_wm_search_paths(defaults, context, ensure_one=True)
        clear_blend_search_index()
        for area in context.screen.areas:
            area.tag_redraw()
        count = len(defaults)
        self.report(
            {'INFO'},
            f"Loaded {count} default search root{'s' if count != 1 else ''}",
        )
        return {'FINISHED'}


class ATOMIC_OT_search_missing_clear_index(bpy.types.Operator):
    """Clear the session Search Missing file index"""
    bl_idname = "atomic.search_missing_clear_index"
    bl_label = "Clear Search Index"
    bl_description = (
        "Forget cached .blend and image paths from Search Missing. The next "
        "Start Search will re-walk directories. Does not clear Smart Select "
        "unused caches"
    )
    bl_options = {'INTERNAL'}

    def execute(self, context):
        clear_blend_search_index()
        self.report({'INFO'}, "Search index cleared")
        _tag_search_ui_redraw()
        for area in context.screen.areas:
            area.tag_redraw()
        return {'FINISHED'}


# Operator to start the search
class ATOMIC_OT_search_missing_start(bpy.types.Operator):
    """Start searching for missing library and image files"""
    bl_idname = "atomic.search_missing_start"
    bl_label = "Start Search"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        global _library_search_state

        search_dirs = _normalize_search_directories(
            _wm_search_path_strings(context)
        )

        valid = [
            d for d in search_dirs
            if os.path.isdir(d) and os.access(d, os.R_OK)
        ]
        if not valid:
            self.report(
                {'ERROR'},
                "Please add at least one valid, readable search directory",
            )
            return {'CANCELLED'}

        if _library_search_state.get('is_searching', False):
            self.report({'WARNING'}, "Search is already in progress")
            return {'CANCELLED'}

        atom = context.scene.atomic

        cached = _cached_files_for_roots(valid)
        if cached is not None:
            blends, images = cached
            _library_search_state['is_searching'] = False
            _library_search_state['found_blend_files'] = blends
            _library_search_state['found_image_files'] = images
            _library_search_state['matches'] = {}
            _library_search_state['image_matches'] = {}
            _library_search_state['progress'] = 100.0
            _library_search_state['status'] = (
                f'Using session index ({len(blends)} .blend, '
                f'{len(images)} image)...'
            )
            _library_search_state['search_complete'] = True
            _library_search_state['search_error'] = None
            _library_search_state['search_dirs'] = list(valid)
            _library_search_state['search_thread'] = None
            _library_search_state['progress_queue'] = None
            _match_libraries()
            _match_images()
            _safe_set_atom_property(atom, 'is_operation_running', False)
            _safe_set_atom_property(atom, 'operation_progress', 0.0)
            _safe_set_atom_property(atom, 'operation_status', "")
            _safe_set_atom_property(atom, 'cancel_operation', False)
            _library_search_state['_dialog_needs_redraw'] = True
            _tag_search_ui_redraw()
            self.report(
                {'INFO'},
                f"Matched from session index "
                f"({len(blends)} .blend, {len(images)} image)",
            )
            return {'FINISHED'}

        _library_search_state['is_searching'] = True
        _library_search_state['found_blend_files'] = []
        _library_search_state['found_image_files'] = []
        _library_search_state['matches'] = {}
        _library_search_state['image_matches'] = {}
        _library_search_state['progress'] = 0.0
        _library_search_state['status'] = (
            f'Initializing search across {len(valid)} director'
            f'{"y" if len(valid) == 1 else "ies"}...'
        )
        _library_search_state['search_complete'] = False
        _library_search_state['search_error'] = None
        _library_search_state['search_dirs'] = list(valid)

        _safe_set_atom_property(atom, 'is_operation_running', True)
        _safe_set_atom_property(atom, 'operation_progress', 0.0)
        _safe_set_atom_property(
            atom, 'operation_status', _library_search_state['status']
        )
        _safe_set_atom_property(atom, 'cancel_operation', False)

        progress_queue = queue.Queue()
        error_queue = queue.Queue()
        _library_search_state['progress_queue'] = progress_queue
        _library_search_state['error_queue'] = error_queue

        try:
            search_thread = threading.Thread(
                target=_search_files_worker,
                args=(
                    valid,
                    progress_queue,
                    _library_search_state['found_blend_files'],
                    _library_search_state['found_image_files'],
                    error_queue,
                ),
                daemon=True,
            )
            search_thread.start()
            _library_search_state['search_thread'] = search_thread
        except Exception as e:
            _library_search_state['is_searching'] = False
            _safe_set_atom_property(atom, 'is_operation_running', False)
            self.report({'ERROR'}, f"Failed to start search thread: {str(e)}")
            return {'CANCELLED'}

        try:
            bpy.app.timers.register(_process_library_search_step)
        except Exception as e:
            config.debug_print(f"[Atomic Error] Failed to register timer: {e}")
            _library_search_state['is_searching'] = False
            _safe_set_atom_property(atom, 'is_operation_running', False)
            self.report({'ERROR'}, f"Failed to start progress timer: {str(e)}")
            return {'CANCELLED'}

        _library_search_state['_dialog_needs_redraw'] = True
        _tag_search_ui_redraw()

        return {'FINISHED'}


class ATOMIC_OT_search_missing_cancel(bpy.types.Operator):
    """Cancel the library search"""
    bl_idname = "atomic.search_missing_cancel"
    bl_label = "Cancel Search"
    bl_options = {'INTERNAL'}
    
    def execute(self, context):
        global _library_search_state
        
        atom = context.scene.atomic
        _safe_set_atom_property(atom, 'cancel_operation', True)
        _safe_set_atom_property(atom, 'operation_status', 'Cancelling...')
        
        # The timer will handle the cancellation
        return {'FINISHED'}


# Operator to select a match for a library
class ATOMIC_OT_search_missing_select(bpy.types.Operator):
    """Select a match for a missing library or image"""
    bl_idname = "atomic.search_missing_select"
    bl_label = "Select Match"
    bl_options = {'INTERNAL'}

    kind: bpy.props.EnumProperty(
        items=(
            ('LIBRARY', 'Library', ''),
            ('IMAGE', 'Image', ''),
        ),
        default='LIBRARY',
    )
    item_key: bpy.props.StringProperty()
    filepath: bpy.props.StringProperty()

    def execute(self, context):
        global _library_search_state

        if self.kind == 'IMAGE':
            matches = _library_search_state.setdefault('image_matches', {})
            if self.item_key in matches:
                matches[self.item_key]['selected_match'] = self.filepath
        else:
            matches = _library_search_state.get('matches', {})
            if self.item_key in matches:
                matches[self.item_key]['selected_match'] = self.filepath
                lib_info = missing.get_missing_library_info(self.item_key)
                if lib_info:
                    warnings = _validate_replacement_library(
                        self.item_key, self.filepath, lib_info
                    )
                    matches[self.item_key]['warnings'] = warnings

        for area in context.screen.areas:
            area.tag_redraw()

        return {'FINISHED'}


class ATOMIC_OT_search_missing_relink(bpy.types.Operator):
    """Relink a library or image to the selected file"""
    bl_idname = "atomic.search_missing_relink"
    bl_label = "Relink"
    bl_options = {'INTERNAL', 'UNDO'}

    kind: bpy.props.EnumProperty(
        items=(
            ('LIBRARY', 'Library', ''),
            ('IMAGE', 'Image', ''),
        ),
        default='LIBRARY',
    )
    item_key: bpy.props.StringProperty()
    filepath: bpy.props.StringProperty()
    ignore_warnings: bpy.props.BoolProperty(default=False)
    # Legacy prop kept so older draw paths do not explode if still set
    library_key: bpy.props.StringProperty()

    def execute(self, context):
        global _library_search_state
        key = self.item_key or self.library_key
        if self.kind == 'IMAGE':
            success, message = _relink_image(key, self.filepath)
            label = "Image"
            bucket = 'image_matches'
        else:
            success, message = _relink_library(key, self.filepath)
            label = "Library"
            bucket = 'matches'

        if success:
            self.report({'INFO'}, f"{label} relinked: {message}")
            _library_search_state.get(bucket, {}).pop(key, None)
            atom = context.scene.atomic
            if atom.is_operation_running:
                _safe_set_atom_property(atom, 'is_operation_running', False)
                _safe_set_atom_property(atom, 'operation_progress', 0.0)
                _safe_set_atom_property(atom, 'operation_status', "")
        else:
            self.report({'ERROR'}, message)

        for area in context.screen.areas:
            area.tag_redraw()

        return {'FINISHED'}


class ATOMIC_OT_search_missing_relink_all(bpy.types.Operator):
    """Relink all missing libraries and images that have a match selected"""
    bl_idname = "atomic.search_missing_relink_all"
    bl_label = "Relink All"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        global _library_search_state
        matches = _library_search_state.get('matches', {})
        image_matches = _library_search_state.get('image_matches', {})
        to_relink = []
        for lib_key, match_info in matches.items():
            path = match_info.get('selected_match') or match_info.get('exact')
            if path and path.strip():
                to_relink.append(('LIBRARY', lib_key, path))
        for img_key, match_info in image_matches.items():
            path = match_info.get('selected_match') or match_info.get('exact')
            if path and path.strip():
                to_relink.append(('IMAGE', img_key, path))
        if not to_relink:
            self.report({'WARNING'}, "No matches to relink")
            return {'CANCELLED'}
        ok, fail = 0, 0
        fail_details = []
        for kind, key, filepath in to_relink:
            if kind == 'IMAGE':
                success, message = _relink_image_batch_result(key, filepath)
                bucket = 'image_matches'
            else:
                success, message = _relink_library_batch_result(key, filepath)
                bucket = 'matches'
            if success:
                ok += 1
                _library_search_state.get(bucket, {}).pop(key, None)
            else:
                fail += 1
                fail_details.append(f"{key}: {message}")
        if ok:
            self.report(
                {'INFO'},
                f"Relinked {ok} file{'s' if ok != 1 else ''}"
                + (f", {fail} failed" if fail else ""),
            )
        if fail:
            self.report(
                {'ERROR'},
                f"{fail} file{'s' if fail != 1 else ''} failed to relink"
                + (f" — {fail_details[0]}" if fail_details else ""),
            )
            for detail in fail_details[1:]:
                self.report({'ERROR'}, detail)
        atom = context.scene.atomic
        if atom.is_operation_running:
            _safe_set_atom_property(atom, 'is_operation_running', False)
            _safe_set_atom_property(atom, 'operation_progress', 0.0)
            _safe_set_atom_property(atom, 'operation_status', "")
        for area in context.screen.areas:
            area.tag_redraw()
        return {'FINISHED'}


# Module-level state for replace missing
# Keys are "LIBRARY:<name>" / "IMAGE:<name>"
_replace_missing_state = {}


def _replace_state_key(kind, item_key):
    """Stable state key for a replace-dialog row."""
    return f"{kind}:{item_key}"


def _replace_path_item_update(self, context):
    """Sync path to global state so browse/relink ops and redraws see it."""
    global _replace_missing_state
    if getattr(self, "_atomic_normalizing_path", False):
        pass
    else:
        raw = (self.path or "").strip()
        if raw:
            try:
                abs_path = bpy.path.abspath(raw)
                if os.path.isdir(abs_path) and not raw.endswith(("/", "\\")):
                    self._atomic_normalizing_path = True
                    self.path = raw + os.sep
            except Exception:
                pass
            finally:
                if hasattr(self, "_atomic_normalizing_path"):
                    self._atomic_normalizing_path = False

    key = _replace_state_key(self.kind, self.item_key or self.lib_key)
    if self.item_key or self.lib_key:
        _replace_missing_state[key] = self.path
    for area in context.screen.areas:
        area.tag_redraw()


class ATOMIC_PG_replace_path_item(bpy.types.PropertyGroup):
    """One replacement path per missing file so each text field is stable."""
    kind: bpy.props.EnumProperty(
        items=(
            ('LIBRARY', 'Library', ''),
            ('IMAGE', 'Image', ''),
        ),
        default='LIBRARY',
    )
    item_key: bpy.props.StringProperty()
    # Legacy alias used by older callers
    lib_key: bpy.props.StringProperty()
    path: bpy.props.StringProperty(
        name="Path",
        description="Path to the replacement file",
        subtype='FILE_PATH',
        default="",
        update=_replace_path_item_update
    )


class ATOMIC_OT_replace_missing(bpy.types.Operator):
    """Replace each missing file with a new file"""
    bl_idname = "atomic.replace_missing"
    bl_label = "Replace Missing Files"

    replace_paths: bpy.props.CollectionProperty(type=ATOMIC_PG_replace_path_item)

    def draw(self, context):
        layout = self.layout
        global _replace_missing_state

        missing_libs = missing.libraries()
        missing_imgs = missing.images()

        if not missing_libs and not missing_imgs:
            row = layout.row()
            row.label(text="No missing files found!", icon='INFO')
            return

        row = layout.row()
        row.label(
            text="Select replacement files for missing libraries and images:",
            icon='FILEBROWSER',
        )
        layout.separator()

        def _draw_row(item, icon_broken, relink_label):
            box = layout.box()
            row = box.row()
            row.label(text="", icon=icon_broken)
            if item.kind == 'LIBRARY':
                info = missing.get_missing_library_info(item.item_key)
            else:
                info = missing.get_missing_image_info(item.item_key)
            path_display = (info or {}).get('filepath') or item.item_key
            row.label(text=path_display)

            row = box.row()
            row.separator()
            path_row = row.row(align=True)
            path_row.prop(item, 'path', text="")

            if item.path:
                row = box.row()
                row.separator()
                relink_op = row.operator(
                    "atomic.replace_missing_relink",
                    text=relink_label,
                    icon='LINKED',
                )
                relink_op.kind = item.kind
                relink_op.item_key = item.item_key
                relink_op.filepath = item.path

        for item in self.replace_paths:
            if item.kind == 'LIBRARY':
                _draw_row(item, 'LIBRARY_DATA_BROKEN', "Relink Library")
            else:
                _draw_row(item, 'IMAGE_DATA', "Relink Image")

        active_keys = {
            _replace_state_key(it.kind, it.item_key) for it in self.replace_paths
        }
        if any(_replace_missing_state.get(k) for k in active_keys):
            layout.separator()
            row = layout.row()
            row.operator(
                "atomic.replace_missing_relink_all",
                text="Relink All",
                icon='LINKED',
            )

    def execute(self, context):
        return {'FINISHED'}

    def invoke(self, context, event):
        global _replace_missing_state
        missing_libs = missing.libraries()
        missing_imgs = missing.images()

        valid_keys = set()
        for k in missing_libs:
            valid_keys.add(_replace_state_key('LIBRARY', k))
        for k in missing_imgs:
            valid_keys.add(_replace_state_key('IMAGE', k))
        _replace_missing_state = {
            k: v for k, v in _replace_missing_state.items() if k in valid_keys
        }

        if not missing_libs and not missing_imgs:
            self.report({'INFO'}, "No missing files found")
            return {'CANCELLED'}

        self.replace_paths.clear()
        for lib_key in missing_libs:
            lib_info = missing.get_missing_library_info(lib_key)
            path_item = self.replace_paths.add()
            path_item.kind = 'LIBRARY'
            path_item.item_key = lib_key
            path_item.lib_key = lib_key
            sk = _replace_state_key('LIBRARY', lib_key)
            existing = _replace_missing_state.get(sk, "")
            path_item.path = existing or (
                lib_info.get('filename', "") if lib_info else ""
            )

        for img_key in missing_imgs:
            img_info = missing.get_missing_image_info(img_key)
            path_item = self.replace_paths.add()
            path_item.kind = 'IMAGE'
            path_item.item_key = img_key
            path_item.lib_key = img_key
            sk = _replace_state_key('IMAGE', img_key)
            existing = _replace_missing_state.get(sk, "")
            path_item.path = existing or (
                img_info.get('filename', "") if img_info else ""
            )

        wm = context.window_manager
        return wm.invoke_props_dialog(self, width=800)


class ATOMIC_OT_replace_missing_set_path(bpy.types.Operator):
    """Set replacement path for a missing file"""
    bl_idname = "atomic.replace_missing_set_path"
    bl_label = "Set Replacement Path"
    bl_options = {'INTERNAL'}

    kind: bpy.props.EnumProperty(
        items=(
            ('LIBRARY', 'Library', ''),
            ('IMAGE', 'Image', ''),
        ),
        default='LIBRARY',
    )
    item_key: bpy.props.StringProperty()
    library_key: bpy.props.StringProperty()
    filepath: bpy.props.StringProperty(
        name="File Path",
        description="Path to the replacement file",
        subtype='FILE_PATH',
        default="",
    )

    def execute(self, context):
        global _replace_missing_state
        key = self.item_key or self.library_key
        if self.filepath and key:
            _replace_missing_state[_replace_state_key(self.kind, key)] = (
                self.filepath
            )
            for area in context.screen.areas:
                area.tag_redraw()
        return {'FINISHED'}


class ATOMIC_OT_replace_missing_browse(bpy.types.Operator):
    """Browse for replacement file"""
    bl_idname = "atomic.replace_missing_browse"
    bl_label = "Browse for File"
    bl_options = {'INTERNAL'}

    kind: bpy.props.EnumProperty(
        items=(
            ('LIBRARY', 'Library', ''),
            ('IMAGE', 'Image', ''),
        ),
        default='LIBRARY',
    )
    item_key: bpy.props.StringProperty()
    library_key: bpy.props.StringProperty()
    filename: bpy.props.StringProperty(
        name="Filename",
        subtype='FILE_NAME',
        default="",
    )
    filepath: bpy.props.StringProperty(
        name="File Path",
        subtype='FILE_PATH',
        default="",
    )
    filter_glob: bpy.props.StringProperty(
        default="*.blend;*.png;*.jpg;*.jpeg;*.exr;*.hdr;*.tif;*.tiff;*.tga;*.bmp;*.webp;*.psd;*.mp4;*.mov;*.avi",
        options={'HIDDEN'},
    )

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        global _replace_missing_state
        key = self.item_key or self.library_key
        selected_path = self.filepath
        if key and selected_path:
            _replace_missing_state[_replace_state_key(self.kind, key)] = (
                selected_path
            )
        for area in context.screen.areas:
            area.tag_redraw()
        return {'FINISHED'}


class ATOMIC_OT_replace_missing_relink(bpy.types.Operator):
    """Relink the library or image to the specified path"""
    bl_idname = "atomic.replace_missing_relink"
    bl_label = "Relink"
    bl_options = {'INTERNAL', 'UNDO'}

    kind: bpy.props.EnumProperty(
        items=(
            ('LIBRARY', 'Library', ''),
            ('IMAGE', 'Image', ''),
        ),
        default='LIBRARY',
    )
    item_key: bpy.props.StringProperty()
    library_key: bpy.props.StringProperty()
    filepath: bpy.props.StringProperty()

    def execute(self, context):
        global _replace_missing_state

        if not self.filepath:
            self.report({'ERROR'}, "No filepath specified")
            return {'CANCELLED'}

        key = self.item_key or self.library_key
        if self.kind == 'IMAGE':
            success, message = _relink_image(key, self.filepath)
            label = "Image"
        else:
            success, message = _relink_library(key, self.filepath)
            label = "Library"

        if success:
            self.report({'INFO'}, f"{label} relinked: {message}")
            _replace_missing_state.pop(_replace_state_key(self.kind, key), None)
            for area in context.screen.areas:
                area.tag_redraw()
        else:
            self.report({'ERROR'}, message)

        return {'FINISHED'}


class ATOMIC_OT_replace_missing_relink_all(bpy.types.Operator):
    """Relink all missing files that have a replacement path set"""
    bl_idname = "atomic.replace_missing_relink_all"
    bl_label = "Relink All"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        global _replace_missing_state

        to_relink = [
            (k, v) for k, v in _replace_missing_state.items() if v and v.strip()
        ]
        if not to_relink:
            self.report({'WARNING'}, "No replacement paths set")
            return {'CANCELLED'}

        ok, fail = 0, 0
        fail_details = []
        for state_key, filepath in to_relink:
            if ':' not in state_key:
                # Legacy bare library key
                kind, key = 'LIBRARY', state_key
            else:
                kind, key = state_key.split(':', 1)
            if kind == 'IMAGE':
                success, message = _relink_image_batch_result(key, filepath)
            else:
                success, message = _relink_library_batch_result(key, filepath)
            if success:
                ok += 1
                _replace_missing_state.pop(state_key, None)
            else:
                fail += 1
                fail_details.append(f"{key}: {message}")

        if ok:
            self.report(
                {'INFO'},
                f"Relinked {ok} file{'s' if ok != 1 else ''}"
                + (f", {fail} failed" if fail else ""),
            )
        if fail:
            self.report(
                {'ERROR'},
                f"{fail} file{'s' if fail != 1 else ''} failed to relink"
                + (f" — {fail_details[0]}" if fail_details else ""),
            )
            for detail in fail_details[1:]:
                self.report({'ERROR'}, detail)

        for area in context.screen.areas:
            area.tag_redraw()
        return {'FINISHED'}



reg_list = [
    ATOMIC_OT_reload_missing,
    ATOMIC_OT_reload_report,
    ATOMIC_OT_search_missing,
    ATOMIC_OT_search_missing_path_add,
    ATOMIC_OT_search_missing_path_remove,
    ATOMIC_OT_search_missing_path_save_defaults,
    ATOMIC_OT_search_missing_path_load_defaults,
    ATOMIC_OT_search_missing_clear_index,
    ATOMIC_OT_search_missing_start,
    ATOMIC_OT_search_missing_cancel,
    ATOMIC_OT_search_missing_select,
    ATOMIC_OT_search_missing_relink,
    ATOMIC_OT_search_missing_relink_all,
    ATOMIC_PG_replace_path_item,
    ATOMIC_OT_replace_missing,
    ATOMIC_OT_replace_missing_browse,
    ATOMIC_OT_replace_missing_set_path,
    ATOMIC_OT_replace_missing_relink,
    ATOMIC_OT_replace_missing_relink_all,
    ATOMIC_OT_remove_missing
]


def register():
    from ..ui.preferences_ui import ATOMIC_PG_remap_search_path

    for item in reg_list:
        register_class(item)

    bpy.types.WindowManager.atomic_remap_search_paths = bpy.props.CollectionProperty(
        type=ATOMIC_PG_remap_search_path,
        name="Atomic Remap Search Paths",
    )


def unregister():
    if hasattr(bpy.types.WindowManager, "atomic_remap_search_paths"):
        try:
            del bpy.types.WindowManager.atomic_remap_search_paths
        except Exception:
            pass

    for item in reg_list:
        compat.safe_unregister_class(item)

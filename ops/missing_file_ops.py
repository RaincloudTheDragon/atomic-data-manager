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
import threading
import queue
from bpy.utils import register_class
from ..utils import compat
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
            image.reload()

        # reload libraries
        for library in bpy.data.libraries:
            library.reload()

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
                    columns=2
                )

            if missing_libraries:
                ui_layouts.box_list(
                    layout=self.layout,
                    items=missing_images,
                    icon='LIBRARY_DATA_DIRECT',
                    columns=2
                )

        else:
            row = layout.row()
            row.label(text="All files successfully reloaded!")

        row = layout.row()  # extra space

    def execute(self, context):
        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)


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


# Module-level state for library search
_library_search_state = {
    'search_directory': '',
    'is_searching': False,
    'found_blend_files': [],
    'matches': {},  # {library_key: {'exact': path, 'candidates': [paths], 'warnings': [], 'selected_match': ''}}
    'progress': 0.0,
    'status': '',
    'search_thread': None,
    'progress_queue': None,
    'search_complete': False,
    'search_error': None
}


def _search_blend_files_worker(directory, progress_queue, found_files, error_queue):
    """Worker thread function to recursively search for .blend files"""
    try:
        if not os.path.exists(directory):
            error_queue.put(f"Directory does not exist: {directory}")
            return
        
        if not os.path.isdir(directory):
            error_queue.put(f"Path is not a directory: {directory}")
            return
        
        total_files = 0
        scanned_dirs = 0
        
        # First pass: count files for progress (with error handling)
        try:
            for root, dirs, files in os.walk(directory):
                try:
                    total_files += len([f for f in files if f.lower().endswith('.blend')])
                    scanned_dirs += 1
                    if scanned_dirs % 10 == 0:
                        progress_queue.put(('status', f'Scanning directory structure... ({scanned_dirs} directories)'))
                except (PermissionError, OSError) as e:
                    # Skip directories we can't access
                    config.debug_print(f"[Atomic Debug] Cannot access {root}: {e}")
                    continue
        except Exception as e:
            error_queue.put(f"Error scanning directory structure: {str(e)}")
            return
        
        # Second pass: collect files (with error handling)
        found_count = 0
        try:
            for root, dirs, files in os.walk(directory):
                try:
                    for file in files:
                        if file.lower().endswith('.blend'):
                            filepath = os.path.join(root, file)
                            try:
                                # Verify it's actually a file and readable
                                if os.path.isfile(filepath) and os.access(filepath, os.R_OK):
                                    found_files.append(filepath)
                                    found_count += 1
                                    
                                    # Update progress
                                    if total_files > 0:
                                        progress = (found_count / total_files) * 100.0
                                        progress_queue.put(('progress', progress))
                                        progress_queue.put(('status', f'Found {found_count}/{total_files} .blend files...'))
                            except (OSError, PermissionError) as e:
                                # Skip files we can't access
                                config.debug_print(f"[Atomic Debug] Cannot access file {filepath}: {e}")
                                continue
                except (PermissionError, OSError) as e:
                    # Skip directories we can't access
                    config.debug_print(f"[Atomic Debug] Cannot access directory {root}: {e}")
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
    """Timer callback to process library search progress"""
    global _library_search_state
    
    atom = bpy.context.scene.atomic
    state = _library_search_state
    
    if not state['is_searching']:
        return None
    
    # Check for cancellation
    if atom.cancel_operation:
        if state['search_thread'] and state['search_thread'].is_alive():
            # Thread will finish naturally, we'll handle cancellation in next step
            pass
        state['is_searching'] = False
        _safe_set_atom_property(atom, 'is_operation_running', False)
        _safe_set_atom_property(atom, 'operation_progress', 0.0)
        _safe_set_atom_property(atom, 'operation_status', "")
        config.debug_print("[Atomic Debug] Library search cancelled")
        return None
    
    # Process progress updates from queue
    if state['progress_queue']:
        try:
            while True:
                try:
                    update_type, value = state['progress_queue'].get_nowait()
                    if update_type == 'progress':
                        state['progress'] = value
                        _safe_set_atom_property(atom, 'operation_progress', value)
                    elif update_type == 'status':
                        state['status'] = value
                        _safe_set_atom_property(atom, 'operation_status', value)
                    elif update_type == 'complete':
                        state['search_complete'] = True
                except queue.Empty:
                    break
        except Exception as e:
            config.debug_print(f"[Atomic Error] Error processing progress queue: {e}")
    
    # Check for errors
    if state.get('error_queue'):
        try:
            error_msg = state['error_queue'].get_nowait()
            state['search_error'] = error_msg
            state['is_searching'] = False
            _safe_set_atom_property(atom, 'operation_status', f"Error: {error_msg}")
            # Clear progress after showing error
            def clear_error_progress():
                _safe_set_atom_property(atom, 'is_operation_running', False)
                _safe_set_atom_property(atom, 'operation_progress', 0.0)
                _safe_set_atom_property(atom, 'operation_status', "")
                for area in bpy.context.screen.areas:
                    area.tag_redraw()
                return None  # Run once
            bpy.app.timers.register(clear_error_progress, first_interval=3.0)  # Clear after 3 seconds
            return None
        except queue.Empty:
            pass
    
    # Check if search is complete
    if state['search_complete'] and (not state['search_thread'] or not state['search_thread'].is_alive()):
        state['is_searching'] = False
        _safe_set_atom_property(atom, 'operation_progress', 100.0)
        _safe_set_atom_property(atom, 'operation_status', f"Search complete! Found {len(state['found_blend_files'])} .blend files")
        
        # Match libraries
        _match_libraries()
        
        # Clear operation running state after a short delay to show completion message
        # This allows the user to see the completion message briefly
        def clear_progress():
            _safe_set_atom_property(atom, 'is_operation_running', False)
            _safe_set_atom_property(atom, 'operation_progress', 0.0)
            _safe_set_atom_property(atom, 'operation_status', "")
            # Redraw UI
            for area in bpy.context.screen.areas:
                area.tag_redraw()
            return None  # Run once
        
        # Only register timer if not already cleared (avoid duplicate timers)
        if atom.is_operation_running:
            bpy.app.timers.register(clear_progress, first_interval=1.5)  # Clear after 1.5 seconds
        
        # Redraw UI
        for area in bpy.context.screen.areas:
            area.tag_redraw()
        
        return None
    
    # Continue polling
    return 0.1


def _match_libraries():
    """Match missing libraries to found .blend files"""
    global _library_search_state
    
    missing_libs = missing.libraries()
    found_files = _library_search_state['found_blend_files']
    matches = {}
    
    for lib_key in missing_libs:
        lib_info = missing.get_missing_library_info(lib_key)
        if not lib_info:
            continue
        
        target_filename = lib_info['filename'].lower()
        exact_match = None
        candidates = []
        
        # Try exact match first (case-insensitive)
        for filepath in found_files:
            filename = os.path.basename(filepath).lower()
            if filename == target_filename:
                exact_match = filepath
                break
        
        # If no exact match, collect candidates (partial matches)
        if not exact_match:
            for filepath in found_files:
                filename = os.path.basename(filepath).lower()
                # Simple fuzzy matching: check if target filename is in candidate or vice versa
                if target_filename in filename or filename in target_filename:
                    candidates.append(filepath)
        
        matches[lib_key] = {
            'exact': exact_match,
            'candidates': candidates[:10],  # Limit to 10 candidates
            'warnings': [],
            'selected_match': exact_match if exact_match else (candidates[0] if candidates else None)
        }
        
        # Validate if we have a match
        if matches[lib_key]['selected_match']:
            warnings = _validate_replacement_library(lib_key, matches[lib_key]['selected_match'], lib_info)
            matches[lib_key]['warnings'] = warnings
    
    _library_search_state['matches'] = matches


def _validate_replacement_library(library_key, replacement_path, original_info):
    """
    Validate a replacement library and return warnings.
    
    Returns list of warning strings.
    """
    warnings = []
    
    if not os.path.exists(replacement_path):
        warnings.append(f"Replacement file does not exist: {replacement_path}")
        return warnings
    
    try:
        # Try to read the blend file to check for missing dependencies
        # We'll use a simple approach: try to load it temporarily
        # Note: This is a simplified check - full validation would require loading the blend file
        
        # Check if replacement library has missing dependencies by examining its structure
        # For now, we'll do a basic file check and let Blender handle the rest when relinking
        
        # Check if original linked data-blocks might be missing in replacement
        # This is a simplified check - we can't easily read blend file contents without loading it
        # The user will see warnings when they actually try to use the library
        
        pass
    except Exception as e:
        warnings.append(f"Could not validate replacement library: {str(e)}")
    
    return warnings


def _relink_library(library_key, new_filepath, use_relative_path=True):
    """Relink a library to a new filepath
    
    Args:
        library_key: Key of the library to relink
        new_filepath: New filepath (can be absolute or relative)
        use_relative_path: If True, use relative path when available (default: True)
    """
    if library_key not in bpy.data.libraries:
        return False, "Library not found"
    
    library = bpy.data.libraries[library_key]
    
    try:
        # Convert to absolute path
        if not new_filepath:
            return False, "No filepath provided"
        
        abs_path = bpy.path.abspath(new_filepath)
        
        if not os.path.exists(abs_path):
            return False, f"File does not exist: {abs_path}"
        
        if not os.path.isfile(abs_path):
            return False, f"Path is not a file: {abs_path}"
        
        if not abs_path.lower().endswith('.blend'):
            return False, f"File is not a .blend file: {abs_path}"
        
        # Check if file is readable
        if not os.access(abs_path, os.R_OK):
            return False, f"Cannot read file (permission denied): {abs_path}"
        
        # Update library filepath
        try:
            if use_relative_path:
                # Use relative path when available (bpy.path.relpath returns relative if possible, absolute otherwise)
                library.filepath = bpy.path.relpath(abs_path)
            else:
                # Use absolute path
                library.filepath = abs_path
        except Exception as e:
            return False, f"Error setting library filepath: {str(e)}"
        
        # Reload the library
        try:
            library.reload()
        except Exception as e:
            # Filepath was set, but reload failed (might be corrupted or incompatible)
            return False, f"Library filepath updated but reload failed: {str(e)}"
        
        return True, "Library relinked successfully"
    except Exception as e:
        return False, f"Error relinking library: {str(e)}"


def _safe_set_atom_property(atom, prop_name, value):
    """Safely set an atom property, catching errors when Blender is in read-only state."""
    if atom is None:
        return False
    try:
        setattr(atom, prop_name, value)
        return True
    except (AttributeError, RuntimeError) as e:
        config.debug_print(f"[Atomic Debug] Could not set {prop_name}: {e}")
        return False


# Atomic Data Manager Search for Missing Files Operator
class ATOMIC_OT_search_missing(bpy.types.Operator):
    """Search a specified directory for missing library files"""
    bl_idname = "atomic.search_missing"
    bl_label = "Search for Missing Libraries"
    bl_options = {'REGISTER', 'UNDO'}
    
    # Directory to search
    def _update_search_directory(self, context):
        """Update state when search directory changes"""
        global _library_search_state
        if self.search_directory:
            try:
                abs_dir = bpy.path.abspath(self.search_directory)
                _library_search_state['search_directory'] = abs_dir
            except Exception:
                pass
    
    search_directory: bpy.props.StringProperty(
        name="Search Directory",
        description="Directory to search for .blend files",
        subtype='DIR_PATH',
        default="",
        update=_update_search_directory
    )
    
    # Relative path option
    relative_path: bpy.props.BoolProperty(
        name="Relative Path",
        description="Select the file relative to the blend file",
        default=True
    )
    
    # Selected matches for libraries with multiple candidates
    selected_matches: bpy.props.StringProperty(default="")  # JSON-like storage: "lib_key:filepath|lib_key:filepath"
    
    def draw(self, context):
        layout = self.layout
        global _library_search_state
        
        state = _library_search_state
        atom = context.scene.atomic
        
        # Directory selection
        row = layout.row()
        row.prop(self, 'search_directory')
        
        # Relative path checkbox
        row = layout.row()
        row.prop(self, 'relative_path', text="Relative Path")
        
        # Update state with current directory
        if self.search_directory:
            try:
                abs_dir = bpy.path.abspath(self.search_directory)
                state['search_directory'] = abs_dir
            except Exception:
                pass
        
        # Search button
        if not state['is_searching']:
            row = layout.row()
            search_dir = self.search_directory or state.get('search_directory', '')
            if search_dir:
                try:
                    abs_dir = bpy.path.abspath(search_dir)
                    if os.path.isdir(abs_dir) and os.access(abs_dir, os.R_OK):
                        row.operator("atomic.search_missing_start", text="Start Search")
                    else:
                        row.label(text="Please select a valid, readable directory", icon='ERROR')
                except Exception:
                    row.label(text="Please select a valid directory", icon='ERROR')
            else:
                row.label(text="Please select a directory", icon='INFO')
        else:
            # Progress display
            row = layout.row()
            row.prop(atom, 'operation_progress', text="Progress", slider=True)
            
            if atom.operation_status:
                row = layout.row()
                row.label(text=atom.operation_status, icon='TIME')
            
            row = layout.row()
            row.operator("atomic.search_missing_cancel", text="Cancel Search")
        
        # Results display
        if state['search_complete'] and not state['is_searching']:
            missing_libs = missing.libraries()
            matches = state.get('matches', {})
            
            if not missing_libs:
                row = layout.row()
                row.label(text="No missing libraries found!", icon='INFO')
                return
            
            layout.separator()
            row = layout.row()
            row.label(text="Missing Libraries:", icon='LIBRARY_DATA_DIRECT')
            
            # Display each missing library
            for lib_key in missing_libs:
                box = layout.box()
                
                # Library name
                row = box.row()
                lib_info = missing.get_missing_library_info(lib_key)
                lib_name = lib_info['filename'] if lib_info else lib_key
                row.label(text=lib_name, icon='LIBRARY_DATA_DIRECT')
                
                match_info = matches.get(lib_key, {})
                exact_match = match_info.get('exact')
                candidates = match_info.get('candidates', [])
                warnings = match_info.get('warnings', [])
                selected_match = match_info.get('selected_match')
                
                # Show match status
                if exact_match:
                    row = box.row()
                    row.label(text=f"✓ Exact match: {os.path.basename(exact_match)}", icon='CHECKMARK')
                elif candidates:
                    row = box.row()
                    row.label(text=f"Found {len(candidates)} candidate(s)", icon='QUESTION')
                    
                    # Show candidate selection
                    if len(candidates) > 1:
                        # Store current selection
                        current_selection = self._get_selected_match(lib_key)
                        
                        row = box.row()
                        row.label(text="Select match:")
                        for i, candidate in enumerate(candidates[:5]):  # Show max 5 candidates
                            candidate_name = os.path.basename(candidate)
                            if len(candidate_name) > 40:
                                candidate_name = candidate_name[:37] + "..."
                            op = row.operator("atomic.search_missing_select", text=candidate_name)
                            op.library_key = lib_key
                            op.filepath = candidate
                elif state['found_blend_files']:
                    row = box.row()
                    row.label(text="No match found", icon='ERROR')
                else:
                    row = box.row()
                    row.label(text="No .blend files found in directory", icon='INFO')
                
                # Show warnings
                if warnings:
                    for warning in warnings:
                        row = box.row()
                        row.label(text=f"⚠ {warning}", icon='ERROR')
                
                # Relink button
                if selected_match:
                    row = box.row()
                    if warnings:
                        op = row.operator("atomic.search_missing_relink", text="Relink (Ignore Warnings)")
                        op.library_key = lib_key
                        op.filepath = selected_match
                        op.ignore_warnings = True
                        op.use_relative_path = self.relative_path
                    else:
                        op = row.operator("atomic.search_missing_relink", text="Relink")
                        op.library_key = lib_key
                        op.filepath = selected_match
                        op.ignore_warnings = False
                        op.use_relative_path = self.relative_path
        
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
    
    def execute(self, context):
        return {'FINISHED'}
    
    def invoke(self, context, event):
        global _library_search_state
        
        # Initialize state
        _library_search_state = {
            'search_directory': self.search_directory or '',
            'is_searching': False,
            'found_blend_files': [],
            'matches': {},
            'progress': 0.0,
            'status': '',
            'search_thread': None,
            'progress_queue': None,
            'search_complete': False,
            'search_error': None
        }
        
        wm = context.window_manager
        return wm.invoke_props_dialog(self, width=600)


# Operator to start the search
class ATOMIC_OT_search_missing_start(bpy.types.Operator):
    """Start searching for missing library files"""
    bl_idname = "atomic.search_missing_start"
    bl_label = "Start Search"
    bl_options = {'INTERNAL'}
    
    def execute(self, context):
        global _library_search_state
        
        # Get search directory from state (updated by the draw method)
        search_dir = _library_search_state.get('search_directory', '')
        
        # Fallback: try to get from the search_missing operator if available
        if not search_dir and hasattr(context.window_manager, 'operators'):
            for op in context.window_manager.operators:
                if op.bl_idname == 'atomic.search_missing' and hasattr(op, 'search_directory') and op.search_directory:
                    search_dir = op.search_directory
                    break
        
        if not search_dir:
            self.report({'ERROR'}, "Please select a search directory first")
            return {'CANCELLED'}
        
        try:
            search_dir = bpy.path.abspath(search_dir)
        except Exception as e:
            self.report({'ERROR'}, f"Invalid path: {str(e)}")
            return {'CANCELLED'}
        
        if not os.path.exists(search_dir):
            self.report({'ERROR'}, f"Directory does not exist: {search_dir}")
            return {'CANCELLED'}
        
        if not os.path.isdir(search_dir):
            self.report({'ERROR'}, f"Path is not a directory: {search_dir}")
            return {'CANCELLED'}
        
        if not os.access(search_dir, os.R_OK):
            self.report({'ERROR'}, f"Permission denied: Cannot read directory {search_dir}")
            return {'CANCELLED'}
        
        # Check if search is already running
        if _library_search_state.get('is_searching', False):
            self.report({'WARNING'}, "Search is already in progress")
            return {'CANCELLED'}
        
        # Initialize search state
        atom = context.scene.atomic
        _library_search_state['search_directory'] = search_dir
        _library_search_state['is_searching'] = True
        _library_search_state['found_blend_files'] = []
        _library_search_state['matches'] = {}
        _library_search_state['progress'] = 0.0
        _library_search_state['status'] = 'Initializing search...'
        _library_search_state['search_complete'] = False
        _library_search_state['search_error'] = None
        
        # Initialize progress tracking
        _safe_set_atom_property(atom, 'is_operation_running', True)
        _safe_set_atom_property(atom, 'operation_progress', 0.0)
        _safe_set_atom_property(atom, 'operation_status', 'Initializing search...')
        _safe_set_atom_property(atom, 'cancel_operation', False)
        
        # Create queues for thread communication
        progress_queue = queue.Queue()
        error_queue = queue.Queue()
        _library_search_state['progress_queue'] = progress_queue
        _library_search_state['error_queue'] = error_queue
        
        # Start search thread
        try:
            search_thread = threading.Thread(
                target=_search_blend_files_worker,
                args=(search_dir, progress_queue, _library_search_state['found_blend_files'], error_queue),
                daemon=True
            )
            search_thread.start()
            _library_search_state['search_thread'] = search_thread
        except Exception as e:
            _library_search_state['is_searching'] = False
            _safe_set_atom_property(atom, 'is_operation_running', False)
            self.report({'ERROR'}, f"Failed to start search thread: {str(e)}")
            return {'CANCELLED'}
        
        # Start timer to process progress
        try:
            bpy.app.timers.register(_process_library_search_step)
        except Exception as e:
            config.debug_print(f"[Atomic Error] Failed to register timer: {e}")
            _library_search_state['is_searching'] = False
            _safe_set_atom_property(atom, 'is_operation_running', False)
            self.report({'ERROR'}, f"Failed to start progress timer: {str(e)}")
            return {'CANCELLED'}
        
        # Redraw
        for area in context.screen.areas:
            area.tag_redraw()
        
        return {'FINISHED'}


# Operator to cancel the search
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
    """Select a match for a library"""
    bl_idname = "atomic.search_missing_select"
    bl_label = "Select Match"
    bl_options = {'INTERNAL'}
    
    library_key: bpy.props.StringProperty()
    filepath: bpy.props.StringProperty()
    
    def execute(self, context):
        global _library_search_state
        
        matches = _library_search_state.get('matches', {})
        if self.library_key in matches:
            matches[self.library_key]['selected_match'] = self.filepath
            # Re-validate
            lib_info = missing.get_missing_library_info(self.library_key)
            if lib_info:
                warnings = _validate_replacement_library(self.library_key, self.filepath, lib_info)
                matches[self.library_key]['warnings'] = warnings
        
        # Redraw
        for area in context.screen.areas:
            area.tag_redraw()
        
        return {'FINISHED'}


# Operator to relink a library
class ATOMIC_OT_search_missing_relink(bpy.types.Operator):
    """Relink a library to the selected file"""
    bl_idname = "atomic.search_missing_relink"
    bl_label = "Relink Library"
    bl_options = {'INTERNAL'}
    
    library_key: bpy.props.StringProperty()
    filepath: bpy.props.StringProperty()
    ignore_warnings: bpy.props.BoolProperty(default=False)
    use_relative_path: bpy.props.BoolProperty(default=True)
    
    def execute(self, context):
        success, message = _relink_library(self.library_key, self.filepath, self.use_relative_path)
        
        if success:
            self.report({'INFO'}, f"Library relinked: {message}")
            # Remove from missing list by updating state
            global _library_search_state
            if self.library_key in _library_search_state.get('matches', {}):
                del _library_search_state['matches'][self.library_key]
            
            # Clear progress state if operation was running
            atom = context.scene.atomic
            if atom.is_operation_running:
                _safe_set_atom_property(atom, 'is_operation_running', False)
                _safe_set_atom_property(atom, 'operation_progress', 0.0)
                _safe_set_atom_property(atom, 'operation_status', "")
        else:
            self.report({'ERROR'}, message)
        
        # Redraw
        for area in context.screen.areas:
            area.tag_redraw()
        
        return {'FINISHED'}


# TODO: Implement replace missing once file browser bugs are fixed
# Atomic Data Manager Replace Missing Files Operator
class ATOMIC_OT_replace_missing(bpy.types.Operator):
    """Replace each missing file with a new file"""
    bl_idname = "atomic.replace_missing"
    bl_label = "Replace Missing Files"

    def draw(self, context):
        layout = self.layout

        row = layout.row()
        row.label(text="Unsupported Operation!")

    def execute(self, context):
        return {'FINISHED'}

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self)


reg_list = [
    ATOMIC_OT_reload_missing,
    ATOMIC_OT_reload_report,
    ATOMIC_OT_search_missing,
    ATOMIC_OT_search_missing_start,
    ATOMIC_OT_search_missing_cancel,
    ATOMIC_OT_search_missing_select,
    ATOMIC_OT_search_missing_relink,
    ATOMIC_OT_replace_missing,
    ATOMIC_OT_remove_missing
]


def register():
    for item in reg_list:
        register_class(item)


def unregister():
    for item in reg_list:
        compat.safe_unregister_class(item)

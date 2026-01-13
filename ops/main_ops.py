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
    'deleted_count': 0
}

# Unified scanning state for both Smart Select and Clean
_scan_state = {
    'mode': None,  # 'quick' or 'full'
    'categories_to_scan': [],  # List of categories to scan
    'current_category_index': 0,
    'results': {},  # Quick scan: {category: bool}, Full scan: {category: [items]}
    'status_updated': False,
    # Incremental scanning state
    'images_list': None,
    'images_index': 0,
    'images_unused': [],
    'worlds_list': None,
    'worlds_index': 0,
    'callback': None,  # Function to call when scan completes
    'callback_data': {}  # Data to pass to callback
}


def _invalidate_cache():
    """Invalidate the unused data cache."""
    global _unused_cache, _cache_valid
    _unused_cache = None
    _cache_valid = False
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


# Multi-process deep scan state
_deep_scan_state = {
    'is_running': False,
    'worker_processes': [],  # List of (job_index, subprocess.Popen) tuples
    'job_outputs': {},  # {job_index: output_json_path}
    'completed_jobs': set(),
    'total_jobs': 0,
    'images_per_job': 0,
    'image_batches': [],  # List of image name lists for each job
    'start_time': None,  # Time when scan started
    'timeout_seconds': 3600  # 1 hour timeout per job
}


def _calculate_job_distribution(total_images):
    """Calculate how many jobs to create and how to distribute images"""
    import os as os_module
    cpu_count = os_module.cpu_count() or 4
    reserved = config.reserve_threads_for_deep_scan
    
    # Cap max workers to 4 - too many simultaneous Blender instances cause resource contention
    # Each Blender instance is heavy, so fewer workers with more images each is more efficient
    max_workers = 4
    worker_threads = min(max_workers, max(1, cpu_count - reserved))
    
    # Calculate images per job (round up)
    images_per_job = math.ceil(total_images / worker_threads) if worker_threads > 0 else total_images

    # Ensure at least 1 image per job and enough images to be worth spawning a process
    min_images_per_job = 10  # Don't spawn a Blender instance for less than 10 images
    if images_per_job < min_images_per_job:
        images_per_job = min_images_per_job

    # Recalculate actual number of jobs needed
    actual_jobs = math.ceil(total_images / images_per_job) if images_per_job > 0 else 1
    
    # Don't spawn more workers than needed
    actual_jobs = min(actual_jobs, worker_threads)

    return actual_jobs, images_per_job, worker_threads


def _split_images_into_batches(image_list, images_per_job):
    """Split image list into batches for each job"""
    batches = []
    for i in range(0, len(image_list), images_per_job):
        batches.append(image_list[i:i + images_per_job])
    return batches


def _launch_worker_processes(blend_file_path, image_batches, temp_dir):
    """Launch headless Blender instances for each job"""
    global _deep_scan_state
    
    # Get Blender executable path
    blender_exe = bpy.app.binary_path
    if not blender_exe or not os.path.exists(blender_exe):
        config.debug_print("[Atomic Error] Could not find Blender executable")
        return False
    
    # Get worker script path (in the same directory as this file)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    worker_script = os.path.join(script_dir, 'image_deep_scan_worker.py')
    
    if not os.path.exists(worker_script):
        config.debug_print(f"[Atomic Error] Worker script not found: {worker_script}")
        return False
    
    processes = []
    job_outputs = {}
    
    for job_index, image_batch in enumerate(image_batches):
        # Create temporary JSON file for image list
        image_list_json = os.path.join(temp_dir, f"atomic_job_{job_index}_images.json")
        output_json = os.path.join(temp_dir, f"atomic_job_{job_index}_result.json")
        
        # Write image list to JSON (image_batch contains image objects)
        image_list_data = {
            'images': [img.name for img in image_batch],
            'blend_file': blend_file_path
        }
        
        try:
            with open(image_list_json, 'w', encoding='utf-8') as f:
                json.dump(image_list_data, f)
        except Exception as e:
            config.debug_print(f"[Atomic Error] Failed to write image list for job {job_index}: {e}")
            continue
        
        job_outputs[job_index] = output_json
        
        # Create log files for stdout/stderr (for independent processes)
        stdout_log = os.path.join(temp_dir, f"atomic_job_{job_index}_stdout.log")
        stderr_log = os.path.join(temp_dir, f"atomic_job_{job_index}_stderr.log")
        
        # Launch headless Blender process as independent external process
        # Format: blender.exe -b blendfile.blend --python worker_script.py -- job_index image_list_json output_json
        # With only 4 workers, addon loading conflicts are minimal
        cmd = [
            blender_exe,
            '-b',  # Background mode
            blend_file_path,
            '--python', worker_script,
            '--',
            str(job_index),
            image_list_json,
            output_json
        ]
        
        try:
            # Launch process as independent external process (new console window on Windows)
            # This ensures processes run independently and can utilize CPU cores separately
            if os.name == 'nt':  # Windows
                # Use Windows 'start' command to launch in a completely separate process
                # This ensures the process is truly independent and not tied to the parent's thread pool
                # Create a batch file that launches Blender, then use 'start' to launch the batch file
                # This avoids command-line escaping issues
                
                # Create batch file that launches Blender
                batch_file = os.path.join(temp_dir, f"atomic_job_{job_index}_launcher.bat")
                with open(batch_file, 'w', encoding='utf-8') as f:
                    f.write('@echo off\n')
                    # Build command line - quote each argument that contains spaces
                    cmd_parts = []
                    for arg in cmd:
                        # Quote arguments with spaces or special characters
                        if ' ' in arg or '"' in arg:
                            # Escape quotes in the argument
                            escaped_arg = arg.replace('"', '""')
                            cmd_parts.append(f'"{escaped_arg}"')
                        else:
                            cmd_parts.append(arg)
                    # Write the full command on one line
                    cmd_line = ' '.join(cmd_parts)
                    # Redirect output
                    f.write(f'{cmd_line} > "{stdout_log}" 2> "{stderr_log}"\n')
                
                # Use 'start' to launch the batch file in a separate window
                # This creates a truly independent process
                # Use cmd /c to execute the batch file
                start_cmd = f'start "Atomic Worker {job_index}" /MIN cmd /c "{batch_file}"'
                
                # Debug: Read and log batch file contents
                try:
                    with open(batch_file, 'r', encoding='utf-8') as f:
                        batch_content = f.read()
                        config.debug_print(f"[Atomic Debug] Batch file {job_index} contents:\n{batch_content}")
                except Exception as e:
                    config.debug_print(f"[Atomic Debug] Could not read batch file: {e}")
                
                config.debug_print(f"[Atomic Debug] Launching worker {job_index} via batch file: {batch_file}")
                
                # Execute start command - it returns immediately, process runs independently
                # We use shell=True because 'start' is a shell builtin
                process = subprocess.Popen(
                    start_cmd,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    # No creationflags needed - 'start' already creates independent process
                )
                # The Blender process is now running in a completely separate window
                # We can't track it via this process object (it's just the cmd.exe that launched it)
                # So we'll poll for output JSON files to detect completion
            else:
                # Non-Windows: use standard subprocess
                stdout_handle = open(stdout_log, 'w', encoding='utf-8', buffering=1)
                stderr_handle = open(stderr_log, 'w', encoding='utf-8', buffering=1)
                process = subprocess.Popen(
                    cmd,
                    stdout=stdout_handle,
                    stderr=stderr_handle
                )
            
            processes.append((job_index, process))
            config.debug_print(f"[Atomic Debug] Launched worker process for job {job_index}")
        except Exception as e:
            config.debug_print(f"[Atomic Error] Failed to launch worker process for job {job_index}: {e}")
            continue
    
    _deep_scan_state['worker_processes'] = processes
    _deep_scan_state['job_outputs'] = job_outputs
    _deep_scan_state['completed_jobs'] = set()
    _deep_scan_state['worker_script'] = worker_script  # Store for cancellation
    _deep_scan_state['blend_file_path'] = blend_file_path  # Store for cancellation
    
    return len(processes) > 0


def _process_deep_scan_step():
    """Timer callback to monitor worker processes and merge results"""
    global _deep_scan_state, _image_scan_cache, _scan_state
    
    atom = bpy.context.scene.atomic
    
    # Initialize start time if not set
    if _deep_scan_state['start_time'] is None:
        _deep_scan_state['start_time'] = time.time()
    
    # Check for cancellation
    if atom.cancel_operation:
        # Kill all worker processes
        # Since we used 'start' command, the process objects are just cmd.exe launchers
        # We need to kill the actual Blender worker processes
        config.debug_print("[Atomic Debug] Cancelling deep scan - killing worker processes...")
        
        # Try to kill via process objects first
        for job_index, process in _deep_scan_state['worker_processes']:
            try:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            except Exception as e:
                config.debug_print(f"[Atomic Debug] Error killing launcher process {job_index}: {e}")
                try:
                    process.kill()
                except:
                    pass
        
        # Kill all worker processes - both cmd.exe launchers and Blender processes
        if os.name == 'nt':  # Windows
            try:
                import subprocess as sp
                # Kill processes with window titles matching "Atomic Worker *" (cmd.exe launchers)
                result = sp.run(['taskkill', '/F', '/FI', 'WINDOWTITLE eq Atomic Worker*', '/T'], 
                               stdout=sp.PIPE, stderr=sp.PIPE, timeout=5)
                if result.returncode == 0:
                    config.debug_print("[Atomic Debug] Killed worker launcher processes via taskkill")
                
                # Also kill any Blender processes that are running the worker script
                # Find all blender.exe processes and check their command lines
                # Use wmic to get process command lines, then kill matching ones
                try:
                    # Get all blender.exe processes
                    result = sp.run(['wmic', 'process', 'where', 'name="blender.exe"', 'get', 'processid,commandline', '/format:csv'],
                                  stdout=sp.PIPE, stderr=sp.PIPE, timeout=5, text=True)
                    if result.returncode == 0:
                        lines = result.stdout.strip().split('\n')
                        for line in lines[1:]:  # Skip header
                            if 'image_deep_scan_worker.py' in line:
                                # Extract process ID and kill it
                                parts = line.split(',')
                                if len(parts) >= 2:
                                    try:
                                        pid = parts[-1].strip()
                                        if pid.isdigit():
                                            sp.run(['taskkill', '/F', '/PID', pid], 
                                                  stdout=sp.PIPE, stderr=sp.PIPE, timeout=2)
                                            config.debug_print(f"[Atomic Debug] Killed Blender worker process PID {pid}")
                                    except:
                                        pass
                except Exception as e:
                    config.debug_print(f"[Atomic Debug] Could not find/kill Blender worker processes: {e}")
                
                # taskkill returns 128 if no processes found, which is fine
            except Exception as e:
                config.debug_print(f"[Atomic Debug] Could not kill worker processes: {e}")
        
        _deep_scan_state['is_running'] = False
        _safe_set_atom_property(atom, 'is_operation_running', False)
        _safe_set_atom_property(atom, 'operation_progress', 0.0)
        _safe_set_atom_property(atom, 'operation_status', "Operation cancelled")
        _safe_set_atom_property(atom, 'cancel_operation', False)
        
        # Cleanup temp files
        temp_dir = tempfile.gettempdir()
        for job_index in range(_deep_scan_state['total_jobs']):
            try:
                image_list_json = os.path.join(temp_dir, f"atomic_job_{job_index}_images.json")
                output_json = os.path.join(temp_dir, f"atomic_job_{job_index}_result.json")
                batch_file = os.path.join(temp_dir, f"atomic_job_{job_index}_launcher.bat")
                stdout_log = os.path.join(temp_dir, f"atomic_job_{job_index}_stdout.log")
                stderr_log = os.path.join(temp_dir, f"atomic_job_{job_index}_stderr.log")
                for f in [image_list_json, output_json, output_json + '.tmp', batch_file, stdout_log, stderr_log]:
                    if os.path.exists(f):
                        try:
                            os.remove(f)
                        except:
                            pass
            except:
                pass
        
        config.debug_print("[Atomic Debug] Deep scan cancelled and cleaned up")
        return None
    
    # Check for timeout
    elapsed_time = time.time() - _deep_scan_state['start_time']
    if elapsed_time > _deep_scan_state['timeout_seconds']:
        config.debug_print(f"[Atomic Error] Deep scan timeout after {elapsed_time:.1f} seconds")
        # Kill all processes
        for job_index, process in _deep_scan_state['worker_processes']:
            if job_index not in _deep_scan_state['completed_jobs']:
                try:
                    process.kill()
                except:
                    pass
        
        _deep_scan_state['is_running'] = False
        _safe_set_atom_property(atom, 'is_operation_running', False)
        _safe_set_atom_property(atom, 'operation_status', "Deep scan timed out")
        
        # Stop the operation - no fallback
        config.debug_print("[Atomic Error] Deep scan timed out")
        _clear_image_scan_cache()
        return None
    
    # Check process status
    all_complete = True
    completed_count = 0
    failed_processes = []
    running_count = 0
    
    for job_index, process in _deep_scan_state['worker_processes']:
        if job_index in _deep_scan_state['completed_jobs']:
            completed_count += 1
            continue
        
        # Check if output file exists (primary method for detached processes)
        output_json = _deep_scan_state['job_outputs'].get(job_index)
        if output_json and os.path.exists(output_json):
            # Output file exists - job completed
            _deep_scan_state['completed_jobs'].add(job_index)
            completed_count += 1
            config.debug_print(f"[Atomic Debug] Job {job_index} completed (output file found)")
            continue
        
        # Check if Blender process is actually running by checking log files
        stdout_log = os.path.join(tempfile.gettempdir(), f"atomic_job_{job_index}_stdout.log")
        stderr_log = os.path.join(tempfile.gettempdir(), f"atomic_job_{job_index}_stderr.log")
        
        # If log files exist and have content, process is running
        process_running = False
        if os.path.exists(stdout_log) or os.path.exists(stderr_log):
            # Check if log files are being written to (recent modification)
            current_time = time.time()
            for log_file in [stdout_log, stderr_log]:
                if os.path.exists(log_file):
                    mtime = os.path.getmtime(log_file)
                    # If modified in last 30 seconds, process is likely running
                    if current_time - mtime < 30:
                        process_running = True
                        running_count += 1
                        break
        
        # Also try to poll the process (works for non-detached processes)
        try:
            return_code = process.poll()
            if return_code is None:
                # Process still running (or detached and we can't poll)
                all_complete = False
                if not process_running:
                    running_count += 1  # Assume running if we can't tell
            else:
                # Process finished - but check if output file exists
                if output_json and os.path.exists(output_json):
                    _deep_scan_state['completed_jobs'].add(job_index)
                    completed_count += 1
                else:
                    # Process exited but no output - might have failed
                    config.debug_print(f"[Atomic Warning] Worker process {job_index} exited with code {return_code} but no output file")
                    # Read log files to see what happened
                    if os.path.exists(stdout_log):
                        try:
                            with open(stdout_log, 'r', encoding='utf-8', errors='ignore') as f:
                                stdout_content = f.read()
                                if stdout_content:
                                    # Show last 1000 chars which usually has the important info
                                    config.debug_print(f"[Atomic Debug] Worker {job_index} stdout (last 1000 chars): ...{stdout_content[-1000:]}")
                        except Exception as e:
                            config.debug_print(f"[Atomic Debug] Could not read stdout log: {e}")
                    if os.path.exists(stderr_log):
                        try:
                            with open(stderr_log, 'r', encoding='utf-8', errors='ignore') as f:
                                stderr_content = f.read()
                                if stderr_content:
                                    # Show last 1000 chars which usually has the important info
                                    config.debug_print(f"[Atomic Debug] Worker {job_index} stderr (last 1000 chars): ...{stderr_content[-1000:]}")
                        except Exception as e:
                            config.debug_print(f"[Atomic Debug] Could not read stderr log: {e}")
                    failed_processes.append(job_index)
                    all_complete = False
        except (OSError, ValueError) as e:
            # Process might be detached - can't poll, check output file and logs instead
            # This is expected for processes launched via 'start' command
            if not output_json or not os.path.exists(output_json):
                all_complete = False
                if process_running:
                    running_count += 1
        except Exception as e:
            config.debug_print(f"[Atomic Error] Error checking process {job_index}: {e}")
            # If we can't check the process, check if output file exists
            if output_json and os.path.exists(output_json):
                _deep_scan_state['completed_jobs'].add(job_index)
                completed_count += 1
            else:
                all_complete = False
                if process_running:
                    running_count += 1
    
    # Update progress - always report, even if we can't poll processes
    total_jobs = _deep_scan_state['total_jobs']
    if total_jobs > 0:
        progress = (completed_count / total_jobs) * 90.0  # Reserve 10% for merging
        _safe_set_atom_property(atom, 'operation_progress', progress)
        _safe_set_atom_property(atom, 'operation_status', f"Deep scan: {completed_count}/{total_jobs} jobs completed, {running_count} running...")
        config.debug_print(f"[Atomic Debug] Progress: {completed_count}/{total_jobs} completed, {running_count} running, {total_jobs - completed_count - running_count} pending")
    
    # If all processes complete, merge results
    if all_complete:
        _safe_set_atom_property(atom, 'operation_status', "Merging results...")
        _safe_set_atom_property(atom, 'operation_progress', 90.0)
        
        # Merge results from all jobs
        unused_images = []
        merged_cache = {
            'image_all_results': {},
            'image_materials_results': {},
            'material_objects_results': {},
            'object_all_results': {},
        }
        
        failed_jobs = []
        
        for job_index in range(total_jobs):
            output_json = _deep_scan_state['job_outputs'].get(job_index)
            if not output_json or not os.path.exists(output_json):
                config.debug_print(f"[Atomic Warning] Missing output for job {job_index}")
                failed_jobs.append(job_index)
                continue
            
            try:
                with open(output_json, 'r', encoding='utf-8') as f:
                    job_result = json.load(f)
                
                if not job_result.get('success', False):
                    config.debug_print(f"[Atomic Warning] Job {job_index} failed: {job_result.get('error', 'Unknown error')}")
                    failed_jobs.append(job_index)
                    continue
                
                # Merge unused images
                unused_images.extend(job_result.get('unused_images', []))
                
                # Merge cache data
                job_cache = job_result.get('image_scan_cache', {})
                merged_cache['image_all_results'].update(job_cache.get('image_all_results', {}))
                merged_cache['image_materials_results'].update(job_cache.get('image_materials_results', {}))
                merged_cache['material_objects_results'].update(job_cache.get('material_objects_results', {}))
                merged_cache['object_all_results'].update(job_cache.get('object_all_results', {}))
                
            except (json.JSONDecodeError, IOError, OSError) as e:
                config.debug_print(f"[Atomic Error] Failed to read job {job_index} result: {e}")
                failed_jobs.append(job_index)
        
        # Update global cache
        global _image_scan_cache
        _image_scan_cache = merged_cache
        
        # Store results in scan state
        if _scan_state:
            if 'results' not in _scan_state or _scan_state['results'] is None:
                _scan_state['results'] = {}
            _scan_state['results']['images'] = unused_images
            config.debug_print(f"[Atomic Debug] Deep scan: Stored {len(unused_images)} unused images in scan results")
            config.debug_print(f"[Atomic Debug] Deep scan: Full results dict now has keys: {list(_scan_state['results'].keys())}")
        
        # Save cache to disk
        if _scan_state and 'results' in _scan_state:
            _save_cache_to_disk(_scan_state['results'], merged_cache)
        
        # Handle failed jobs - if too many failed, fall back to single-threaded
        if len(failed_jobs) > 0:
            config.debug_print(f"[Atomic Warning] {len(failed_jobs)} jobs failed: {failed_jobs}")
            if len(failed_jobs) >= total_jobs:
                # All jobs failed, fall back to single-threaded
                config.debug_print("[Atomic Error] All jobs failed")
                _deep_scan_state['is_running'] = False
                _clear_image_scan_cache()
                _safe_set_atom_property(atom, 'is_operation_running', False)
                _safe_set_atom_property(atom, 'operation_status', "All jobs failed")
                # Stop the operation - no fallback
                return None
        
        # Cleanup temporary files
        temp_dir = tempfile.gettempdir()
        for job_index in range(total_jobs):
            try:
                image_list_json = os.path.join(temp_dir, f"atomic_job_{job_index}_images.json")
                output_json = os.path.join(temp_dir, f"atomic_job_{job_index}_result.json")
                if os.path.exists(image_list_json):
                    os.remove(image_list_json)
                if os.path.exists(output_json):
                    os.remove(output_json)
                if os.path.exists(output_json + '.tmp'):
                    os.remove(output_json + '.tmp')
            except Exception as e:
                config.debug_print(f"[Atomic Debug] Error cleaning up temp files for job {job_index}: {e}")
        
        _deep_scan_state['is_running'] = False
        _safe_set_atom_property(atom, 'operation_progress', 100.0)
        _safe_set_atom_property(atom, 'operation_status', f"Deep scan complete! Found {len(unused_images)} unused images")
        
        # Continue with normal scan flow - move to next category
        if _scan_state:
            _scan_state['current_category_index'] += 1
            _scan_state['status_updated'] = False
            # Clear image-specific state
            _scan_state['images_list'] = None
            _scan_state['images_index'] = 0
            _scan_state['images_unused'] = []
            
            # Restart unified scan timer to continue with next category
            total_categories = len(_scan_state['categories_to_scan'])
            progress = (_scan_state['current_category_index'] / total_categories) * 50.0
            _safe_set_atom_property(atom, 'operation_progress', progress)
        
        # Redraw UI
        for area in bpy.context.screen.areas:
            area.tag_redraw()
        
        # Restart unified scan to continue with next category
        bpy.app.timers.register(_process_unified_scan_step)
        return None  # Stop this timer
    
    # Continue polling
    return 0.5  # Check every 0.5 seconds


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
        config.debug_print("[Atomic Debug] Cache cleared manually")
        return {'FINISHED'}


# Atomic Data Manager Cancel Operation Operator
class ATOMIC_OT_cancel_operation(bpy.types.Operator):
    """Cancel the current operation"""
    bl_idname = "atomic.cancel_operation"
    bl_label = "Cancel Operation"
    bl_description = "Cancel the currently running operation"

    def execute(self, context):
        atom = context.scene.atomic
        _safe_set_atom_property(atom, 'cancel_operation', True)
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

        # Delete all items synchronously
        deleted_count = 0
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
                    elif category == 'worlds':
                        if item_key in bpy.data.worlds:
                            bpy.data.worlds.remove(bpy.data.worlds[item_key])
                            deleted_count += 1
                except:
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
            return context.window_manager.invoke_props_dialog(self)
        
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
                return context.window_manager.invoke_props_dialog(self)
        
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
    atom = bpy.context.scene.atomic
    global _clean_execute_state
    
    # Check for cancellation
    if atom.cancel_operation:
        _safe_set_atom_property(atom, 'is_operation_running', False)
        _safe_set_atom_property(atom, 'operation_progress', 0.0)
        _safe_set_atom_property(atom, 'operation_status', "Operation cancelled")
        _safe_set_atom_property(atom, 'cancel_operation', False)
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
                elif category == 'worlds':
                    if item_key in bpy.data.worlds:
                        bpy.data.worlds.remove(bpy.data.worlds[item_key])
                
                _clean_execute_state['deleted_count'] += 1
            except:
                pass  # Item may have been deleted already or doesn't exist
            
            _clean_execute_state['current_item_index'] += 1
            progress = (_clean_execute_state['deleted_count'] / _clean_execute_state['total_items']) * 100.0
            _safe_set_atom_property(atom, 'operation_progress', progress)
            
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
    _safe_set_atom_property(atom, 'is_operation_running', False)
    _safe_set_atom_property(atom, 'operation_progress', 100.0)
    _safe_set_atom_property(atom, 'operation_status', f"Complete! Removed {deleted_count} unused data-blocks")
    
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


def _on_smart_select_quick_scan_complete(results, **kwargs):
    """Callback for Smart Select quick scan completion.
    Processes quick scan results and triggers full scan for detected categories."""
    global _smart_select_state
    
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
        _safe_set_atom_property(atom, 'is_operation_running', False)
        _safe_set_atom_property(atom, 'operation_progress', 100.0)
        _safe_set_atom_property(atom, 'operation_status', "Complete! No unused items found")
        _smart_select_state = None
        for area in bpy.context.screen.areas:
            area.tag_redraw()
        return
    
    # Start full scan for detected categories
    global _scan_state
    config.debug_print(f"[Atomic Debug] Smart Select: Quick scan complete, starting full scan for categories: {detected_categories}")
    _scan_state = {
        'mode': 'full',
        'categories_to_scan': detected_categories,
        'current_category_index': 0,
        'results': None,
        'status_updated': False,
        'images_list': None,
        'images_index': 0,
        'images_unused': [],
        'worlds_list': None,
        'worlds_index': 0,
        'callback': _on_smart_select_full_scan_complete,
        'callback_data': {}
    }
    
    bpy.app.timers.register(_process_unified_scan_step)
    _process_unified_scan_step()  # Call immediately to start processing


def _on_smart_select_full_scan_complete(results, **kwargs):
    """Callback for Smart Select full scan completion.
    Processes full scan results, caches them, and updates UI toggles."""
    global _smart_select_state, _unused_cache, _cache_valid
    
    # Store results
    _smart_select_state['all_unused'] = results
    
    # Cache the results
    _unused_cache = results
    _cache_valid = True
    
    atom = bpy.context.scene.atomic
    _safe_set_atom_property(atom, 'operation_progress', 75.0)
    
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
    atom.worlds = _smart_select_state['unused_flags'].get('worlds', False)
    
    # Operation complete
    _safe_set_atom_property(atom, 'is_operation_running', False)
    _safe_set_atom_property(atom, 'operation_progress', 100.0)
    _safe_set_atom_property(atom, 'operation_status', f"Complete! Found unused items in {len(_smart_select_state['detected_categories'])} categories")
    
    # Clear state
    _smart_select_state = None
    
    # Force UI update
    for area in bpy.context.screen.areas:
        area.tag_redraw()


def _on_clean_scan_complete(results, **kwargs):
    """Callback for Clean scan completion.
    Populates operator properties and shows dialog."""
    global _clean_operator_instance, _clean_invoke_state, _clean_pending_results, _clean_pending_categories
    
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
    
    # Operation complete - show dialog
    _safe_set_atom_property(atom, 'is_operation_running', False)
    _safe_set_atom_property(atom, 'operation_progress', 100.0)
    _safe_set_atom_property(atom, 'operation_status', "")
    
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
                    wm.invoke_props_dialog(operator_instance)
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
        from ..stats import unused, unused_parallel
        atom = bpy.context.scene.atomic
        global _scan_state, _unused_cache, _cache_valid
        
        config.debug_print(f"[Atomic Debug] Unified Scanner: _scan_state = {_scan_state}")
        
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
            for area in bpy.context.screen.areas:
                area.tag_redraw()
            return None
        
        # Check cache first (only for full scans)
        # BUT: Skip cache if images_deep_scan is enabled (force fresh scan)
        skip_cache = False
        config.debug_print(f"[Atomic Debug] Unified Scanner: Checking cache bypass. images in categories: {'images' in _scan_state['categories_to_scan']}, images_deep_scan={atom.images_deep_scan}")
        if 'images' in _scan_state['categories_to_scan'] and atom.images_deep_scan:
            skip_cache = True
            config.debug_print("[Atomic Debug] Unified Scanner: Deep scan enabled for images - bypassing cache")
            # Invalidate in-memory cache for images when deep scan is enabled
            if _unused_cache is not None and 'images' in _unused_cache:
                _unused_cache.pop('images', None)
                config.debug_print("[Atomic Debug] Unified Scanner: Cleared images from in-memory cache")
        
        if not skip_cache and _scan_state['mode'] == 'full' and _cache_valid and _unused_cache is not None:
            config.debug_print("[Atomic Debug] Unified Scanner: Using cached results")
            # Check if cache has all requested categories
            cache_has_all = all(cat in _unused_cache for cat in _scan_state['categories_to_scan'])
            if cache_has_all:
                # Filter cache to only include requested categories
                filtered_results = {cat: _unused_cache[cat] for cat in _scan_state['categories_to_scan']}
                _scan_state['results'] = filtered_results
                _safe_set_atom_property(atom, 'operation_progress', 50.0)
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
                if _scan_state['mode'] == 'quick':
                    _safe_set_atom_property(atom, 'operation_status', f"Scanning {category}...")
                else:
                    _safe_set_atom_property(atom, 'operation_status', f"Counting {category}...")
                progress = (_scan_state['current_category_index'] / total_categories) * 50.0
                _safe_set_atom_property(atom, 'operation_progress', progress)
                _scan_state['status_updated'] = True
                # Force UI update and return to let it refresh
                for area in bpy.context.screen.areas:
                    area.tag_redraw()
                return 0.01  # Return to let UI update
            
            config.debug_print(f"[Atomic Debug] Unified Scanner: Status already updated, processing category '{category}' (mode={_scan_state['mode']})")
            # Handle incremental image scanning (for full scans only)
            if category == 'images' and _scan_state['mode'] == 'full':
                config.debug_print(f"[Atomic Debug] Unified Scanner: Entering images block")
                config.debug_print(f"[Atomic Debug] Unified Scanner: atom object = {atom}, images_deep_scan attribute exists = {hasattr(atom, 'images_deep_scan')}")
                # Initialize image list if not done
                if _scan_state['images_list'] is None:
                    _scan_state['images_list'] = [img for img in bpy.data.images if not compat.is_library_or_override(img)]
                    _scan_state['images_index'] = 0
                    _scan_state['images_unused'] = []
                    if _scan_state['results'] is None:
                        _scan_state['results'] = {}
                    # Initialize images result list (will be populated by deep scan or single-threaded scan)
                    _scan_state['results'][category] = []
                    config.debug_print(f"[Atomic Debug] Unified Scanner: Initialized images result list, deep_scan={atom.images_deep_scan}")
                    config.debug_print(f"[Atomic Debug] Unified Scanner: atom.images_deep_scan type={type(atom.images_deep_scan)}, value={atom.images_deep_scan}")
                    
                    # Check if deep scan is enabled
                    if atom.images_deep_scan:
                        config.debug_print("[Atomic Debug] Unified Scanner: Deep scan IS enabled, entering deep scan block")
                        # When deep scan is enabled, always run a fresh scan (don't use cache)
                        # This ensures accurate results since cache might be stale or from a non-deep scan
                        config.debug_print("[Atomic Debug] Deep scan enabled - forcing fresh scan (ignoring cache)")
                        # Clear any existing cache to ensure fresh scan
                        cache_path = _get_cache_filepath()
                        if cache_path and os.path.exists(cache_path):
                            try:
                                os.remove(cache_path)
                                config.debug_print(f"[Atomic Debug] Removed stale cache file: {cache_path}")
                            except Exception as e:
                                config.debug_print(f"[Atomic Debug] Could not remove cache file: {e}")
                        
                        # Start deep scan (cache was ignored/removed above)
                        total_images = len(_scan_state['images_list'])
                        config.debug_print(f"[Atomic Debug] Starting deep scan for {total_images} images")
                        if total_images == 0:
                            # No images to scan
                            config.debug_print("[Atomic Debug] No images to scan")
                            _scan_state['results'][category] = []
                            _scan_state['current_category_index'] += 1
                            _scan_state['status_updated'] = False
                            return 0.01
                        
                        # Check preference for single-threaded vs multi-process
                        use_multi_process = False
                        if not config.single_threaded_image_deep_scan:
                            # Try multi-process implementation
                            blend_file_path = bpy.data.filepath
                            if blend_file_path:
                                # Calculate job distribution
                                num_jobs, images_per_job, worker_threads = _calculate_job_distribution(total_images)
                                config.debug_print(f"[Atomic Debug] Multi-process deep scan: {num_jobs} jobs, {images_per_job} images per job, {worker_threads} worker threads")
                                
                                # Split images into batches
                                image_batches = _split_images_into_batches(_scan_state['images_list'], images_per_job)
                                
                                # Initialize deep scan state
                                global _deep_scan_state
                                _deep_scan_state['is_running'] = True
                                _deep_scan_state['total_jobs'] = num_jobs
                                _deep_scan_state['images_per_job'] = images_per_job
                                _deep_scan_state['image_batches'] = image_batches
                                _deep_scan_state['start_time'] = time.time()
                                _deep_scan_state['completed_jobs'] = set()
                                _deep_scan_state['worker_processes'] = []
                                _deep_scan_state['job_outputs'] = {}
                                
                                # Launch worker processes
                                temp_dir = tempfile.gettempdir()
                                config.debug_print(f"[Atomic Debug] Launching {num_jobs} worker processes...")
                                if _launch_worker_processes(blend_file_path, image_batches, temp_dir):
                                    use_multi_process = True
                                    config.debug_print(f"[Atomic Debug] Successfully launched {num_jobs} worker processes")
                                    _safe_set_atom_property(atom, 'operation_status', f"Launching {num_jobs} worker processes...")
                                    # Start monitoring timer
                                    bpy.app.timers.register(_process_deep_scan_step)
                                    return 0.1  # Continue monitoring
                                else:
                                    config.debug_print("[Atomic Error] Failed to launch worker processes")
                                    _deep_scan_state['is_running'] = False
                                    _safe_set_atom_property(atom, 'is_operation_running', False)
                                    _safe_set_atom_property(atom, 'operation_status', "Failed to launch worker processes")
                                    return None  # Stop the operation - no fallback
                            else:
                                config.debug_print("[Atomic Error] Cannot run multi-process deep scan: blend file not saved")
                                _safe_set_atom_property(atom, 'is_operation_running', False)
                                _safe_set_atom_property(atom, 'operation_status', "Cannot run multi-process: blend file not saved")
                                return None  # Stop the operation - no fallback
                        
                        # If we get here and use_multi_process is False, it means we couldn't launch workers
                        # Don't fall back - just stop
                        if not use_multi_process:
                            config.debug_print("[Atomic Error] Multi-process deep scan not available")
                            _safe_set_atom_property(atom, 'is_operation_running', False)
                            _safe_set_atom_property(atom, 'operation_status', "Multi-process deep scan not available")
                            return None  # Stop - no fallback
                        
                        # Workers were launched successfully, timer is running
                        # Don't continue with single-threaded - just return and let timer handle it
                        return 0.1
                    else:
                        # Deep scan disabled, only do shallow checks (fast path only)
                        config.debug_print("[Atomic Debug] Deep scan checkbox disabled - skipping deep image checks")
                        _scan_state['results'][category] = []
                        # Move to next category immediately
                        _scan_state['current_category_index'] += 1
                        _scan_state['status_updated'] = False
                        progress = (_scan_state['current_category_index'] / len(_scan_state['categories_to_scan'])) * 50.0
                        _safe_set_atom_property(atom, 'operation_progress', progress)
                        for area in bpy.context.screen.areas:
                            area.tag_redraw()
                        return 0.01
                
                total_images = len(_scan_state['images_list'])
                
                if _scan_state['images_index'] < total_images:
                    # Check for cancellation before processing each image
                    if atom.cancel_operation:
                        _safe_set_atom_property(atom, 'is_operation_running', False)
                        _safe_set_atom_property(atom, 'operation_progress', 0.0)
                        _safe_set_atom_property(atom, 'operation_status', "Operation cancelled")
                        _safe_set_atom_property(atom, 'cancel_operation', False)
                        _scan_state = None
                        for area in bpy.context.screen.areas:
                            area.tag_redraw()
                        return None
                    
                    # Process one image at a time (no batching for better cancellation responsiveness)
                    current_index = _scan_state['images_index'] + 1
                    current_image = _scan_state['images_list'][_scan_state['images_index']]
                    
                    # Update status with current image
                    _safe_set_atom_property(atom, 'operation_status', f"Checking image {current_index}/{total_images}: {current_image.name[:25]}...")
                    
                    # Process single image
                    if _check_single_image(current_image):
                        _scan_state['images_unused'].append(current_image.name)
                    
                    _scan_state['images_index'] += 1
                    
                    # Update progress within images category
                    category_progress = (_scan_state['images_index'] / total_images) * (1.0 / total_categories)
                    base_progress = (_scan_state['current_category_index'] / total_categories) * 50.0
                    _safe_set_atom_property(atom, 'operation_progress', base_progress + category_progress * 50.0)
                    
                    # Force UI update
                    for area in bpy.context.screen.areas:
                        area.tag_redraw()
                    
                    return 0.01  # Continue processing images
                
                # All images processed, store result and move to next category
                if 'results' not in _scan_state:
                    _scan_state['results'] = {}
                _scan_state['results'][category] = _scan_state['images_unused']
                config.debug_print(f"[Atomic Debug] Single-threaded scan: Stored {len(_scan_state['images_unused'])} unused images in scan results")
                
                # Save cache to disk if deep scan was used
                if atom.images_deep_scan:
                    _save_cache_to_disk(_scan_state['results'], _image_scan_cache)
                
                _scan_state['images_list'] = None
                _scan_state['images_index'] = 0
                _scan_state['images_unused'] = []
                # Move to next category
                _scan_state['current_category_index'] += 1
                _scan_state['status_updated'] = False
                progress = (_scan_state['current_category_index'] / total_categories) * 50.0
                _safe_set_atom_property(atom, 'operation_progress', progress)
                for area in bpy.context.screen.areas:
                    area.tag_redraw()
                return 0.01  # Continue to next category
            
            # Handle incremental world scanning (for full scans only)
            elif category == 'worlds' and _scan_state['mode'] == 'full':
                config.debug_print(f"[Atomic Debug] Unified Scanner: Entering worlds block")
                # Initialize world list if not done
                if _scan_state['worlds_list'] is None:
                    _scan_state['worlds_list'] = [
                        w for w in bpy.data.worlds 
                        if not compat.is_library_or_override(w)
                    ]
                    _scan_state['worlds_index'] = 0
                    if _scan_state['results'] is None:
                        _scan_state['results'] = {}
                    _scan_state['results'][category] = []
                
                # Process one world per timer callback
                worlds_list = _scan_state['worlds_list']
                if _scan_state['worlds_index'] < len(worlds_list):
                    world = worlds_list[_scan_state['worlds_index']]
                    
                    # Check if world is unused
                    is_unused = world.users == 0 or (world.users == 1 and
                                                     world.use_fake_user and
                                                     config.include_fake_users)
                    if is_unused:
                        _scan_state['results'][category].append(world.name)
                    
                    _scan_state['worlds_index'] += 1
                    progress_base = (_scan_state['current_category_index'] / total_categories) * 50.0
                    world_progress = (_scan_state['worlds_index'] / len(worlds_list)) * (50.0 / total_categories)
                    _safe_set_atom_property(atom, 'operation_progress', progress_base + world_progress)
                    
                    # Force UI update
                    for area in bpy.context.screen.areas:
                        area.tag_redraw()
                    
                    return 0.01  # Continue processing this category
                else:
                    # Finished scanning worlds, move to next category
                    _scan_state['worlds_list'] = None
                    _scan_state['worlds_index'] = 0
                    # Move to next category
                    _scan_state['current_category_index'] += 1
                    _scan_state['status_updated'] = False
                    progress = (_scan_state['current_category_index'] / total_categories) * 50.0
                    _safe_set_atom_property(atom, 'operation_progress', progress)
                    for area in bpy.context.screen.areas:
                        area.tag_redraw()
                    return 0.01  # Continue to next category
            
            # Handle other categories (quick scan or full scan for non-incremental categories)
            else:
                config.debug_print(f"[Atomic Debug] Unified Scanner: Processing category '{category}' in else block (mode={_scan_state['mode']})")
                # Initialize results dict if needed (for both quick and full scans)
                if _scan_state['results'] is None:
                    _scan_state['results'] = {}
                
                if _scan_state['mode'] == 'quick':
                    # Quick scan: check if category has any unused items
                    config.debug_print(f"[Atomic Debug] Unified Scanner: Quick scan for '{category}'")
                    if category == 'collections':
                        result = unused_parallel._has_any_unused_collections()
                    elif category == 'images':
                        result = unused_parallel._has_any_unused_images()
                    elif category == 'lights':
                        result = unused_parallel._has_any_unused_lights()
                    elif category == 'materials':
                        result = unused_parallel._has_any_unused_materials()
                    elif category == 'node_groups':
                        result = unused_parallel._has_any_unused_node_groups()
                    elif category == 'objects':
                        result = unused_parallel._has_any_unused_objects()
                    elif category == 'particles':
                        result = unused_parallel._has_any_unused_particles()
                    elif category == 'textures':
                        result = unused_parallel._has_any_unused_textures()
                    elif category == 'armatures':
                        result = unused_parallel._has_any_unused_armatures()
                    elif category == 'worlds':
                        result = unused_parallel._has_any_unused_worlds()
                    else:
                        result = False
                    
                    _scan_state['results'][category] = result
                    config.debug_print(f"[Atomic Debug] Unified Scanner: Stored result for '{category}': {result}, results now: {_scan_state['results']}")
                
                else:  # mode == 'full'
                    config.debug_print(f"[Atomic Debug] Unified Scanner: Full scan for '{category}'")
                    # Full scan: get complete list of unused items
                    if category == 'collections':
                        unused_list = unused.collections_deep()
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
                    else:
                        unused_list = []
                    
                    if _scan_state['results'] is None:
                        _scan_state['results'] = {}
                    _scan_state['results'][category] = unused_list
                
                # Move to next category (for non-images/non-worlds categories)
                _scan_state['current_category_index'] += 1
                _scan_state['status_updated'] = False  # Reset for next category
                progress = (_scan_state['current_category_index'] / total_categories) * 50.0
                _safe_set_atom_property(atom, 'operation_progress', progress)
                config.debug_print(f"[Atomic Debug] Unified Scanner: Finished '{category}', moved to index {_scan_state['current_category_index']}/{total_categories}, results: {_scan_state['results']}")
                
                # Force UI update
                for area in bpy.context.screen.areas:
                    area.tag_redraw()
                
                return 0.01  # Continue processing
        
        # All categories scanned
        config.debug_print(f"[Atomic Debug] Unified Scanner: All categories scanned! current_index={_scan_state.get('current_category_index')}, total={total_categories}, results={_scan_state.get('results')}")
        _safe_set_atom_property(atom, 'operation_progress', 50.0)
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
            config.debug_print(f"[Atomic Debug] Unified Scanner: Calling callback with results: {_scan_state['results']}")
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
        config.debug_print(f"[Atomic Debug] _populate_unused_lists: images result = {len(images_result) if images_result else 'None'} items, deep_scan={atom.images_deep_scan}")
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
        config.debug_print(f"[Atomic Debug] Clean: _scan_state = {_scan_state}")
        
        # Check for cancellation
        if atom.cancel_operation:
            _safe_set_atom_property(atom, 'is_operation_running', False)
            _safe_set_atom_property(atom, 'operation_progress', 0.0)
            _safe_set_atom_property(atom, 'operation_status', "Operation cancelled")
            _safe_set_atom_property(atom, 'cancel_operation', False)
            _clean_invoke_state = None
            _scan_state = None
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
            'images_list': None,
            'images_index': 0,
            'images_unused': [],
            'worlds_list': None,
            'worlds_index': 0,
                'callback': _on_clean_scan_complete,
                'callback_data': {}
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
        config.debug_print(f"[Atomic Debug] Smart Select: _scan_state = {_scan_state}")
        
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
            'images_list': None,
            'images_index': 0,
            'images_unused': [],
            'worlds_list': None,
            'worlds_index': 0,
                'callback': _on_smart_select_quick_scan_complete,
                'callback_data': {}
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

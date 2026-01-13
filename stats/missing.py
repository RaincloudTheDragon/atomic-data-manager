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

This file contains functions that detect missing files in the Blender
project.

"""

import bpy
import os
from ..utils import version, compat


def get_missing(data):
    # returns a list of keys of unpacked data-blocks with non-existent
    # filepaths

    missing = []

    # list of keys that should not be flagged
    do_not_flag = ["Render Result", "Viewer Node", "D-NOISE Export"]

    for datablock in data:
        # Skip library-linked and override datablocks
        if compat.is_library_or_override(datablock):
            continue

        # the absolute path to our data-block
        abspath = bpy.path.abspath(datablock.filepath)

        # Check if data-block is packed
        # Blender 5.0+: Image objects use 'packed_files' (plural), Library objects use 'packed_file' (singular)
        # Blender 4.2/4.5: Both Image and Library objects use 'packed_file' (singular)
        is_packed = False
        if version.is_version_at_least(5, 0, 0):
            # Blender 5.0+: Check type-specific attributes
            if isinstance(datablock, bpy.types.Image):
                is_packed = bool(datablock.packed_files) if hasattr(datablock, 'packed_files') else False
            elif isinstance(datablock, bpy.types.Library):
                is_packed = bool(datablock.packed_file) if hasattr(datablock, 'packed_file') else False
        else:
            # Blender 4.2/4.5: Both use 'packed_file' (singular)
            is_packed = bool(datablock.packed_file) if hasattr(datablock, 'packed_file') else False

        # Check if file exists (with special handling for UDIM images)
        file_exists = False
        if abspath and isinstance(datablock, bpy.types.Image) and '<UDIM>' in abspath:
            # UDIM image: check if any UDIM tile files exist
            # UDIM tiles are numbered 1001, 1002, etc. (standard range is 1001-1099)
            # Check a reasonable range of UDIM tiles
            for udim_tile in range(1001, 1100):  # Check tiles 1001-1099
                udim_path = abspath.replace('<UDIM>', str(udim_tile))
                if os.path.isfile(udim_path):
                    file_exists = True
                    break
        elif abspath:
            # Regular file: check if it exists
            file_exists = os.path.isfile(abspath)

        # if data-block is not packed and has an invalid filepath
        if not is_packed and not file_exists:

            # if data-block is not in our do not flag list
            # append it to the missing data list
            if datablock.name not in do_not_flag:
                missing.append(datablock.name)

        # if data-block is packed but it does not have a filepath
        elif is_packed and not abspath:

            # if data-block is not in our do not flag list
            # append it to the missing data list
            if datablock.name not in do_not_flag:
                missing.append(datablock.name)

    return missing


def images():
    # returns a list of keys of images with a non-existent filepath
    return get_missing(bpy.data.images)


def libraries():
    # returns a list of keys of libraries with a non-existent filepath
    return get_missing(bpy.data.libraries)


def get_missing_library_info(library_key):
    """
    Get information about a missing library for matching and validation.
    
    Returns:
        dict with keys:
            - 'filepath': original filepath
            - 'filename': basename for matching
            - 'linked_data_blocks': list of data-block names linked from this library
    """
    if library_key not in bpy.data.libraries:
        return None
    
    library = bpy.data.libraries[library_key]
    filepath = library.filepath
    filename = os.path.basename(bpy.path.abspath(filepath)) if filepath else ""
    
    # Get linked data-block names (collections, objects, materials, etc.)
    linked_data_blocks = []
    try:
        # Collections
        for collection in bpy.data.collections:
            if collection.library == library:
                linked_data_blocks.append(('COLLECTION', collection.name))
        # Objects
        for obj in bpy.data.objects:
            if obj.library == library:
                linked_data_blocks.append(('OBJECT', obj.name))
        # Materials
        for material in bpy.data.materials:
            if material.library == library:
                linked_data_blocks.append(('MATERIAL', material.name))
        # Meshes
        for mesh in bpy.data.meshes:
            if mesh.library == library:
                linked_data_blocks.append(('MESH', mesh.name))
        # Armatures
        for armature in bpy.data.armatures:
            if armature.library == library:
                linked_data_blocks.append(('ARMATURE', armature.name))
    except Exception:
        # If we can't access library data, return what we have
        pass
    
    return {
        'filepath': filepath,
        'filename': filename,
        'linked_data_blocks': linked_data_blocks
    }

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
from ..utils import version


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

        # if data-block is not packed and has an invalid filepath
        if not is_packed and not os.path.isfile(abspath):

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

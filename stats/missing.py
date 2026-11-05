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


def _is_pack_linked_library(datablock):
    """True for Blender 5+ archive libraries or sources that own pack-linked archives.

    Pack-linked Essentials-style assets keep data in the current .blend via archive libraries. The source library filepath may still point at a missing on-disk path (Blender's own report_missing_files does too); that is not session-missing for Atomic.
    """
    if not isinstance(datablock, bpy.types.Library):
        return False
    if getattr(datablock, "is_archive", False):
        return True
    if getattr(datablock, "archive_parent_library", None) is not None:
        return True
    archives = getattr(datablock, "archive_libraries", None)
    if archives is not None:
        try:
            if len(archives) > 0:
                return True
        except Exception:
            pass
    # Load-order fallback: archive child may exist before archive_libraries is populated on the parent
    try:
        for other in bpy.data.libraries:
            parent = getattr(other, "archive_parent_library", None)
            if parent is not None and parent == datablock:
                return True
    except Exception:
        pass
    # Any ID still tagged pack-linked from this library
    try:
        for idb in datablock.users_id:
            if getattr(idb, "is_linked_packed", False):
                return True
    except Exception:
        pass
    return False


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

        # Pack-linked archive libs / their parents: data lives in this .blend
        if _is_pack_linked_library(datablock):
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


# File-bearing bpy.data collections scanned for nested (linked) missing paths.
# Libraries are handled separately via Library.parent (indirect link chains).
# Not session-relinkable — fix inside the owning .blend.
_LINKED_FILE_COLLECTIONS = (
    ("images", "IMAGE"),
    ("fonts", "FONT"),
    ("sounds", "SOUND"),
    ("movieclips", "MOVIECLIP"),
    ("volumes", "VOLUME"),
    ("cache_files", "CACHE_FILE"),
)


def _is_packed(datablock):
    """True when the datablock has packed file data (singular or plural API)."""
    if hasattr(datablock, "packed_files") and datablock.packed_files:
        return True
    if hasattr(datablock, "packed_file") and datablock.packed_file:
        return True
    return False


def _filepath_exists(abspath, is_udim=False):
    """True when abspath exists on disk (any UDIM tile 1001-1099 if is_udim)."""
    if not abspath:
        return False
    if is_udim or "<UDIM>" in abspath:
        for tile in range(1001, 1100):
            if os.path.isfile(abspath.replace("<UDIM>", str(tile))):
                return True
        return False
    return os.path.isfile(abspath)


def _library_abspath(library, _seen=None):
    """Absolute path for a Library, resolving ``//`` via parent chain then session.

    Indirect libs (Library.parent set) store paths relative to the parent .blend; resolving them against the current session file alone is wrong.
    """
    if library is None:
        return ""
    filepath = library.filepath or ""
    if not filepath:
        return ""

    seen = _seen if _seen is not None else set()
    lib_ptr = library.as_pointer()
    if lib_ptr in seen:
        return bpy.path.abspath(filepath)
    seen.add(lib_ptr)

    parent = getattr(library, "parent", None)
    if parent is not None:
        parent_abs = _library_abspath(parent, seen)
        if parent_abs:
            try:
                return bpy.path.abspath(filepath, start=os.path.dirname(parent_abs))
            except Exception:
                pass
    try:
        return bpy.path.abspath(filepath)
    except Exception:
        return filepath


def _resolve_vs_library(filepath, library):
    """Absolute path for a linked datablock filepath, relative to its library file.

    Relative ``//`` paths inside a linked .blend must resolve against that library (parent-chain aware), not the current session file.
    """
    if not filepath or library is None:
        return ""
    lib_abs = _library_abspath(library)
    if not lib_abs:
        return bpy.path.abspath(filepath)
    try:
        return bpy.path.abspath(filepath, start=os.path.dirname(lib_abs))
    except Exception:
        return bpy.path.abspath(filepath)


def _link_chain_basenames(library):
    """Basenames from root parent → library (empty if library is directly linked)."""
    if library is None:
        return []
    chain = []
    seen = set()
    node = library
    while node is not None:
        ptr = node.as_pointer()
        if ptr in seen:
            break
        seen.add(ptr)
        abs_path = _library_abspath(node)
        chain.append(os.path.basename(abs_path) if abs_path else node.name)
        node = getattr(node, "parent", None)
    chain.reverse()
    return chain


def _ensure_nested_lib_entry(by_lib, library):
    """Get or create the nested-missing entry for a library that owns broken paths."""
    lib_key = library.name
    entry = by_lib.get(lib_key)
    if entry is not None:
        return entry

    lib_abs = _library_abspath(library)
    entry = {
        "library_key": lib_key,
        "library_filepath": lib_abs,
        "library_basename": os.path.basename(lib_abs) if lib_abs else lib_key,
        "link_chain": _link_chain_basenames(library),
        "missing_count": 0,
        "missing_examples": [],
    }
    by_lib[lib_key] = entry
    return entry


def _record_nested_miss(by_lib, owner_library, type_label, name, filepath):
    """Increment nested-missing stats on the library that stores the broken path."""
    entry = _ensure_nested_lib_entry(by_lib, owner_library)
    entry["missing_count"] += 1
    if len(entry["missing_examples"]) < 5:
        entry["missing_examples"].append(
            {
                "type": type_label,
                "name": name,
                "filepath": filepath,
            }
        )


def libraries_with_nested_missing():
    """Libraries that store missing deps (file paths or nested .blend links).

    Covers linked images/fonts/etc. and indirect Library.parent link chains. Session-local missing images/libraries stay in :func:`images` / :func:`libraries` (relinkable here). These results are source-file intervention only — Open the library that owns the broken path (for a missing nested .blend link, that is the parent library).

    Returns:
        list[dict]: library_key, library_filepath, library_basename, link_chain, missing_count, missing_examples (up to 5 of type/name/filepath).
    """
    by_lib = {}

    # File-bearing IDs linked from a library (path lives in that library)
    for attr, type_label in _LINKED_FILE_COLLECTIONS:
        coll = getattr(bpy.data, attr, None)
        if coll is None:
            continue
        for datablock in coll:
            # Strictly linked only — local overrides may be editable in-session.
            library = getattr(datablock, "library", None)
            if library is None:
                continue
            filepath = getattr(datablock, "filepath", None) or ""
            # Packed file blobs OR Blender 5+ pack-linked (archive) IDs live in this .blend
            if (
                not filepath
                or _is_packed(datablock)
                or getattr(datablock, "is_linked_packed", False)
            ):
                continue

            is_udim = False
            if type_label == "IMAGE":
                source = getattr(datablock, "source", "") or ""
                is_udim = source == "TILED" or "<UDIM>" in filepath

            abs_path = _resolve_vs_library(filepath, library)
            if _filepath_exists(abs_path, is_udim=is_udim):
                continue

            _record_nested_miss(by_lib, library, type_label, datablock.name, filepath)

    # Indirect .blend links: child.Library.parent is the blend that stores the link
    for child in bpy.data.libraries:
        # Archive libs store pack-linked IDs in this file; never nested-missing
        if _is_pack_linked_library(child):
            continue
        parent = getattr(child, "parent", None)
        if parent is None:
            continue
        filepath = child.filepath or ""
        if not filepath or _is_packed(child):
            continue

        abs_path = _library_abspath(child)
        if _filepath_exists(abs_path):
            continue

        _record_nested_miss(by_lib, parent, "LIBRARY", child.name, filepath)

    return sorted(by_lib.values(), key=lambda e: (e["library_basename"] or "").lower())


def get_missing_image_info(image_key):
    """
    Info for matching/relinking a missing image.

    Returns:
        dict with filepath, filename (basename), source, is_udim — or None.
    """
    if image_key not in bpy.data.images:
        return None

    image = bpy.data.images[image_key]
    filepath = image.filepath or ""
    try:
        abs_path = bpy.path.abspath(filepath) if filepath else ""
    except Exception:
        abs_path = filepath
    filename = os.path.basename(abs_path) if abs_path else ""

    source = getattr(image, "source", "FILE") or "FILE"
    is_udim = (
        source == "TILED"
        or "<UDIM>" in (filepath or "")
        or "<UDIM>" in (abs_path or "")
    )

    return {
        "filepath": filepath,
        "filename": filename,
        "source": source,
        "is_udim": is_udim,
    }


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


def expected_abspath(kind, key):
    """Absolute expected filepath for a missing IMAGE or LIBRARY datablock key.

    Returns '' when the key is unknown or has no filepath.
    """
    if kind == "IMAGE":
        info = get_missing_image_info(key)
    elif kind == "LIBRARY":
        info = get_missing_library_info(key)
    else:
        return ""
    if not info:
        return ""
    filepath = info.get("filepath") or ""
    if not filepath:
        return ""
    try:
        return os.path.normpath(bpy.path.abspath(filepath))
    except Exception:
        return filepath


def nested_example_abspath(entry, example):
    """Absolute expected path for a nested-missing example, resolved vs the owning library."""
    filepath = (example or {}).get("filepath") or ""
    if not filepath:
        return ""
    lib_abs = (entry or {}).get("library_filepath") or ""
    if lib_abs:
        try:
            return os.path.normpath(
                bpy.path.abspath(filepath, start=os.path.dirname(lib_abs))
            )
        except Exception:
            pass
    try:
        return os.path.normpath(bpy.path.abspath(filepath))
    except Exception:
        return filepath

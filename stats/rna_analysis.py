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

This file contains RNA-based analysis functions for detecting unused data-blocks.
Uses Blender's RNA introspection system to build a dependency graph.

"""

import bpy
import json
import os
from .. import config
from ..utils import compat


# Data-block types we care about for dependency analysis
# Note: We rebuild this dynamically in get_data_block_types() to avoid stale references
# after opening a new blend file
_DATA_BLOCK_TYPE_NAMES = [
    'images', 'materials', 'objects', 'collections', 'node_groups',
    'textures', 'lights', 'armatures', 'worlds', 'particles', 'meshes', 'scenes'
]


def _get_data_block_types():
    """
    Safely get a dictionary of data-block types with fresh references.
    This must be called each time to avoid stale references after opening a new blend file.
    """
    try:
        return {
            'images': bpy.data.images,
            'materials': bpy.data.materials,
            'objects': bpy.data.objects,
            'collections': bpy.data.collections,
            'node_groups': bpy.data.node_groups,
            'textures': bpy.data.textures,
            'lights': bpy.data.lights,
            'armatures': bpy.data.armatures,
            'worlds': bpy.data.worlds,
            'particles': bpy.data.particles,
            'meshes': bpy.data.meshes,
            'scenes': bpy.data.scenes,
        }
    except Exception:
        # If accessing bpy.data fails, return empty dict
        return {}


def _safe_snapshot(collection):
    """
    Create a safe snapshot of a Blender collection/iterable.
    Returns an empty list if the collection is invalid (e.g., after opening a new blend file).
    
    This function catches all exceptions because Blender's RNA system can crash
    at the C level when collections become invalid after opening a new blend file.
    """
    try:
        # First, try to check if the collection is accessible
        # This might fail if the collection is invalid, but it's safer than
        # directly calling list() which can crash in Blender's C code
        if collection is None:
            return []
        
        # Try to get an iterator - this is where crashes often occur
        # when collections become invalid after opening a new blend file
        return list(collection)
    except Exception:
        # Catch all exceptions (RuntimeError, ReferenceError, SystemError, etc.)
        # because Blender's RNA system can raise various exceptions or crash
        # when collections become invalid after opening a new blend file
        return []


def _is_id_datablock_property(prop):
    """Check if a property is a pointer to an ID data-block."""
    if prop.type != 'POINTER':
        return False
    
    # Check if it points to an ID data-block type
    if hasattr(prop, 'fixed_type') and prop.fixed_type:
        rna_type = prop.fixed_type
        # ID data-blocks have bl_rna and are in bpy.data
        if hasattr(rna_type, 'bl_rna'):
            rna_identifier = rna_type.bl_rna.identifier
            # Check if this is a known ID data-block type
            try:
                data_block_types = _get_data_block_types()
                for data_type, data_collection in data_block_types.items():
                    try:
                        if hasattr(data_collection, 'bl_rna'):
                            if data_collection.bl_rna.identifier == rna_identifier:
                                return True
                    except Exception:
                        continue
            except Exception:
                pass
    return False


def _is_id_datablock_collection(prop):
    """Check if a property is a collection containing ID data-blocks."""
    if prop.type != 'COLLECTION':
        return False
    
    if hasattr(prop, 'fixed_type') and prop.fixed_type:
        rna_type = prop.fixed_type
        if hasattr(rna_type, 'bl_rna'):
            rna_identifier = rna_type.bl_rna.identifier
            # Check if collection items are ID data-blocks
            try:
                data_block_types = _get_data_block_types()
                for data_type, data_collection in data_block_types.items():
                    try:
                        if hasattr(data_collection, 'bl_rna'):
                            if data_collection.bl_rna.identifier == rna_identifier:
                                return True
                    except Exception:
                        continue
            except Exception:
                pass
    return False


def _extract_references_from_datablock(datablock, depth=0, max_depth=5):
    """
    Extract all data-block references from a single data-block instance.
    
    Args:
        datablock: The data-block to extract references from
        depth: Current recursion depth (default: 0)
        max_depth: Maximum recursion depth to prevent infinite loops (default: 5)
    """
    references = []
    
    # Prevent infinite recursion
    if depth >= max_depth:
        return references
    
    # Safety check: ensure datablock is valid
    if datablock is None:
        return references
    
    try:
        rna = datablock.bl_rna
    except (AttributeError, TypeError, RuntimeError):
        return references
    
    try:
        for prop in rna.properties:
            # Skip internal/read-only properties
            if prop.identifier.startswith('_') or prop.is_readonly:
                continue
            
            # Check for pointer properties to ID data-blocks
            if _is_id_datablock_property(prop):
                try:
                    value = getattr(datablock, prop.identifier, None)
                    if value and hasattr(value, 'name'):
                        # Additional safety: check if value is still valid
                        try:
                            name = value.name
                            type_identifier = 'unknown'
                            if hasattr(value, 'bl_rna'):
                                try:
                                    type_identifier = value.bl_rna.identifier
                                except (AttributeError, RuntimeError):
                                    pass
                            
                            references.append({
                                'property': prop.identifier,
                                'type': type_identifier,
                                'name': name
                            })
                        except (AttributeError, RuntimeError, ReferenceError):
                            # Data-block may have been deleted or is invalid
                            pass
                except (AttributeError, TypeError, RuntimeError):
                    pass
            
            # Check for collection properties containing ID data-blocks
            elif _is_id_datablock_collection(prop):
                try:
                    collection = getattr(datablock, prop.identifier, None)
                    if collection:
                        # Use snapshot to avoid iteration issues
                        items = _safe_snapshot(collection)
                        if not items:
                            continue
                        
                        for item in items:
                            if item is None:
                                continue
                            try:
                                # Extract references from items that have names (e.g., material slots)
                                if hasattr(item, 'name'):
                                    name = item.name
                                    type_identifier = 'unknown'
                                    if hasattr(item, 'bl_rna'):
                                        try:
                                            type_identifier = item.bl_rna.identifier
                                        except (AttributeError, RuntimeError):
                                            pass
                                    
                                    references.append({
                                        'property': prop.identifier,
                                        'type': type_identifier,
                                        'name': name
                                    })
                                
                                # IMPORTANT: Also recursively extract from collection items (e.g., modifiers)
                                # even if they don't have names, to capture nested references like modifier.texture
                                # This ensures we capture references even if explicit handling fails
                                if depth < max_depth:
                                    try:
                                        nested_refs = _extract_references_from_datablock(item, depth + 1, max_depth)
                                        # Prepend the collection property name to nested property paths
                                        for nested_ref in nested_refs:
                                            nested_prop = nested_ref.get('property', '')
                                            if nested_prop:
                                                nested_ref['property'] = f"{prop.identifier}.{nested_prop}"
                                        references.extend(nested_refs)
                                    except (AttributeError, TypeError, RecursionError, RuntimeError):
                                        # Recursive extraction may fail for some items
                                        pass
                            except (AttributeError, RuntimeError, ReferenceError):
                                # Item may have been deleted or is invalid
                                continue
                except (AttributeError, TypeError, RuntimeError):
                    pass
            
            # Special handling for nested structures (e.g., node trees)
            # Check if property is a pointer that might contain nested references
            elif prop.type == 'POINTER' and hasattr(prop, 'fixed_type'):
                try:
                    value = getattr(datablock, prop.identifier, None)
                    if value:
                        # Recursively extract from nested structures with depth limit
                        nested_refs = _extract_references_from_datablock(value, depth + 1, max_depth)
                        references.extend(nested_refs)
                except (AttributeError, TypeError, RecursionError, RuntimeError):
                    pass
    
    except (AttributeError, TypeError, RuntimeError):
        pass
    
    return references


def _extract_node_tree_references(node_tree):
    """Extract references from a node tree (materials, compositor, etc.)."""
    references = []
    
    if not node_tree:
        return references
    
    try:
        # Create a snapshot of nodes to avoid iteration issues
        nodes = _safe_snapshot(node_tree.nodes)
        if not nodes:
            return references
        
        for node in nodes:
            if node is None:
                continue
            try:
                # Check node properties for data-block references
                node_refs = _extract_references_from_datablock(node)
                references.extend(node_refs)
                
                # Special handling for group nodes
                if hasattr(node, 'node_tree') and node.node_tree:
                    try:
                        ng = node.node_tree
                        if ng and hasattr(ng, 'name'):
                            references.append({
                                'property': 'node_tree',
                                'type': 'NodeTree',
                                'name': ng.name
                            })
                        # Recursively check nested node tree
                        nested_refs = _extract_node_tree_references(node.node_tree)
                        references.extend(nested_refs)
                    except (AttributeError, RuntimeError, ReferenceError):
                        pass
                
                # Special handling for nodes with image property (Image Texture nodes, etc.)
                if hasattr(node, 'image') and node.image:
                    try:
                        img = node.image
                        if img and hasattr(img, 'name') and not compat.is_library_or_override(img):
                            references.append({
                                'property': 'image',
                                'type': 'Image',
                                'name': img.name
                            })
                    except (AttributeError, RuntimeError, ReferenceError):
                        pass
                
                # Special handling for nodes with material input sockets (Menu Switch, Set Material, etc.)
                if hasattr(node, 'inputs'):
                    try:
                        for input_socket in node.inputs:
                            try:
                                # Check socket type - material sockets are typically 'MATERIAL' type
                                socket_type = getattr(input_socket, 'type', '')
                                if socket_type == 'MATERIAL' or 'material' in str(socket_type).lower():
                                    # Check if this socket has a default_value that is a material
                                    if hasattr(input_socket, 'default_value') and input_socket.default_value:
                                        socket_material = input_socket.default_value
                                        # Check if it's a material datablock
                                        if socket_material and hasattr(socket_material, 'name') and not compat.is_library_or_override(socket_material):
                                            references.append({
                                                'property': 'inputs.material',
                                                'type': 'Material',
                                                'name': socket_material.name
                                            })
                            except (AttributeError, ReferenceError, RuntimeError, TypeError, KeyError):
                                continue  # Skip this socket if we can't access it
                    except (AttributeError, RuntimeError, ReferenceError):
                        pass
            except (AttributeError, RuntimeError, ReferenceError):
                # Node may have been deleted or is invalid
                continue
    except (AttributeError, TypeError):
        pass
    
    return references


def dump_rna_references(output_path=None):
    """
    Dump all data-block references found via RNA introspection to JSON.
    
    Args:
        output_path: Optional path to save JSON file. If None, returns dict.
    
    Returns:
        Dictionary with structure: {data_type: {item_name: {references: [...], referenced_by: []}}}
    """
    config.debug_print("[Atomic Debug] RNA Analysis: Starting reference dump...")
    
    rna_data = {}
    reference_map = {}  # Track reverse references: {target_type: {target_name: [source_info]}}
    
    # Get fresh references to data-block types (critical after opening a new blend file)
    try:
        data_block_types = _get_data_block_types()
    except Exception:
        config.debug_print("[Atomic Debug] RNA Analysis: Failed to get data-block types, returning empty data")
        if output_path:
            with open(output_path, 'w') as f:
                json.dump({}, f, indent=2)
        return {}
    
    # Initialize structure
    for data_type in data_block_types.keys():
        rna_data[data_type] = {}
    
    # Extract references from all data-blocks
    # Wrap in try-except to handle crashes when collections become invalid
    for data_type, data_collection in data_block_types.items():
        try:
            config.debug_print(f"[Atomic Debug] RNA Analysis: Processing {data_type}...")
            
            # Create a snapshot of the data collection to avoid iteration issues
            # This is critical when a new blend file is opened - old data-blocks become invalid
            datablocks = _safe_snapshot(data_collection)
            
            for datablock in datablocks:
                # Skip library-linked/override datablocks *except* for certain "reference-only"
                # roots (not cleanable themselves) that can still reference local data that
                # should not be flagged as unused (e.g. local materials assigned to linked objects).
                is_linked_or_override = False
                try:
                    is_linked_or_override = compat.is_library_or_override(datablock)
                except (AttributeError, RuntimeError, ReferenceError):
                    # Datablock may be invalid
                    continue
                if is_linked_or_override and data_type not in {'objects'}:
                    continue
                
                try:
                    item_name = datablock.name
                except (AttributeError, RuntimeError, ReferenceError):
                    # Datablock may have been deleted or is invalid
                    continue
                
                references = []
                
                # Extract direct references
                try:
                    direct_refs = _extract_references_from_datablock(datablock)
                    references.extend(direct_refs)
                except (AttributeError, RuntimeError, ReferenceError):
                    # Datablock may have become invalid during processing
                    direct_refs = []
                
                # Special handling for materials (node trees)
                try:
                    if data_type == 'materials' and hasattr(datablock, 'node_tree') and datablock.node_tree:
                        node_refs = _extract_node_tree_references(datablock.node_tree)
                        references.extend(node_refs)
                except (AttributeError, RuntimeError, ReferenceError):
                    pass
                
                # Special handling for node groups
                try:
                    if data_type == 'node_groups':
                        node_refs = _extract_node_tree_references(datablock)
                        references.extend(node_refs)
                except (AttributeError, RuntimeError, ReferenceError):
                    pass
                
                # Special handling for scenes (compositor, rigidbody_world, collection, world, etc.)
                try:
                    if data_type == 'scenes':
                        # Compositor node tree reference
                        node_tree = compat.get_scene_compositor_node_tree(datablock)
                        if node_tree:
                            try:
                                if not compat.is_library_or_override(node_tree):
                                    references.append({
                                        'property': 'node_tree',
                                        'type': 'NodeTree',
                                        'name': node_tree.name
                                    })
                                    # Also extract references from within the node tree
                                    node_refs = _extract_node_tree_references(node_tree)
                                    references.extend(node_refs)
                            except (AttributeError, RuntimeError, ReferenceError):
                                pass
                        
                        # Scene's root collection
                        if hasattr(datablock, 'collection') and datablock.collection:
                            try:
                                if not compat.is_library_or_override(datablock.collection):
                                    references.append({
                                        'property': 'collection',
                                        'type': 'Collection',
                                        'name': datablock.collection.name
                                    })
                            except (AttributeError, RuntimeError, ReferenceError):
                                pass
                        
                        # Scene's world reference
                        if hasattr(datablock, 'world') and datablock.world:
                            try:
                                if not compat.is_library_or_override(datablock.world):
                                    references.append({
                                        'property': 'world',
                                        'type': 'World',
                                        'name': datablock.world.name
                                    })
                            except (AttributeError, RuntimeError, ReferenceError):
                                pass
                        
                        # RigidBodyWorld collection reference
                        if hasattr(datablock, 'rigidbody_world') and datablock.rigidbody_world:
                            try:
                                if hasattr(datablock.rigidbody_world, 'collection') and datablock.rigidbody_world.collection:
                                    if not compat.is_library_or_override(datablock.rigidbody_world.collection):
                                        references.append({
                                            'property': 'rigidbody_world.collection',
                                            'type': 'Collection',
                                            'name': datablock.rigidbody_world.collection.name
                                        })
                            except (AttributeError, RuntimeError, ReferenceError):
                                pass
                except (AttributeError, RuntimeError, ReferenceError):
                    pass
                
                # Special handling for collections (objects property)
                try:
                    if data_type == 'collections':
                        # Collections have an 'objects' property that contains objects
                        # This is a collection property, so it should be detected by _is_id_datablock_collection
                        # But let's also explicitly check to ensure it's captured
                        if hasattr(datablock, 'objects'):
                            # Create a snapshot to avoid iteration issues
                            objects = _safe_snapshot(datablock.objects)
                            
                            for obj in objects:
                                if obj is None:
                                    continue
                                try:
                                    # Even if the object is linked/override, keep the reference:
                                    # linked scene content can still reference local datablocks.
                                    references.append({
                                        'property': 'objects',
                                        'type': 'Object',
                                        'name': obj.name
                                    })
                                except (AttributeError, RuntimeError, ReferenceError):
                                    continue
                except (AttributeError, RuntimeError, ReferenceError):
                    pass
                
                # Special handling for objects (modifiers with node groups, material slots)
                try:
                    if data_type == 'objects':
                        # Objects can have modifiers that reference node groups (e.g., Geometry Nodes modifiers)
                        if hasattr(datablock, 'modifiers'):
                            # Create a snapshot to avoid iteration issues
                            modifiers = _safe_snapshot(datablock.modifiers)
                            
                            # Debug: Log modifier count for Turf objects
                            if item_name in ('Turf.001', 'Turf'):
                                mod_names = [m.name if m else 'None' for m in modifiers]
                                config.debug_print(f"[Atomic Debug] RNA Analysis: {item_name} modifiers count={len(modifiers)}, names={mod_names}")
                            
                            for modifier in modifiers:
                                if modifier is None:
                                    continue
                                try:
                                    if compat.is_geometry_nodes_modifier(modifier):
                                        ng = compat.get_geometry_nodes_modifier_node_group(modifier)
                                        if ng and not compat.is_library_or_override(ng):
                                            references.append({
                                                'property': 'modifiers.node_group',
                                                'type': 'NodeTree',
                                                'name': ng.name
                                            })
                                except (AttributeError, RuntimeError, ReferenceError):
                                    # Geometry nodes modifier access may fail
                                    pass
                                
                                # Modifiers with .texture (e.g. Displace) reference Texture datablocks
                                # IMPORTANT: capture references to linked textures too, so we can traverse
                                # the graph correctly (even though linked textures themselves aren't cleanable)
                                # Use separate try-except to ensure texture references are captured even if
                                # geometry nodes modifier access failed above
                                try:
                                    has_texture_attr = hasattr(modifier, 'texture')
                                    texture_value = modifier.texture if has_texture_attr else None
                                    
                                    # Debug: Log modifier texture access for Turf objects
                                    if item_name in ('Turf.001', 'Turf'):
                                        config.debug_print(f"[Atomic Debug] RNA Analysis: {item_name} modifier '{modifier.name}' has_texture={has_texture_attr}, texture={texture_value}")
                                    
                                    if has_texture_attr and texture_value:
                                        # Access texture.name in try-except in case texture is linked/inaccessible
                                        try:
                                            texture_name = modifier.texture.name
                                            # Get type identifier from texture's bl_rna to ensure correct mapping
                                            texture_type = 'Texture'
                                            if hasattr(modifier.texture, 'bl_rna'):
                                                try:
                                                    rna_id = modifier.texture.bl_rna.identifier
                                                    # Map common Blender RNA identifiers to our type names
                                                    if 'Texture' in rna_id:
                                                        texture_type = 'Texture'
                                                except (AttributeError, RuntimeError):
                                                    pass
                                            
                                            # Check if this reference already exists (from recursive extraction)
                                            # to avoid duplicates, but ensure we capture it explicitly
                                            ref_exists = any(
                                                ref.get('property') == 'modifiers.texture' and
                                                ref.get('name') == texture_name and
                                                ref.get('type', '').lower() in ('texture', 'texturedatablock', 'bpy.types.texture')
                                                for ref in references
                                            )
                                            
                                            # Debug: Log texture reference capture for Turf objects
                                            if item_name in ('Turf.001', 'Turf'):
                                                config.debug_print(f"[Atomic Debug] RNA Analysis: {item_name} modifier '{modifier.name}' texture_name={texture_name}, ref_exists={ref_exists}")
                                            
                                            if not ref_exists:
                                                references.append({
                                                    'property': 'modifiers.texture',
                                                    'type': texture_type,
                                                    'name': texture_name
                                                })
                                                # Debug: Confirm reference was added
                                                if item_name in ('Turf.001', 'Turf'):
                                                    config.debug_print(f"[Atomic Debug] RNA Analysis: {item_name} ADDED modifiers.texture -> {texture_name}")
                                        except (AttributeError, RuntimeError, ReferenceError) as e:
                                            # Texture.name access failed - texture may be linked/inaccessible
                                            if item_name in ('Turf.001', 'Turf'):
                                                config.debug_print(f"[Atomic Debug] RNA Analysis: {item_name} texture.name access failed: {e}")
                                            pass
                                except (AttributeError, RuntimeError, ReferenceError) as e:
                                    # Modifier.texture may be inaccessible (e.g., linked modifier/texture)
                                    if item_name in ('Turf.001', 'Turf'):
                                        config.debug_print(f"[Atomic Debug] RNA Analysis: {item_name} modifier texture access failed: {e}")
                                    pass
                        
                        # Objects have material slots that reference materials
                        if hasattr(datablock, 'material_slots'):
                            # Create a snapshot to avoid iteration issues
                            material_slots = _safe_snapshot(datablock.material_slots)
                            
                            for slot in material_slots:
                                if slot is None:
                                    continue
                                try:
                                    if hasattr(slot, 'material') and slot.material:
                                        if not compat.is_library_or_override(slot.material):
                                            references.append({
                                                'property': 'material_slots.material',
                                                'type': 'Material',
                                                'name': slot.material.name
                                            })
                                except (AttributeError, RuntimeError, ReferenceError):
                                    # Slot or material may be invalid
                                    continue
                        
                        # Objects have particle_systems that reference particle settings
                        # IMPORTANT: capture references to linked particle settings too, so we can traverse
                        # the graph correctly (even though linked particle settings themselves aren't cleanable)
                        if hasattr(datablock, 'particle_systems'):
                            for ps in _safe_snapshot(datablock.particle_systems):
                                if ps is None:
                                    continue
                                try:
                                    if hasattr(ps, 'settings') and ps.settings:
                                        references.append({
                                            'property': 'particle_systems.settings',
                                            'type': 'ParticleSettings',
                                            'name': ps.settings.name
                                        })
                                except (AttributeError, RuntimeError, ReferenceError):
                                    continue
                except (AttributeError, RuntimeError, ReferenceError):
                    pass
                
                # Special handling for worlds (node tree → images/textures)
                try:
                    if data_type == 'worlds' and hasattr(datablock, 'node_tree') and datablock.node_tree:
                        node_refs = _extract_node_tree_references(datablock.node_tree)
                        references.extend(node_refs)
                except (AttributeError, RuntimeError, ReferenceError):
                    pass
                
                # Special handling for particles (texture_slots → Texture; used by objects in scene)
                try:
                    if data_type == 'particles' and hasattr(datablock, 'texture_slots'):
                        for slot in _safe_snapshot(datablock.texture_slots):
                            if slot is None:
                                continue
                            try:
                                if hasattr(slot, 'texture') and slot.texture and not compat.is_library_or_override(slot.texture):
                                    references.append({
                                        'property': 'texture_slots.texture',
                                        'type': 'Texture',
                                        'name': slot.texture.name
                                    })
                            except (AttributeError, RuntimeError, ReferenceError):
                                continue
                except (AttributeError, RuntimeError, ReferenceError):
                    pass
                
                # Special handling for textures (legacy .image → Image; e.g. rippleblur.png via Texture used by Turf)
                try:
                    if data_type == 'textures' and hasattr(datablock, 'image') and datablock.image and not compat.is_library_or_override(datablock.image):
                        references.append({
                            'property': 'image',
                            'type': 'Image',
                            'name': datablock.image.name
                        })
                except (AttributeError, RuntimeError, ReferenceError):
                    pass
                
                # Debug: Log references for Turf objects to trace the modifiers.texture issue
                if item_name in ('Turf.001', 'Turf') and data_type == 'objects':
                    config.debug_print(f"[Atomic Debug] RNA Analysis: {item_name} references BEFORE storing: {references}")
                    texture_refs = [r for r in references if 'texture' in r.get('property', '').lower()]
                    config.debug_print(f"[Atomic Debug] RNA Analysis: {item_name} texture-related refs: {texture_refs}")
                
                # Store references
                rna_data[data_type][item_name] = {
                    'references': references,
                    'referenced_by': []  # Will be populated in reverse pass
                }
                
                # Build reverse reference map
                for ref in references:
                    ref_type = ref.get('type', '').lower()
                    ref_name = ref.get('name', '')
                    
                    # Normalize Blender RNA identifiers to our type names
                    # Handle patterns like 'Texture', 'TextureDatablock', 'bpy.types.Texture', etc.
                    ref_type_normalized = ref_type
                    if 'texture' in ref_type and 'datablock' not in ref_type:
                        ref_type_normalized = 'texture'
                    elif 'material' in ref_type and 'datablock' not in ref_type:
                        ref_type_normalized = 'material'
                    elif 'image' in ref_type and 'datablock' not in ref_type:
                        ref_type_normalized = 'image'
                    elif 'object' in ref_type and 'datablock' not in ref_type:
                        ref_type_normalized = 'object'
                    elif 'collection' in ref_type and 'datablock' not in ref_type:
                        ref_type_normalized = 'collection'
                    elif 'nodetree' in ref_type or 'nodegroup' in ref_type or 'node_tree' in ref_type:
                        ref_type_normalized = 'nodetree'
                    elif 'light' in ref_type and 'datablock' not in ref_type:
                        ref_type_normalized = 'light'
                    elif 'armature' in ref_type and 'datablock' not in ref_type:
                        ref_type_normalized = 'armature'
                    elif 'world' in ref_type and 'datablock' not in ref_type:
                        ref_type_normalized = 'world'
                    elif 'particlesettings' in ref_type or ('particle' in ref_type and 'settings' in ref_type):
                        ref_type_normalized = 'particlesettings'
                    elif 'mesh' in ref_type and 'datablock' not in ref_type:
                        ref_type_normalized = 'mesh'
                    elif 'scene' in ref_type and 'datablock' not in ref_type:
                        ref_type_normalized = 'scene'
                    
                    # Map type names to our data_type keys
                    type_mapping = {
                        'image': 'images',
                        'material': 'materials',
                        'object': 'objects',
                        'collection': 'collections',
                        'nodetree': 'node_groups',
                        'texture': 'textures',
                        'light': 'lights',
                        'armature': 'armatures',
                        'world': 'worlds',
                        'particlesettings': 'particles',
                        'mesh': 'meshes',
                        'scene': 'scenes',
                    }
                    
                    mapped_type = type_mapping.get(ref_type_normalized, ref_type_normalized)
                    if mapped_type in _DATA_BLOCK_TYPE_NAMES:
                        if mapped_type not in reference_map:
                            reference_map[mapped_type] = {}
                        if ref_name not in reference_map[mapped_type]:
                            reference_map[mapped_type][ref_name] = []
                        
                        reference_map[mapped_type][ref_name].append({
                            'type': data_type,
                            'name': item_name,
                            'property': ref.get('property', '')
                        })
        except Exception as e:
            # If processing this data_type fails (e.g., collection became invalid),
            # log and continue with next data_type
            config.debug_print(f"[Atomic Warning] RNA Analysis: Failed to process {data_type}: {e}")
            continue
    
    # Populate reverse references
    for data_type, items in reference_map.items():
        for item_name, sources in items.items():
            if data_type in rna_data and item_name in rna_data[data_type]:
                rna_data[data_type][item_name]['referenced_by'] = sources
    
    config.debug_print(f"[Atomic Debug] RNA Analysis: Reference dump complete. Processed {sum(len(items) for items in rna_data.values())} data-blocks.")
    
    # Debug: Show sample of extracted references
    if config.enable_debug_prints:
        sample_count = 0
        for data_type, items in rna_data.items():
            for item_name, item_data in items.items():
                refs = item_data.get('references', [])
                if refs and sample_count < 5:
                    config.debug_print(f"[Atomic Debug] RNA Sample: {data_type}.{item_name} references: {[r.get('name') for r in refs[:3]]}")
                    sample_count += 1
                    if sample_count >= 5:
                        break
            if sample_count >= 5:
                break
    
    # Save to file if path provided
    if output_path:
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(rna_data, f, indent=2)
            config.debug_print(f"[Atomic Debug] RNA Analysis: Saved to {output_path}")
        except Exception as e:
            config.debug_print(f"[Atomic Error] RNA Analysis: Failed to save dump: {e}")
    
    return rna_data


def build_dependency_graph(rna_data):
    """
    Build a bidirectional dependency graph from RNA dump.
    
    Args:
        rna_data: Dictionary from dump_rna_references()
    
    Returns:
        Dictionary with structure: {data_type: {item_name: {'references': set(...), 'referenced_by': set(...)}}}}
    """
    config.debug_print("[Atomic Debug] RNA Analysis: Building dependency graph...")
    
    graph = {}
    
    # Initialize graph structure
    for data_type in _DATA_BLOCK_TYPE_NAMES:
        graph[data_type] = {}
    
    # Build forward references (what this references)
    for data_type, items in rna_data.items():
        for item_name, item_data in items.items():
            if data_type not in graph:
                graph[data_type] = {}
            
            if item_name not in graph[data_type]:
                graph[data_type][item_name] = {
                    'references': set(),
                    'referenced_by': set()
                }
            
            # Add forward references
            for ref in item_data.get('references', []):
                ref_type = ref.get('type', '').lower()
                ref_name = ref.get('name', '')
                
                # Normalize Blender RNA identifiers to our type names (same as in dump_rna_references)
                ref_type_normalized = ref_type
                if 'texture' in ref_type and 'datablock' not in ref_type:
                    ref_type_normalized = 'texture'
                elif 'material' in ref_type and 'datablock' not in ref_type:
                    ref_type_normalized = 'material'
                elif 'image' in ref_type and 'datablock' not in ref_type:
                    ref_type_normalized = 'image'
                elif 'object' in ref_type and 'datablock' not in ref_type:
                    ref_type_normalized = 'object'
                elif 'collection' in ref_type and 'datablock' not in ref_type:
                    ref_type_normalized = 'collection'
                elif 'nodetree' in ref_type or 'nodegroup' in ref_type or 'node_tree' in ref_type:
                    ref_type_normalized = 'nodetree'
                elif 'light' in ref_type and 'datablock' not in ref_type:
                    ref_type_normalized = 'light'
                elif 'armature' in ref_type and 'datablock' not in ref_type:
                    ref_type_normalized = 'armature'
                elif 'world' in ref_type and 'datablock' not in ref_type:
                    ref_type_normalized = 'world'
                elif 'particlesettings' in ref_type or ('particle' in ref_type and 'settings' in ref_type):
                    ref_type_normalized = 'particlesettings'
                elif 'mesh' in ref_type and 'datablock' not in ref_type:
                    ref_type_normalized = 'mesh'
                elif 'scene' in ref_type and 'datablock' not in ref_type:
                    ref_type_normalized = 'scene'
                
                # Map type names
                type_mapping = {
                    'image': 'images',
                    'material': 'materials',
                    'object': 'objects',
                    'collection': 'collections',
                    'nodetree': 'node_groups',
                    'texture': 'textures',
                    'light': 'lights',
                    'armature': 'armatures',
                    'world': 'worlds',
                    'particlesettings': 'particles',
                    'mesh': 'meshes',
                    'scene': 'scenes',
                }
                
                mapped_type = type_mapping.get(ref_type_normalized, ref_type_normalized)
                if mapped_type in _DATA_BLOCK_TYPE_NAMES:
                    graph[data_type][item_name]['references'].add((mapped_type, ref_name))
    
    # Build reverse references (what references this)
    for data_type, items in rna_data.items():
        for item_name, item_data in items.items():
            for source in item_data.get('referenced_by', []):
                source_type = source.get('type', '')
                source_name = source.get('name', '')
                
                if source_type in _DATA_BLOCK_TYPE_NAMES:
                    if source_type not in graph:
                        graph[source_type] = {}
                    if source_name not in graph[source_type]:
                        graph[source_type][source_name] = {
                            'references': set(),
                            'referenced_by': set()
                        }

                    # Record reverse edge (target <- source)
                    graph[source_type][source_name]['referenced_by'].add((data_type, item_name))

                    # IMPORTANT: also ensure the corresponding forward edge exists.
                    # Some Blender datablocks show up only in reverse discovery (e.g. certain
                    # linked/override modifier texture users) which would otherwise break
                    # reachability traversal from roots.
                    graph[source_type][source_name]['references'].add((data_type, item_name))
    
    config.debug_print("[Atomic Debug] RNA Analysis: Dependency graph built.")
    return graph


def analyze_unused_from_graph(graph, category, include_fake_users=None):
    """
    Determine unused items using the dependency graph.
    
    Args:
        graph: Dependency graph from build_dependency_graph()
        category: Category to analyze ('images', 'materials', etc.)
        include_fake_users: Whether to treat fake users as used (defaults to config.include_fake_users)
    
    Returns:
        List of unused item names for the specified category
    """
    if include_fake_users is None:
        include_fake_users = config.include_fake_users
    
    config.debug_print(f"[Atomic Debug] RNA Analysis: Analyzing unused {category}...")
    
    if category not in _DATA_BLOCK_TYPE_NAMES:
        config.debug_print(f"[Atomic Warning] RNA Analysis: Unknown category '{category}'")
        return []
    
    # Mark all items as unused initially
    used = set()
    
    # Find root items (those that are directly used in scenes/view layers)
    roots = []
    
    # Debug: Check if graph has any data
    if config.enable_debug_prints:
        total_nodes = sum(len(items) for items in graph.values())
        config.debug_print(f"[Atomic Debug] RNA Analysis: Graph has {total_nodes} total nodes")
        # Check if collections have object references
        collection_count = 0
        for coll_name, coll_data in graph.get('collections', {}).items():
            refs = coll_data.get('references', set())
            obj_refs = [r for r in refs if r[0] == 'objects']
            if obj_refs and collection_count < 3:
                config.debug_print(f"[Atomic Debug] RNA Analysis: Collection '{coll_name}' references {len(obj_refs)} objects (sample: {[r[1] for r in list(obj_refs)[:3]]})")
                collection_count += 1
    
    # Helper function to get all collections in a scene hierarchy
    def get_all_scene_collections(root_collection):
        """Recursively get all collections in the scene hierarchy."""
        collections = []
        if root_collection and not compat.is_library_or_override(root_collection):
            try:
                collections.append(root_collection)
                # Add all descendant collections
                try:
                    children = list(root_collection.children_recursive)
                except (RuntimeError, ReferenceError):
                    children = []
                
                for child in children:
                    if child is None:
                        continue
                    try:
                        if not compat.is_library_or_override(child):
                            collections.append(child)
                    except (AttributeError, RuntimeError, ReferenceError):
                        # Child may be invalid
                        continue
            except (AttributeError, RuntimeError, ReferenceError):
                # Root collection may be invalid
                pass
        return collections
    
    # Objects in scenes/view layers (directly in scene.objects)
    for scene in bpy.data.scenes:
        if compat.is_library_or_override(scene):
            continue
        
        # Add scene itself as a root so its references (compositor node tree, world, etc.) are traversed
        try:
            roots.append(('scenes', scene.name))
        except (AttributeError, RuntimeError, ReferenceError):
            # Scene may be invalid
            pass
        
        # Create a snapshot to avoid iteration issues
        scene_objects = _safe_snapshot(scene.objects)
        
        for obj in scene_objects:
            if obj is None:
                continue
            try:
                # IMPORTANT: include linked/override objects as roots so their references
                # (e.g. local materials assigned to linked objects) are treated as used.
                roots.append(('objects', obj.name))

                # Also mark the object's data-block as used (for lights, meshes, armatures, etc.)
                if hasattr(obj, 'data') and obj.data and hasattr(obj.data, 'name'):
                    try:
                        data_type_map = {
                            'LIGHT': 'lights',
                            'MESH': 'meshes',
                            'ARMATURE': 'armatures',
                            'CURVE': 'curves',
                            'SURFACE': 'curves',  # Surface objects also use curve data
                            'FONT': 'curves',  # Font objects also use curve data
                            'META': 'metaballs',
                            'LATTICE': 'lattices',
                            'VOLUME': 'volumes',
                        }
                        obj_type = obj.type
                        if obj_type in data_type_map:
                            data_type = data_type_map[obj_type]
                            if not compat.is_library_or_override(obj.data):
                                roots.append((data_type, obj.data.name))
                    except (AttributeError, RuntimeError, ReferenceError):
                        pass

                # Also mark node groups used by object modifiers (e.g., Geometry Nodes modifiers)
                if hasattr(obj, 'modifiers'):
                    try:
                        modifiers = list(obj.modifiers)
                    except (RuntimeError, ReferenceError):
                        modifiers = []

                    for modifier in modifiers:
                        if modifier is None:
                            continue
                        try:
                            if compat.is_geometry_nodes_modifier(modifier):
                                ng = compat.get_geometry_nodes_modifier_node_group(modifier)
                                if ng and not compat.is_library_or_override(ng):
                                    roots.append(('node_groups', ng.name))
                        except (AttributeError, RuntimeError, ReferenceError):
                            continue
            except (AttributeError, RuntimeError, ReferenceError):
                # Object may have been deleted or is invalid
                continue
        
        # World assigned to scene
        if scene.world and not compat.is_library_or_override(scene.world):
            roots.append(('worlds', scene.world.name))
        
        # Collections in scene (including root collection and all descendants)
        scene_collections = get_all_scene_collections(scene.collection)
        for collection in scene_collections:
            roots.append(('collections', collection.name))
        
        # RigidBodyWorld collection (physics world)
        if hasattr(scene, 'rigidbody_world') and scene.rigidbody_world:
            if hasattr(scene.rigidbody_world, 'collection') and scene.rigidbody_world.collection:
                if not compat.is_library_or_override(scene.rigidbody_world.collection):
                    roots.append(('collections', scene.rigidbody_world.collection.name))
        
        # Objects in collections that are in scenes (via collection.objects)
        # This ensures objects in collections are marked as used
        # Note: The graph traversal should also handle this, but we add them explicitly as a safety measure
        for collection in scene_collections:
            # Create a snapshot to avoid iteration issues
            collection_objects = _safe_snapshot(collection.objects)
            
            for obj in collection_objects:
                if obj is None:
                    continue
                try:
                    # IMPORTANT: include linked/override objects as roots so their references
                    # (e.g. textures in Displace modifiers, materials, etc.) are traversed.
                    roots.append(('objects', obj.name))
                    
                    # Also mark the object's data-block as used (for lights, meshes, armatures, etc.)
                    if hasattr(obj, 'data') and obj.data and hasattr(obj.data, 'name'):
                        try:
                            data_type_map = {
                                'LIGHT': 'lights',
                                'MESH': 'meshes',
                                'ARMATURE': 'armatures',
                                'CURVE': 'curves',
                                'SURFACE': 'curves',  # Surface objects also use curve data
                                'FONT': 'curves',  # Font objects also use curve data
                                'META': 'metaballs',
                                'LATTICE': 'lattices',
                                'VOLUME': 'volumes',
                            }
                            obj_type = obj.type
                            if obj_type in data_type_map:
                                data_type = data_type_map[obj_type]
                                if not compat.is_library_or_override(obj.data):
                                    roots.append((data_type, obj.data.name))
                        except (AttributeError, RuntimeError, ReferenceError):
                            pass
                    
                    # Also mark node groups used by object modifiers (e.g., Geometry Nodes modifiers)
                    if hasattr(obj, 'modifiers'):
                        try:
                            modifiers = list(obj.modifiers)
                        except (RuntimeError, ReferenceError):
                            modifiers = []
                        
                        for modifier in modifiers:
                            if modifier is None:
                                continue
                            try:
                                if compat.is_geometry_nodes_modifier(modifier):
                                    ng = compat.get_geometry_nodes_modifier_node_group(modifier)
                                    if ng and not compat.is_library_or_override(ng):
                                        roots.append(('node_groups', ng.name))
                            except (AttributeError, RuntimeError, ReferenceError):
                                continue
                except (AttributeError, RuntimeError, ReferenceError):
                    # Object may have been deleted or is invalid
                    continue
    
    # Fake users
    if not include_fake_users:
        try:
            data_block_types = _get_data_block_types()
            for data_type, data_collection in data_block_types.items():
                # Create a snapshot to avoid iteration issues
                datablocks = _safe_snapshot(data_collection)
                
                for datablock in datablocks:
                    if datablock is None:
                        continue
                    try:
                        if compat.is_library_or_override(datablock):
                            continue
                        if hasattr(datablock, 'use_fake_user') and datablock.use_fake_user:
                            roots.append((data_type, datablock.name))
                    except (AttributeError, RuntimeError, ReferenceError):
                        # Datablock may be invalid
                        continue
        except Exception:
            # If accessing data-block types fails, skip fake user check
            pass
    
    # Traverse graph from roots
    visited = set()
    queue = list(roots)
    
    while queue:
        data_type, item_name = queue.pop(0)
        
        if (data_type, item_name) in visited:
            continue
        
        visited.add((data_type, item_name))
        used.add((data_type, item_name))
        
        # Follow forward references (what this item references)
        if data_type in graph and item_name in graph[data_type]:
            for ref_type, ref_name in graph[data_type][item_name]['references']:
                if (ref_type, ref_name) not in visited:
                    queue.append((ref_type, ref_name))
    
    # Find unused items in the requested category
    unused = []
    
    # Special do_not_flag lists
    do_not_flag = {
        'images': ["Render Result", "Viewer Node", "D-NOISE Export"]
    }
    
    category_do_not_flag = do_not_flag.get(category, [])
    
    # Iterate over all data-blocks in the category
    try:
        # Get fresh reference to avoid stale data after opening new blend file
        data_block_types = _get_data_block_types()
        if category not in data_block_types:
            category_datablocks = []
        else:
            # Create a snapshot to avoid iteration issues
            category_datablocks = _safe_snapshot(data_block_types[category])
    except Exception:
        # If accessing data-block types fails, return empty list
        category_datablocks = []
    
    for datablock in category_datablocks:
        if datablock is None:
            continue
        try:
            if compat.is_library_or_override(datablock):
                continue
            
            item_name = datablock.name
            if (category, item_name) not in used:
                if item_name not in category_do_not_flag:
                    unused.append(item_name)
        except (AttributeError, RuntimeError, ReferenceError):
            # Datablock may be invalid
            continue
    
    config.debug_print(f"[Atomic Debug] RNA Analysis: Found {len(unused)} unused {category}")
    return unused

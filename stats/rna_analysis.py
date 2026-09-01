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
from collections import defaultdict
from .. import config
from ..utils import compat


# Data-block types we care about for dependency analysis
# Note: We rebuild this dynamically in get_data_block_types() to avoid stale references
# after opening a new blend file
_DATA_BLOCK_TYPE_NAMES = [
    'images', 'materials', 'objects', 'collections', 'node_groups',
    'textures', 'lights', 'armatures', 'actions', 'worlds', 'particles',
    'meshes', 'scenes'
]


def _get_data_block_types():
    """
    Safely get a dictionary of data-block types with fresh references.
    This must be called each time to avoid stale references after opening a new blend file.
    """
    try:
        raw = {
            'images': bpy.data.images,
            'materials': bpy.data.materials,
            'objects': bpy.data.objects,
            'collections': bpy.data.collections,
            'node_groups': bpy.data.node_groups,
            'textures': bpy.data.textures,
            'lights': bpy.data.lights,
            'armatures': bpy.data.armatures,
            'actions': getattr(bpy.data, 'actions', None),
            'worlds': bpy.data.worlds,
            'particles': bpy.data.particles,
            'meshes': bpy.data.meshes,
            'scenes': bpy.data.scenes,
        }
        return {k: v for k, v in raw.items() if v is not None}
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


def _id_ref_from_value(value, property_name, skip_library_types=None):
    """
    Build a dependency-graph reference dict from an ID pointer value.

    Args:
        value: Candidate ID data-block (Object, Collection, Material, etc.)
        property_name: Graph edge label for this reference
        skip_library_types: Optional set of mapped type names to skip when linked/override
            (e.g. {'Material', 'Image'}). Object/Collection refs are kept even when linked
            so local deps reachable through them stay marked used.
    """
    if value is None or not hasattr(value, 'name') or not hasattr(value, 'bl_rna'):
        return None

    if skip_library_types is None:
        skip_library_types = {'Material', 'Image'}

    try:
        type_id = value.bl_rna.identifier
    except (AttributeError, RuntimeError, ReferenceError):
        return None

    # Normalize Blender RNA type identifiers to graph type labels
    type_map = {
        'Object': 'Object',
        'Collection': 'Collection',
        'Material': 'Material',
        'Image': 'Image',
        'Texture': 'Texture',
        'NodeTree': 'NodeTree',
        'GeometryNodeTree': 'NodeTree',
        'ShaderNodeTree': 'NodeTree',
        'CompositorNodeTree': 'NodeTree',
        'Armature': 'Armature',
        'Action': 'Action',
        'Mesh': 'Mesh',
        'World': 'World',
        'Light': 'Light',
        'ParticleSettings': 'ParticleSettings',
        'Scene': 'Scene',
    }
    mapped = type_map.get(type_id)
    if mapped is None:
        if 'NodeTree' in type_id or 'NodeGroup' in type_id:
            mapped = 'NodeTree'
        else:
            return None

    try:
        if mapped in skip_library_types and compat.is_library_or_override(value):
            return None
        return {
            'property': property_name,
            'type': mapped,
            'name': value.name,
        }
    except (AttributeError, RuntimeError, ReferenceError):
        return None


def _extract_geometry_nodes_modifier_input_refs(modifier):
    """
    Extract Object/Collection/Material/Image refs from Geometry Nodes modifier inputs.

    Blender 5.x stores overrides on modifier.properties.inputs.<Socket_N>.value.
    Older builds may expose socket IDProperties on the modifier itself.
    """
    references = []

    # Blender 5.x GeometryNodesModifierInterface inputs
    try:
        props = getattr(modifier, 'properties', None)
        inputs = getattr(props, 'inputs', None) if props is not None else None
        if inputs is not None and hasattr(inputs, 'bl_rna'):
            for prop in inputs.bl_rna.properties:
                if prop.identifier in ('rna_type', 'name') or prop.type != 'POINTER':
                    continue
                try:
                    sock = getattr(inputs, prop.identifier, None)
                    if sock is None:
                        continue
                    val = getattr(sock, 'value', None)
                    ref = _id_ref_from_value(
                        val,
                        f'modifiers.properties.inputs.{prop.identifier}',
                    )
                    if ref:
                        references.append(ref)
                except (AttributeError, RuntimeError, ReferenceError, TypeError, KeyError):
                    continue
    except (AttributeError, RuntimeError, ReferenceError, TypeError):
        pass

    # Legacy: modifier IDProperties keyed by socket identifier (Input_*/Socket_*)
    try:
        keys = modifier.keys()
    except (AttributeError, TypeError, RuntimeError):
        keys = []

    for key in keys:
        if not isinstance(key, str):
            continue
        if not (key.startswith('Input_') or key.startswith('Socket_')):
            continue
        try:
            val = modifier.get(key)
            ref = _id_ref_from_value(val, f'modifiers["{key}"]')
            if ref:
                references.append(ref)
        except (AttributeError, RuntimeError, ReferenceError, TypeError, KeyError):
            continue

    return references


def _extract_node_tree_interface_refs(node_tree):
    """Extract ID defaults from a node group interface (Object/Collection/etc.)."""
    references = []
    if not node_tree or not hasattr(node_tree, 'interface'):
        return references

    try:
        items = _safe_snapshot(node_tree.interface.items_tree)
    except (AttributeError, RuntimeError, ReferenceError, TypeError):
        return references

    for item in items:
        if item is None:
            continue
        try:
            # Only input defaults feed runtime usage
            if getattr(item, 'in_out', None) == 'OUTPUT':
                continue
            if not hasattr(item, 'default_value'):
                continue
            ref = _id_ref_from_value(item.default_value, 'interface.default_value')
            if ref:
                references.append(ref)
        except (AttributeError, RuntimeError, ReferenceError, TypeError, KeyError):
            continue

    return references


def _extract_node_tree_references(node_tree):
    """Extract references from a node tree (materials, compositor, geometry nodes, etc.)."""
    references = []
    
    if not node_tree:
        return references

    # Group interface defaults (Collection/Object sockets, etc.)
    references.extend(_extract_node_tree_interface_refs(node_tree))
    
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
                
                # ID input sockets: Material, Object, Collection, Image
                # (Object Info, Collection Info, Set Material, Menu Switch, etc.)
                if hasattr(node, 'inputs'):
                    try:
                        for input_socket in node.inputs:
                            try:
                                socket_type = str(getattr(input_socket, 'type', '')).upper()
                                if socket_type not in (
                                    'MATERIAL', 'OBJECT', 'COLLECTION', 'IMAGE', 'TEXTURE'
                                ):
                                    continue
                                if not hasattr(input_socket, 'default_value'):
                                    continue
                                ref = _id_ref_from_value(
                                    input_socket.default_value,
                                    f'inputs.{socket_type.lower()}',
                                )
                                if ref:
                                    references.append(ref)
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


def _extract_animation_data_action_refs(anim_data, prop_prefix='animation_data'):
    """Collect Action references from AnimationData (.action and NLA strips)."""
    references = []
    if anim_data is None:
        return references
    try:
        action = getattr(anim_data, 'action', None)
        if action is not None and hasattr(action, 'name'):
            if not compat.is_library_or_override(action):
                references.append({
                    'property': f'{prop_prefix}.action',
                    'type': 'Action',
                    'name': action.name,
                })
        for track in list(getattr(anim_data, 'nla_tracks', []) or []):
            if track is None:
                continue
            for strip in list(getattr(track, 'strips', []) or []):
                if strip is None:
                    continue
                strip_action = getattr(strip, 'action', None)
                if strip_action is not None and hasattr(strip_action, 'name'):
                    if not compat.is_library_or_override(strip_action):
                        references.append({
                            'property': f'{prop_prefix}.nla_tracks.strips.action',
                            'type': 'Action',
                            'name': strip_action.name,
                        })
    except (AttributeError, RuntimeError, ReferenceError, TypeError):
        pass
    return references


def dump_rna_references(output_path=None, only_type=None, rna_data=None, reference_map=None):
    """
    Dump all data-block references found via RNA introspection to JSON.
    
    Args:
        output_path: Optional path to save JSON file. If None, returns dict.
        only_type: When set, process a single data-block type (incremental builds).
        rna_data: Optional accumulator dict for incremental builds.
        reference_map: Optional reverse-reference accumulator for incremental builds.
    
    Returns:
        Dictionary with structure: {data_type: {item_name: {references: [...], referenced_by: []}}}
    """
    incremental = rna_data is not None
    if not incremental:
        config.debug_print("[Atomic Debug] RNA Analysis: Starting reference dump...")
        rna_data = {}
        reference_map = {}
    
    # Get fresh references to data-block types (critical after opening a new blend file)
    try:
        data_block_types = _get_data_block_types()
    except Exception:
        config.debug_print("[Atomic Debug] RNA Analysis: Failed to get data-block types, returning empty data")
        if output_path:
            with open(output_path, 'w') as f:
                json.dump({}, f, indent=2)
        return rna_data if incremental else {}
    
    # Initialize structure
    if not incremental:
        for data_type in data_block_types.keys():
            rna_data[data_type] = {}
    
    if only_type is not None:
        if only_type not in data_block_types:
            return rna_data
        types_to_process = [(only_type, data_block_types[only_type])]
    else:
        types_to_process = list(data_block_types.items())
    
    # Extract references from all data-blocks
    # Wrap in try-except to handle crashes when collections become invalid
    for data_type, data_collection in types_to_process:
        if data_type not in rna_data:
            rna_data[data_type] = {}
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
                if is_linked_or_override and data_type not in {'objects', 'node_groups'}:
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
                
                # Special handling for node groups (including linked/override trees so
                # nested *local* groups still get referenced_by edges).
                try:
                    if data_type == 'node_groups':
                        node_refs = _extract_node_tree_references(datablock)
                        references.extend(node_refs)
                except (AttributeError, RuntimeError, ReferenceError):
                    pass
                
                # Linked/override node groups are reference-only: keep outbound edges for
                # nested local groups, then skip other type-specific handlers.
                if is_linked_or_override and data_type == 'node_groups':
                    rna_data[data_type][item_name] = {
                        'references': references,
                        'referenced_by': [],
                    }
                    for ref in references:
                        ref_type = (ref.get('type', '') or '').lower()
                        ref_name = ref.get('name', '')
                        if not ref_name:
                            continue
                        if 'nodetree' in ref_type or 'nodegroup' in ref_type or 'node_tree' in ref_type:
                            mapped_type = 'node_groups'
                        elif 'material' in ref_type:
                            mapped_type = 'materials'
                        elif 'image' in ref_type:
                            mapped_type = 'images'
                        elif 'object' in ref_type:
                            mapped_type = 'objects'
                        elif 'collection' in ref_type:
                            mapped_type = 'collections'
                        else:
                            continue
                        if mapped_type not in reference_map:
                            reference_map[mapped_type] = {}
                        if ref_name not in reference_map[mapped_type]:
                            reference_map[mapped_type][ref_name] = []
                        reference_map[mapped_type][ref_name].append({
                            'type': data_type,
                            'name': item_name,
                            'property': ref.get('property', ''),
                        })
                    continue

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

                        # Scene animation (action + NLA)
                        try:
                            references.extend(
                                _extract_animation_data_action_refs(
                                    getattr(datablock, 'animation_data', None)
                                )
                            )
                        except (AttributeError, RuntimeError, ReferenceError):
                            pass
                except (AttributeError, RuntimeError, ReferenceError):
                    pass
                
                # Special handling for collections (objects + child collections)
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

                        # Child collections (needed so collection-instance sources reach nested GEO_* trees)
                        if hasattr(datablock, 'children'):
                            for child in _safe_snapshot(datablock.children):
                                if child is None:
                                    continue
                                try:
                                    references.append({
                                        'property': 'children',
                                        'type': 'Collection',
                                        'name': child.name
                                    })
                                except (AttributeError, RuntimeError, ReferenceError):
                                    continue
                except (AttributeError, RuntimeError, ReferenceError):
                    pass
                
                # Special handling for objects (modifiers with node groups, material slots)
                try:
                    if data_type == 'objects':
                        # Collection instances: Empty/object → instanced Collection
                        # (source collections are often outside the scene hierarchy)
                        try:
                            if getattr(datablock, 'instance_type', None) == 'COLLECTION':
                                inst_col = getattr(datablock, 'instance_collection', None)
                                ref = _id_ref_from_value(
                                    inst_col,
                                    'instance_collection',
                                    skip_library_types=set(),
                                )
                                if ref:
                                    references.append(ref)
                        except (AttributeError, RuntimeError, ReferenceError):
                            pass

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
                                        # Keep object→ng edges even for linked/override groups so
                                        # nested local groups remain reachable via parent indices.
                                        if ng and hasattr(ng, 'name'):
                                            references.append({
                                                'property': 'modifiers.node_group',
                                                'type': 'NodeTree',
                                                'name': ng.name
                                            })
                                        # Object/Collection/Material inputs on the modifier interface
                                        references.extend(
                                            _extract_geometry_nodes_modifier_input_refs(modifier)
                                        )
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

                        # Pose-bone custom shapes (rig widgets often live outside the scene)
                        try:
                            pose = getattr(datablock, 'pose', None)
                            if pose is not None and hasattr(pose, 'bones'):
                                for pose_bone in _safe_snapshot(pose.bones):
                                    if pose_bone is None:
                                        continue
                                    try:
                                        shape = getattr(pose_bone, 'custom_shape', None)
                                        ref = _id_ref_from_value(
                                            shape,
                                            'pose.bones.custom_shape',
                                            skip_library_types=set(),
                                        )
                                        if ref:
                                            references.append(ref)
                                    except (AttributeError, RuntimeError, ReferenceError):
                                        continue
                        except (AttributeError, RuntimeError, ReferenceError):
                            pass

                        # Object animation (active action + NLA strips)
                        try:
                            references.extend(
                                _extract_animation_data_action_refs(
                                    getattr(datablock, 'animation_data', None)
                                )
                            )
                        except (AttributeError, RuntimeError, ReferenceError):
                            pass

                        # Shape-key animation actions
                        try:
                            data = getattr(datablock, 'data', None)
                            shape_keys = getattr(data, 'shape_keys', None) if data else None
                            if shape_keys is not None:
                                references.extend(
                                    _extract_animation_data_action_refs(
                                        getattr(shape_keys, 'animation_data', None),
                                        'shape_keys.animation_data',
                                    )
                                )
                        except (AttributeError, RuntimeError, ReferenceError):
                            pass
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
                    elif ref_type == 'action' or (
                        'action' in ref_type and 'datablock' not in ref_type
                        and 'faction' not in ref_type
                    ):
                        ref_type_normalized = 'action'
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
                        'action': 'actions',
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
    
    if only_type is None and not incremental:
        finalize_rna_reference_dump(rna_data, reference_map)
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


def finalize_rna_reference_dump(rna_data, reference_map):
    """Populate referenced_by lists after an incremental or full RNA dump."""
    for data_type, items in reference_map.items():
        for item_name, sources in items.items():
            if data_type in rna_data and item_name in rna_data[data_type]:
                rna_data[data_type][item_name]['referenced_by'] = sources


def begin_rna_graph_build():
    """Start an incremental RNA reference dump + dependency graph build."""
    try:
        type_names = list(_get_data_block_types().keys())
    except Exception:
        type_names = list(_DATA_BLOCK_TYPE_NAMES)
    return {
        'phase': 'dump',
        'type_names': type_names,
        'type_index': 0,
        'dump_progress': 0.0,
        'rna_data': {},
        'reference_map': {},
    }


def reference_graph_build_fraction(state):
    """
    Return 0-1 progress through incremental reference graph build.

    Dump dominates runtime; finalize and graph linking get fixed late-stage slots.
    """
    phase = state.get('phase', 'dump')
    if phase == 'dump':
        return state.get('dump_progress', 0.0) * 0.75
    if phase == 'finalize':
        return 0.78
    if phase == 'graph':
        return 0.88
    return 1.0


def step_rna_graph_build(state):
    """
    Advance incremental RNA graph build by one step.

    Returns:
        (done, graph_or_none, status_label)
        status_label is the current step name for UI (e.g. data type or 'graph').
    """
    phase = state.get('phase', 'dump')

    if phase == 'dump':
        type_names = state['type_names']
        type_index = state['type_index']
        if type_index >= len(type_names):
            state['phase'] = 'finalize'
            return step_rna_graph_build(state)

        data_type = type_names[type_index]
        dump_rna_references(
            only_type=data_type,
            rna_data=state['rna_data'],
            reference_map=state['reference_map'],
        )
        state['type_index'] = type_index + 1
        dump_progress = state['type_index'] / max(len(type_names), 1)
        state['dump_progress'] = dump_progress
        if config.enable_debug_prints:
            config.debug_print(
                f"[Atomic Debug] reference graph dump: {state['type_index']}/"
                f"{len(type_names)} '{data_type}'"
            )
        if state['type_index'] >= len(type_names):
            state['phase'] = 'finalize'
        return False, None, data_type

    if phase == 'finalize':
        if config.enable_debug_prints:
            config.debug_print("[Atomic Debug] reference graph: resolving references")
        finalize_rna_reference_dump(state['rna_data'], state['reference_map'])
        state['phase'] = 'graph'
        return False, None, 'finalize'

    if phase == 'graph':
        if config.enable_debug_prints:
            config.debug_print("[Atomic Debug] reference graph: linking dependencies")
        graph = build_dependency_graph(state['rna_data'])
        state['dump_progress'] = 1.0
        return True, graph, None

    return True, None, None


NODE_GROUPS_BATCH_SIZE = 50
MATERIALS_BATCH_SIZE = 8
NODE_GROUP_INDEX_OBJECT_BATCH = 100
GRAPH_CATEGORY_BATCH_SIZE = 50
GRAPH_CATEGORY_BATCH_SIZES = {
    # action_all() walks every in-scene object per action — keep batches tiny for UI.
    'actions': 1,
    'objects': 20,
}


def graph_category_batch_size(category):
    """Return timer-tick batch size for a generic graph category scan."""
    return GRAPH_CATEGORY_BATCH_SIZES.get(category, GRAPH_CATEGORY_BATCH_SIZE)

_graph_used_cache = {'filepath': None, 'include_fake_users': None, 'used': None}


def _compositor_protected_node_groups(graph):
    """Node groups that are compositor trees or nested inside them (not cleanable)."""
    protected = set()
    queue = []
    for scene_data in graph.get('scenes', {}).values():
        for ref_type, ref_name in scene_data.get('references', set()):
            if ref_type == 'node_groups':
                queue.append(ref_name)
    while queue:
        ng_name = queue.pop()
        if ng_name in protected:
            continue
        protected.add(ng_name)
        ng_data = graph.get('node_groups', {}).get(ng_name)
        if not ng_data:
            continue
        for ref_type, ref_name in ng_data.get('references', set()):
            if ref_type == 'node_groups':
                queue.append(ref_name)
    return protected


def _build_ng_parent_map(graph):
    """Map each node group to parent groups that reference it (nested group users)."""
    ng_parents = defaultdict(set)
    for ng_name, ng_data in graph.get('node_groups', {}).items():
        for src_type, src_name in ng_data.get('referenced_by', set()):
            if src_type == 'node_groups':
                ng_parents[ng_name].add(src_name)
    return ng_parents


def _build_ng_to_materials(graph, ng_parents):
    """
    Materials using each node group, including via nested parent groups
    (node_group_materials parity).
    """
    ng_to_materials = defaultdict(set)
    for ng_name, ng_data in graph.get('node_groups', {}).items():
        for src_type, src_name in ng_data.get('referenced_by', set()):
            if src_type == 'materials':
                ng_to_materials[ng_name].add(src_name)

    changed = True
    while changed:
        changed = False
        for ng_name, parents in ng_parents.items():
            for parent_name in parents:
                before = len(ng_to_materials[ng_name])
                ng_to_materials[ng_name].update(ng_to_materials.get(parent_name, ()))
                parent_data = graph.get('node_groups', {}).get(parent_name, {})
                for src_type, src_name in parent_data.get('referenced_by', set()):
                    if src_type == 'materials':
                        ng_to_materials[ng_name].add(src_name)
                if len(ng_to_materials[ng_name]) > before:
                    changed = True
    return ng_to_materials


def _build_ng_to_materials_transitive(graph):
    """Map node group -> materials referenced inside its tree (incl. nested groups)."""
    ng_to_mats = defaultdict(set)
    for ng_name, ng_data in graph.get('node_groups', {}).items():
        for ref_type, ref_name in ng_data.get('references', set()):
            if ref_type == 'materials':
                ng_to_mats[ng_name].add(ref_name)

    changed = True
    while changed:
        changed = False
        for ng_name, ng_data in graph.get('node_groups', {}).items():
            for ref_type, ref_name in ng_data.get('references', set()):
                if ref_type == 'node_groups':
                    before = len(ng_to_mats[ng_name])
                    ng_to_mats[ng_name].update(ng_to_mats.get(ref_name, ()))
                    if len(ng_to_mats[ng_name]) > before:
                        changed = True
    return ng_to_mats


def _build_object_ng_map(graph):
    """Objects referencing a node group (e.g. Geometry Nodes modifiers)."""
    object_ngs = defaultdict(set)
    for obj_name, obj_data in graph.get('objects', {}).items():
        for ref_type, ref_name in obj_data.get('references', set()):
            if ref_type == 'node_groups':
                object_ngs[obj_name].add(ref_name)
    return object_ngs


def _build_ng_to_objects(graph, ng_parents, object_ngs):
    """
    Objects using each node group directly or via a parent group modifier
    (node_group_objects parity).
    """
    ng_to_objects = defaultdict(set)
    for obj_name, ng_names in object_ngs.items():
        for ng_name in ng_names:
            ng_to_objects[ng_name].add(obj_name)

    changed = True
    while changed:
        changed = False
        for ng_name, parents in ng_parents.items():
            for parent_name in parents:
                before = len(ng_to_objects[ng_name])
                ng_to_objects[ng_name].update(ng_to_objects.get(parent_name, ()))
                if len(ng_to_objects[ng_name]) > before:
                    changed = True
    return ng_to_objects


def _build_mat_slot_objects(graph):
    """Material -> objects that assign it via material slots (material_objects parity)."""
    mat_objects = defaultdict(set)
    for obj_name, obj_data in graph.get('objects', {}).items():
        for ref_type, ref_name in obj_data.get('references', set()):
            if ref_type == 'materials':
                mat_objects[ref_name].add(obj_name)
    return mat_objects


def _build_mat_gn_objects(graph, ng_to_mats_transitive, object_ngs):
    """
    Material -> scene objects using it via Geometry Nodes (material_geometry_nodes parity).
    Uses graph edges only (object->ng, ng->material).
    """
    mat_gn_objects = defaultdict(set)
    for obj_name, ng_names in object_ngs.items():
        for ng_name in ng_names:
            for mat_name in ng_to_mats_transitive.get(ng_name, ()):
                mat_gn_objects[mat_name].add(obj_name)
    return mat_gn_objects


def _build_static_node_group_graph_indices(graph):
    """Build graph-derived lookup tables (no per-group bpy walks)."""
    ng_parents = _build_ng_parent_map(graph)
    ng_to_mats_transitive = _build_ng_to_materials_transitive(graph)
    object_ngs = _build_object_ng_map(graph)
    return {
        'ng_parents': ng_parents,
        'ng_to_materials': _build_ng_to_materials(graph, ng_parents),
        'ng_to_objects': _build_ng_to_objects(graph, ng_parents, object_ngs),
        'mat_objects': _build_mat_slot_objects(graph),
        'mat_gn_objects': _build_mat_gn_objects(graph, ng_to_mats_transitive, object_ngs),
        'compositor_ngs': _compositor_protected_node_groups(graph),
        'referenced_by_count': {
            ng_name: len(ng_data.get('referenced_by', set()))
            for ng_name, ng_data in graph.get('node_groups', {}).items()
        },
    }


def begin_node_group_graph_index_build(graph):
    """Start incremental in-scene object index build for graph-only node_groups scan."""
    return {
        'graph': graph,
        'static': _build_static_node_group_graph_indices(graph),
        'object_names': list(graph.get('objects', {}).keys()),
        'obj_index': 0,
        'in_scene_objects': set(),
    }


def step_node_group_graph_index_build(state, batch_size=NODE_GROUP_INDEX_OBJECT_BATCH):
    """
    Advance in-scene object indexing (object_all parity, one batch per tick).

    Returns:
        (done, indices_or_none)
    """
    from . import users as users_stats

    if state.get('indices') is not None:
        return True, state['indices']

    object_names = state['object_names']
    start = state['obj_index']
    end = min(start + batch_size, len(object_names))
    in_scene = state['in_scene_objects']
    for offset in range(start, end):
        obj_name = object_names[offset]
        try:
            if users_stats.object_all(obj_name):
                in_scene.add(obj_name)
        except (AttributeError, KeyError, RuntimeError, ReferenceError):
            continue

    state['obj_index'] = end
    if config.enable_debug_prints:
        config.debug_print(
            f"[Atomic Debug] scene object index: {end}/{len(object_names)}"
        )
    if end < len(object_names):
        return False, None

    indices = dict(state['static'])
    indices['in_scene_objects'] = in_scene
    state['indices'] = indices
    return True, indices


def is_node_group_cleanable_from_graph(ng_name, indices, fake_user_map, memo, visited=None):
    """
    Graph-only cleanability check (node_groups_deep / is_node_group_cleanable parity).
    Uses pre-built indices from the RNA dependency graph — no live user walks per group.
    """
    if visited is None:
        visited = set()
    if ng_name in visited:
        return False
    visited.add(ng_name)

    if ng_name in memo:
        return memo[ng_name]

    if ng_name in indices['compositor_ngs']:
        memo[ng_name] = False
        return False

    if indices['referenced_by_count'].get(ng_name, 0) == 0:
        cleanable = not fake_user_map.get(ng_name, False) or config.include_fake_users
        memo[ng_name] = cleanable
        return cleanable

    all_objects = set(indices['ng_to_objects'].get(ng_name, ()))
    for mat_name in indices['ng_to_materials'].get(ng_name, ()):
        all_objects.update(indices['mat_objects'].get(mat_name, ()))
        all_objects.update(indices['mat_gn_objects'].get(mat_name, ()))

    in_scene = indices['in_scene_objects']
    all_objects_unused = not all_objects or all(
        obj_name not in in_scene for obj_name in all_objects
    )

    all_parents_unused = True
    for parent_name in indices['ng_parents'].get(ng_name, ()):
        if not is_node_group_cleanable_from_graph(
            parent_name, indices, fake_user_map, memo, visited.copy()
        ):
            all_parents_unused = False
            break

    cleanable = False
    if all_objects_unused and all_parents_unused:
        cleanable = not fake_user_map.get(ng_name, False) or config.include_fake_users

    memo[ng_name] = cleanable
    return cleanable


def begin_node_groups_analysis(graph, short_circuit=False):
    """Start graph-based batched node_groups analysis (node_groups_deep parity)."""
    from . import unused as unused_stats

    unused_stats.clear_node_group_rna_cache()
    unused_stats.begin_node_group_scan_session()

    fake_user_map = {}
    names = []
    for node_group in bpy.data.node_groups:
        try:
            if compat.is_library_or_override(node_group):
                continue
            names.append(node_group.name)
            fake_user_map[node_group.name] = bool(
                getattr(node_group, 'use_fake_user', False)
            )
        except (AttributeError, RuntimeError, ReferenceError):
            continue
    return {
        'graph': graph,
        'names': names,
        'index': 0,
        'unused': [],
        'short_circuit': short_circuit,
        'fake_user_map': fake_user_map,
        'memo': {},
        'index_build_state': begin_node_group_graph_index_build(graph),
        'indices': None,
    }


def _node_group_scan_sub_fraction(state, index_frac=None, scan_frac=None):
    """
    Map index-build and node-group scan work to 0-1 sub_fraction for the category slice.

    Weight follows object vs node-group counts so long index passes still move the bar.
    """
    obj_total = len(state.get('index_build_state', {}).get('object_names', []))
    ng_total = len(state.get('names', []))
    work_total = max(obj_total + ng_total, 1)
    index_weight = obj_total / work_total

    if index_frac is not None:
        return index_frac * index_weight
    if scan_frac is not None:
        return index_weight + scan_frac * (1.0 - index_weight)
    return 0.0


def step_node_groups_analysis(state, batch_size=NODE_GROUPS_BATCH_SIZE):
    """
    Process the next batch of node groups using graph indices only.

    Returns:
        (done, unused_list, progress_fraction, current_node_group_name)
    """
    if state['indices'] is None:
        index_done, indices = step_node_group_graph_index_build(
            state['index_build_state']
        )
        if not index_done:
            index_build = state['index_build_state']
            obj_total = max(len(index_build.get('object_names', [])), 1)
            index_frac = index_build.get('obj_index', 0) / obj_total
            return (
                False,
                state['unused'],
                _node_group_scan_sub_fraction(state, index_frac=index_frac),
                None,
            )
        state['indices'] = indices

    names = state['names']
    total = len(names)
    if total == 0:
        from . import unused as unused_stats
        unused_stats.clear_node_group_rna_cache()
        return True, state['unused'], 1.0, None

    indices = state['indices']
    fake_user_map = state['fake_user_map']
    memo = state['memo']
    from . import unused as unused_stats

    start = state['index']
    end = min(start + batch_size, total)
    current_name = None
    for offset in range(start, end):
        ng_name = names[offset]
        current_name = ng_name
        if config.enable_debug_prints:
            config.debug_print(
                f"[Atomic Debug] node_groups scan: {offset + 1}/{total} '{ng_name}'"
            )
        try:
            if not is_node_group_cleanable_from_graph(
                ng_name, indices, fake_user_map, memo
            ):
                continue
            # Live parity guard: graph can miss nested / linked-parent edges
            # (e.g. local "get coordinate" inside an override GN tree).
            if not unused_stats.is_node_group_cleanable(ng_name):
                if config.enable_debug_prints:
                    config.debug_print(
                        f"[Atomic Debug] node_groups: graph said cleanable but "
                        f"live kept '{ng_name}'"
                    )
                continue
            state['unused'].append(ng_name)
            if state['short_circuit']:
                unused_stats.clear_node_group_rna_cache()
                return True, state['unused'], 1.0, current_name
        except (AttributeError, KeyError, RuntimeError, ReferenceError):
            continue

    state['index'] = end
    done = end >= total
    if done:
        unused_stats.clear_node_group_rna_cache()
    scan_progress = end / total
    return (
        done,
        state['unused'],
        _node_group_scan_sub_fraction(state, scan_frac=scan_progress),
        current_name,
    )


def analyze_node_groups_from_graph(graph, short_circuit=False):
    """
    Return unused node group names using pre-built graph indices only.

    Builds indices in one pass (suitable for direct callers outside the unified scanner).
    """
    state = begin_node_groups_analysis(graph, short_circuit=short_circuit)
    while True:
        done, unused_list, _progress, _current = step_node_groups_analysis(
            state, batch_size=NODE_GROUPS_BATCH_SIZE
        )
        if done:
            return unused_list


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
                elif ref_type == 'action' or (
                    'action' in ref_type and 'datablock' not in ref_type
                    and 'faction' not in ref_type
                ):
                    ref_type_normalized = 'action'
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
                    'action': 'actions',
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


def _compute_graph_used_set(graph, include_fake_users=None):
    """
    Traverse the dependency graph from scene roots and return the used set.

    Returns:
        set of (data_type, item_name) tuples marked as reachable from roots.
    """
    if include_fake_users is None:
        include_fake_users = config.include_fake_users

    used = set()
    roots = []

    def get_all_scene_collections(root_collection):
        """Recursively get all collections in the scene hierarchy."""
        collections = []
        if root_collection and not compat.is_library_or_override(root_collection):
            try:
                collections.append(root_collection)
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
                        continue
            except (AttributeError, RuntimeError, ReferenceError):
                pass
        return collections

    for scene in bpy.data.scenes:
        if compat.is_library_or_override(scene):
            continue
        try:
            roots.append(('scenes', scene.name))
        except (AttributeError, RuntimeError, ReferenceError):
            pass

        scene_objects = _safe_snapshot(scene.objects)
        for obj in scene_objects:
            if obj is None:
                continue
            try:
                roots.append(('objects', obj.name))
                if hasattr(obj, 'data') and obj.data and hasattr(obj.data, 'name'):
                    try:
                        data_type_map = {
                            'LIGHT': 'lights',
                            'MESH': 'meshes',
                            'ARMATURE': 'armatures',
                            'CURVE': 'curves',
                            'SURFACE': 'curves',
                            'FONT': 'curves',
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
                continue

        if scene.world and not compat.is_library_or_override(scene.world):
            roots.append(('worlds', scene.world.name))

        scene_collections = get_all_scene_collections(scene.collection)
        for collection in scene_collections:
            if collection and not compat.is_library_or_override(collection):
                roots.append(('collections', collection.name))

        if hasattr(scene, 'rigidbody_world') and scene.rigidbody_world:
            if hasattr(scene.rigidbody_world, 'collection') and scene.rigidbody_world.collection:
                if not compat.is_library_or_override(scene.rigidbody_world.collection):
                    roots.append(('collections', scene.rigidbody_world.collection.name))

        for collection in scene_collections:
            collection_objects = _safe_snapshot(collection.objects)
            for obj in collection_objects:
                if obj is None:
                    continue
                try:
                    roots.append(('objects', obj.name))
                    if hasattr(obj, 'data') and obj.data and hasattr(obj.data, 'name'):
                        try:
                            data_type_map = {
                                'LIGHT': 'lights',
                                'MESH': 'meshes',
                                'ARMATURE': 'armatures',
                                'CURVE': 'curves',
                                'SURFACE': 'curves',
                                'FONT': 'curves',
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
                    continue

    if not include_fake_users:
        try:
            data_block_types = _get_data_block_types()
            for data_type, data_collection in data_block_types.items():
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
                        continue
        except Exception:
            pass

    visited = set()
    queue = list(roots)
    while queue:
        data_type, item_name = queue.pop(0)
        if (data_type, item_name) in visited:
            continue
        visited.add((data_type, item_name))
        used.add((data_type, item_name))
        if data_type in graph and item_name in graph[data_type]:
            for ref_type, ref_name in graph[data_type][item_name]['references']:
                if (ref_type, ref_name) not in visited:
                    queue.append((ref_type, ref_name))

    return used


def get_cached_graph_used_set(graph, include_fake_users=None):
    """Return graph reachability set, recomputing only when file or fake-user setting changes."""
    if include_fake_users is None:
        include_fake_users = config.include_fake_users
    filepath = bpy.data.filepath or ''
    cache = _graph_used_cache
    if (
        cache['filepath'] == filepath
        and cache['include_fake_users'] == include_fake_users
        and cache['used'] is not None
    ):
        return cache['used']
    used = _compute_graph_used_set(graph, include_fake_users)
    _graph_used_cache['filepath'] = filepath
    _graph_used_cache['include_fake_users'] = include_fake_users
    _graph_used_cache['used'] = used
    return used


def clear_graph_used_cache():
    """Drop cached graph reachability (call on blend-file change or cache invalidation)."""
    global _graph_used_cache
    _graph_used_cache = {'filepath': None, 'include_fake_users': None, 'used': None}


def begin_materials_analysis(graph, short_circuit=False, include_fake_users=None):
    """Start batched materials unused analysis (graph reachability + fallback session)."""
    from . import users as users_stats

    users_stats._get_material_rna_session()
    materials = []
    for material in bpy.data.materials:
        try:
            if compat.is_library_or_override(material):
                continue
            materials.append(material)
        except (AttributeError, RuntimeError, ReferenceError):
            continue
    return {
        'graph': graph,
        'include_fake_users': include_fake_users,
        'used': None,
        'materials': materials,
        'index': 0,
        'unused': [],
        'short_circuit': short_circuit,
    }


def step_materials_analysis(state, batch_size=MATERIALS_BATCH_SIZE):
    """
    Process the next batch of materials for unused detection.

    Returns:
        (done, unused_list, progress_fraction, current_material_name)
    """
    from . import users as users_stats

    if state['used'] is None:
        state['used'] = get_cached_graph_used_set(
            state['graph'],
            state['include_fake_users'],
        )
        if config.enable_debug_prints:
            config.debug_print(
                f"[Atomic Debug] materials scan: graph used set ready "
                f"({len(state['used'])} nodes)"
            )
        return False, state['unused'], 0.05, None

    materials = state['materials']
    total = len(materials)
    if total == 0:
        users_stats.clear_material_scan_caches()
        return True, state['unused'], 1.0, None

    start = state['index']
    end = min(start + batch_size, total)
    current_name = None
    for offset in range(start, end):
        material = materials[offset]
        item_name = material.name
        current_name = item_name
        if config.enable_debug_prints:
            config.debug_print(
                f"[Atomic Debug] materials scan: {offset + 1}/{total} '{item_name}'"
            )
        if ('materials', item_name) in state['used']:
            continue
        try:
            if users_stats.material_has_scene_reachable_user(
                item_name, material=material
            ):
                continue
        except (AttributeError, KeyError, RuntimeError, ReferenceError):
            pass
        state['unused'].append(item_name)
        if state['short_circuit']:
            users_stats.clear_material_scan_caches()
            return True, state['unused'], 1.0, current_name

    state['index'] = end
    done = end >= total
    if done:
        users_stats.clear_material_scan_caches()
    return done, state['unused'], end / total, current_name


def begin_graph_category_analysis(
    graph,
    category,
    short_circuit=False,
    include_fake_users=None,
):
    """Start batched unused analysis for a generic graph category (actions, objects, etc.)."""
    do_not_flag = {
        'images': ["Render Result", "Viewer Node", "D-NOISE Export"],
    }
    names = []
    if category in _DATA_BLOCK_TYPE_NAMES:
        try:
            data_block_types = _get_data_block_types()
            if category in data_block_types:
                category_datablocks = _safe_snapshot(data_block_types[category])
                skip_names = do_not_flag.get(category, [])
                for datablock in category_datablocks:
                    if datablock is None:
                        continue
                    try:
                        if compat.is_library_or_override(datablock):
                            continue
                        item_name = datablock.name
                        if item_name in skip_names:
                            continue
                        names.append(item_name)
                    except (AttributeError, RuntimeError, ReferenceError):
                        continue
        except Exception:
            pass
    return {
        'graph': graph,
        'category': category,
        'include_fake_users': include_fake_users,
        'used': None,
        'names': names,
        'index': 0,
        'unused': [],
        'short_circuit': short_circuit,
        'batch_size': graph_category_batch_size(category),
    }


def step_graph_category_analysis(state, batch_size=None):
    """
    Process the next batch of items for a generic graph category.

    Returns:
        (done, unused_list, progress_fraction, current_item_name)
    """
    from . import users

    category = state['category']
    if batch_size is None:
        batch_size = state.get('batch_size', GRAPH_CATEGORY_BATCH_SIZE)

    if state['used'] is None:
        state['used'] = get_cached_graph_used_set(
            state['graph'],
            state['include_fake_users'],
        )
        if config.enable_debug_prints:
            config.debug_print(
                f"[Atomic Debug] {category} scan: graph used set ready "
                f"({len(state['used'])} nodes)"
            )
        return False, state['unused'], 0.05, None

    names = state['names']
    total = len(names)
    if total == 0:
        return True, state['unused'], 1.0, None

    used = state['used']
    start = state['index']
    end = min(start + batch_size, total)
    current_name = None
    for offset in range(start, end):
        item_name = names[offset]
        current_name = item_name
        if config.enable_debug_prints:
            config.debug_print(
                f"[Atomic Debug] {category} scan: {offset + 1}/{total} '{item_name}'"
            )
        if (category, item_name) in used:
            continue
        if category == 'objects':
            try:
                if users.object_all(item_name):
                    continue
            except (AttributeError, KeyError, RuntimeError, ReferenceError):
                pass
        elif category == 'actions':
            try:
                if users.action_all(item_name):
                    continue
            except (AttributeError, KeyError, RuntimeError, ReferenceError):
                pass
        state['unused'].append(item_name)
        if state['short_circuit']:
            return True, state['unused'], 1.0, current_name

    state['index'] = end
    done = end >= total
    return done, state['unused'], end / total, current_name


def analyze_unused_from_graph(
    graph,
    category,
    include_fake_users=None,
    short_circuit=False,
):
    """
    Determine unused items using the dependency graph.
    
    Args:
        graph: Dependency graph from build_dependency_graph()
        category: Category to analyze ('images', 'materials', etc.)
        include_fake_users: Whether to treat fake users as used (defaults to config.include_fake_users)
        short_circuit: If True, return as soon as the first unused item is found (quick scan)
    
    Returns:
        List of unused item names for the specified category
    """
    from . import users

    if include_fake_users is None:
        include_fake_users = config.include_fake_users
    
    config.debug_print(f"[Atomic Debug] RNA Analysis: Analyzing unused {category}...")

    if category == 'materials':
        users._get_material_rna_session()
        config.debug_print(
            "[Atomic Debug] RNA Analysis: Material fallback session ready"
        )

    if category not in _DATA_BLOCK_TYPE_NAMES:
        config.debug_print(f"[Atomic Warning] RNA Analysis: Unknown category '{category}'")
        return []

    if config.enable_debug_prints:
        total_nodes = sum(len(items) for items in graph.values())
        config.debug_print(f"[Atomic Debug] RNA Analysis: Graph has {total_nodes} total nodes")
        collection_count = 0
        for coll_name, coll_data in graph.get('collections', {}).items():
            refs = coll_data.get('references', set())
            obj_refs = [r for r in refs if r[0] == 'objects']
            if obj_refs and collection_count < 3:
                config.debug_print(
                    f"[Atomic Debug] RNA Analysis: Collection '{coll_name}' references "
                    f"{len(obj_refs)} objects (sample: {[r[1] for r in list(obj_refs)[:3]]})"
                )
                collection_count += 1

    used = get_cached_graph_used_set(graph, include_fake_users)

    if category == 'node_groups':
        unused = analyze_node_groups_from_graph(graph, short_circuit=short_circuit)
        config.debug_print(
            f"[Atomic Debug] RNA Analysis: Found {len(unused)} unused "
            f"{category} (graph scan)"
        )
        return unused

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
            if item_name in category_do_not_flag:
                continue

            if (category, item_name) not in used:
                # Objects that appear in a scene collection must stay (traceable to a scene), even
                # if the RNA graph missed them (e.g. mesh parented to an out-of-scene armature).
                if category == 'objects':
                    try:
                        if users.object_all(item_name):
                            continue
                    except (AttributeError, KeyError, RuntimeError, ReferenceError):
                        pass
                if category == 'actions':
                    # Keep actions that still have scene/object users (NLA, shape keys, etc.)
                    try:
                        if users.action_all(item_name):
                            continue
                    except (AttributeError, KeyError, RuntimeError, ReferenceError):
                        pass
                if category == 'materials':
                    # Cleanability rule (issue #5): keep only when a
                    # scene-reachable object or brush still uses this material
                    # (RNA graph miss). Session cache matches material_objects /
                    # material_geometry_nodes / material_brushes semantics.
                    try:
                        if users.material_has_scene_reachable_user(
                            item_name, material=datablock
                        ):
                            continue
                    except (AttributeError, KeyError, RuntimeError, ReferenceError):
                        pass
                unused.append(item_name)
                if short_circuit:
                    config.debug_print(
                        f"[Atomic Debug] RNA Analysis: Short-circuit unused "
                        f"{category} (found '{item_name}')"
                    )
                    if category == 'materials':
                        users.clear_material_scan_caches()
                    return unused
        except (AttributeError, RuntimeError, ReferenceError):
            # Datablock may be invalid
            continue
    
    config.debug_print(f"[Atomic Debug] RNA Analysis: Found {len(unused)} unused {category}")
    if category == 'materials':
        users.clear_material_scan_caches()
    return unused

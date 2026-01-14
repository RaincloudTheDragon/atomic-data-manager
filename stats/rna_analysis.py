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
DATA_BLOCK_TYPES = {
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
            for data_type, data_collection in DATA_BLOCK_TYPES.items():
                if hasattr(data_collection, 'bl_rna'):
                    if data_collection.bl_rna.identifier == rna_identifier:
                        return True
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
            for data_type, data_collection in DATA_BLOCK_TYPES.items():
                if hasattr(data_collection, 'bl_rna'):
                    if data_collection.bl_rna.identifier == rna_identifier:
                        return True
    return False


def _extract_references_from_datablock(datablock):
    """Extract all data-block references from a single data-block instance."""
    references = []
    
    try:
        rna = datablock.bl_rna
        for prop in rna.properties:
            # Skip internal/read-only properties
            if prop.identifier.startswith('_') or prop.is_readonly:
                continue
            
            # Check for pointer properties to ID data-blocks
            if _is_id_datablock_property(prop):
                try:
                    value = getattr(datablock, prop.identifier, None)
                    if value and hasattr(value, 'name'):
                        references.append({
                            'property': prop.identifier,
                            'type': value.bl_rna.identifier if hasattr(value, 'bl_rna') else 'unknown',
                            'name': value.name
                        })
                except (AttributeError, TypeError):
                    pass
            
            # Check for collection properties containing ID data-blocks
            elif _is_id_datablock_collection(prop):
                try:
                    collection = getattr(datablock, prop.identifier, None)
                    if collection:
                        for item in collection:
                            if hasattr(item, 'name'):
                                references.append({
                                    'property': prop.identifier,
                                    'type': item.bl_rna.identifier if hasattr(item, 'bl_rna') else 'unknown',
                                    'name': item.name
                                })
                except (AttributeError, TypeError):
                    pass
            
            # Special handling for nested structures (e.g., node trees)
            # Check if property is a pointer that might contain nested references
            elif prop.type == 'POINTER' and hasattr(prop, 'fixed_type'):
                try:
                    value = getattr(datablock, prop.identifier, None)
                    if value:
                        # Recursively extract from nested structures
                        nested_refs = _extract_references_from_datablock(value)
                        references.extend(nested_refs)
                except (AttributeError, TypeError, RecursionError):
                    pass
    
    except (AttributeError, TypeError):
        pass
    
    return references


def _extract_node_tree_references(node_tree):
    """Extract references from a node tree (materials, compositor, etc.)."""
    references = []
    
    if not node_tree:
        return references
    
    try:
        for node in node_tree.nodes:
            # Check node properties for data-block references
            node_refs = _extract_references_from_datablock(node)
            references.extend(node_refs)
            
            # Special handling for group nodes
            if hasattr(node, 'node_tree') and node.node_tree:
                references.append({
                    'property': 'node_tree',
                    'type': 'NodeTree',
                    'name': node.node_tree.name
                })
                # Recursively check nested node tree
                nested_refs = _extract_node_tree_references(node.node_tree)
                references.extend(nested_refs)
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
    
    # Initialize structure
    for data_type in DATA_BLOCK_TYPES.keys():
        rna_data[data_type] = {}
    
    # Extract references from all data-blocks
    for data_type, data_collection in DATA_BLOCK_TYPES.items():
        config.debug_print(f"[Atomic Debug] RNA Analysis: Processing {data_type}...")
        
        for datablock in data_collection:
            # Skip library-linked and override datablocks
            if compat.is_library_or_override(datablock):
                continue
            
            item_name = datablock.name
            references = []
            
            # Extract direct references
            direct_refs = _extract_references_from_datablock(datablock)
            references.extend(direct_refs)
            
            # Special handling for materials (node trees)
            if data_type == 'materials' and hasattr(datablock, 'node_tree') and datablock.node_tree:
                node_refs = _extract_node_tree_references(datablock.node_tree)
                references.extend(node_refs)
            
            # Special handling for node groups
            if data_type == 'node_groups':
                node_refs = _extract_node_tree_references(datablock)
                references.extend(node_refs)
            
            # Special handling for scenes (compositor)
            if data_type == 'scenes':
                node_tree = compat.get_scene_compositor_node_tree(datablock)
                if node_tree:
                    node_refs = _extract_node_tree_references(node_tree)
                    references.extend(node_refs)
            
            # Store references
            rna_data[data_type][item_name] = {
                'references': references,
                'referenced_by': []  # Will be populated in reverse pass
            }
            
            # Build reverse reference map
            for ref in references:
                ref_type = ref.get('type', '').lower()
                ref_name = ref.get('name', '')
                
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
                
                mapped_type = type_mapping.get(ref_type, ref_type)
                if mapped_type in DATA_BLOCK_TYPES:
                    if mapped_type not in reference_map:
                        reference_map[mapped_type] = {}
                    if ref_name not in reference_map[mapped_type]:
                        reference_map[mapped_type][ref_name] = []
                    
                    reference_map[mapped_type][ref_name].append({
                        'type': data_type,
                        'name': item_name,
                        'property': ref.get('property', '')
                    })
    
    # Populate reverse references
    for data_type, items in reference_map.items():
        for item_name, sources in items.items():
            if data_type in rna_data and item_name in rna_data[data_type]:
                rna_data[data_type][item_name]['referenced_by'] = sources
    
    config.debug_print(f"[Atomic Debug] RNA Analysis: Reference dump complete. Processed {sum(len(items) for items in rna_data.values())} data-blocks.")
    
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
    for data_type in DATA_BLOCK_TYPES.keys():
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
                
                mapped_type = type_mapping.get(ref_type, ref_type)
                if mapped_type in DATA_BLOCK_TYPES:
                    graph[data_type][item_name]['references'].add((mapped_type, ref_name))
    
    # Build reverse references (what references this)
    for data_type, items in rna_data.items():
        for item_name, item_data in items.items():
            for source in item_data.get('referenced_by', []):
                source_type = source.get('type', '')
                source_name = source.get('name', '')
                
                if source_type in graph and source_name in graph[source_type]:
                    graph[source_type][source_name]['referenced_by'].add((data_type, item_name))
    
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
    
    if category not in DATA_BLOCK_TYPES:
        config.debug_print(f"[Atomic Warning] RNA Analysis: Unknown category '{category}'")
        return []
    
    # Mark all items as unused initially
    used = set()
    
    # Find root items (those that are directly used in scenes/view layers)
    roots = []
    
    # Objects in scenes/view layers
    for scene in bpy.data.scenes:
        if compat.is_library_or_override(scene):
            continue
        for obj in scene.objects:
            if not compat.is_library_or_override(obj):
                roots.append(('objects', obj.name))
        
        # World assigned to scene
        if scene.world and not compat.is_library_or_override(scene.world):
            roots.append(('worlds', scene.world.name))
        
        # Collections in scene
        for collection in scene.collection.children_recursive:
            if not compat.is_library_or_override(collection):
                roots.append(('collections', collection.name))
    
    # Collections directly in scenes
    for scene in bpy.data.scenes:
        if not compat.is_library_or_override(scene):
            if scene.collection and not compat.is_library_or_override(scene.collection):
                roots.append(('collections', scene.collection.name))
    
    # Fake users
    if not include_fake_users:
        for data_type, data_collection in DATA_BLOCK_TYPES.items():
            for datablock in data_collection:
                if compat.is_library_or_override(datablock):
                    continue
                if hasattr(datablock, 'use_fake_user') and datablock.use_fake_user:
                    roots.append((data_type, datablock.name))
    
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
    for datablock in DATA_BLOCK_TYPES[category]:
        if compat.is_library_or_override(datablock):
            continue
        
        item_name = datablock.name
        if (category, item_name) not in used:
            if item_name not in category_do_not_flag:
                unused.append(item_name)
    
    config.debug_print(f"[Atomic Debug] RNA Analysis: Found {len(unused)} unused {category}")
    return unused

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

This module provides API compatibility functions for handling differences
between Blender 4.2 LTS, 4.5 LTS, and 5.0.

"""

import bpy
from bpy.utils import register_class, unregister_class
from . import version


def safe_register_class(cls):
    """
    Safely register a class, handling any version-specific registration issues.
    
    Args:
        cls: The class to register
    
    Returns:
        bool: True if registration succeeded, False otherwise
    """
    try:
        register_class(cls)
        return True
    except Exception as e:
        print(f"Warning: Failed to register {cls.__name__}: {e}")
        return False


def safe_unregister_class(cls):
    """
    Safely unregister a class, handling any version-specific unregistration issues.
    
    Args:
        cls: The class to unregister
    
    Returns:
        bool: True if unregistration succeeded, False otherwise
    """
    try:
        unregister_class(cls)
        return True
    except Exception as e:
        print(f"Warning: Failed to unregister {cls.__name__}: {e}")
        return False


def get_addon_prefs():
    """
    Get the addon preferences instance, compatible across versions.
    
    Returns:
        AddonPreferences or None: The addon preferences instance if found
    """
    prefs = bpy.context.preferences
    for addon in prefs.addons.values():
        ap = getattr(addon, "preferences", None)
        if ap and hasattr(ap, "enable_missing_file_warning"):
            return ap
    return None


def get_geometry_nodes_modifier_node_group(modifier):
    """
    Get the node group from a geometry nodes modifier, handling version differences.
    
    Args:
        modifier: The modifier object
    
    Returns:
        NodeGroup or None: The node group if available
    """
    if not hasattr(modifier, 'type') or modifier.type != 'NODES':
        return None
    
    # Check for node_group attribute (available in all supported versions)
    if hasattr(modifier, 'node_group') and modifier.node_group:
        return modifier.node_group
    
    return None


def is_geometry_nodes_modifier(modifier):
    """
    Check if a modifier is a geometry nodes modifier, compatible across versions.
    
    Args:
        modifier: The modifier object
    
    Returns:
        bool: True if the modifier is a geometry nodes modifier
    """
    if not hasattr(modifier, 'type'):
        return False
    
    return modifier.type == 'NODES'


def get_node_tree_from_node(node):
    """
    Get the node tree from a node, handling version differences.
    
    Args:
        node: The node object
    
    Returns:
        NodeTree or None: The node tree if available
    """
    if hasattr(node, 'node_tree') and node.node_tree:
        return node.node_tree
    return None


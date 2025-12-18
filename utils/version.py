"""
This module provides version detection and comparison utilities for
multi-version Blender support (4.2 LTS, 4.5 LTS, and 5.0).

"""

import bpy

# Version constants
VERSION_4_2_LTS = (4, 2, 0)
VERSION_4_5_LTS = (4, 5, 0)
VERSION_5_0 = (5, 0, 0)


def get_blender_version():
    """
    Returns the current Blender version as a tuple (major, minor, patch).
    
    Returns:
        tuple: (major, minor, patch) version numbers
    """
    return bpy.app.version


def get_version_string():
    """
    Returns the current Blender version as a string (e.g., "4.2.0").
    
    Returns:
        str: Version string in format "major.minor.patch"
    """
    version = get_blender_version()
    return f"{version[0]}.{version[1]}.{version[2]}"


def is_version_at_least(major, minor=0, patch=0):
    """
    Check if the current Blender version is at least the specified version.
    
    Args:
        major (int): Major version number
        minor (int): Minor version number (default: 0)
        patch (int): Patch version number (default: 0)
    
    Returns:
        bool: True if current version >= specified version
    """
    current = get_blender_version()
    target = (major, minor, patch)
    
    if current[0] != target[0]:
        return current[0] > target[0]
    if current[1] != target[1]:
        return current[1] > target[1]
    return current[2] >= target[2]


def is_version_less_than(major, minor=0, patch=0):
    """
    Check if the current Blender version is less than the specified version.
    
    Args:
        major (int): Major version number
        minor (int): Minor version number (default: 0)
        patch (int): Patch version number (default: 0)
    
    Returns:
        bool: True if current version < specified version
    """
    return not is_version_at_least(major, minor, patch)


def get_version_category():
    """
    Returns the version category string for the current Blender version.
    
    Returns:
        str: '4.2', '4.5', or '5.0' based on the current version
    """
    version = get_blender_version()
    major, minor = version[0], version[1]
    
    if major == 4:
        if minor < 5:
            return '4.2'
        else:
            return '4.5'
    elif major >= 5:
        return '5.0'
    else:
        # Fallback for older versions
        return f"{major}.{minor}"


def is_version_4_2():
    """Check if running Blender 4.2 LTS (4.2.x only, not 4.3 or 4.4)."""
    version = get_blender_version()
    return version[0] == 4 and version[1] == 2


def is_version_4_5():
    """Check if running Blender 4.5 LTS."""
    return is_version_at_least(4, 5, 0) and is_version_less_than(5, 0, 0)


def is_version_5_0():
    """Check if running Blender 5.0 or later."""
    return is_version_at_least(5, 0, 0)


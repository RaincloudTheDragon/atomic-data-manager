"""
This module provides API compatibility functions for handling differences
between Blender 4.2 LTS, 4.5 LTS, and 5.0.

"""

import os
import bpy
from bpy.utils import register_class, unregister_class
from . import version
from .. import config


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


def get_scene_compositor_node_tree(scene):
    """
    Get the compositor node tree from a scene, handling version differences.
    
    In Blender 4.2/4.5: scene.node_tree
    In Blender 5.0+: scene.compositing_node_group
    
    Args:
        scene: The scene object
    
    Returns:
        NodeTree or None: The compositor node tree if available
    """
    # Blender 5.0+ uses compositing_node_group (not compositing_node_tree!)
    if version.is_version_at_least(5, 0, 0):
        # Try compositing_node_group first (Blender 5.0+)
        try:
            node_tree = getattr(scene, 'compositing_node_group', None)
            config.debug_print(f"[Atomic Debug] get_scene_compositor_node_tree: scene='{scene.name}', use_nodes={scene.use_nodes}, compositing_node_group={node_tree}")
            if node_tree:
                config.debug_print(f"[Atomic Debug] get_scene_compositor_node_tree: Found compositor node tree: {node_tree.name}")
                return node_tree
        except (AttributeError, TypeError) as e:
            config.debug_print(f"[Atomic Debug] get_scene_compositor_node_tree: compositing_node_group access failed: {e}")
        
        # Fallback: try compositing_node_tree (in case it exists in some versions)
        try:
            node_tree = getattr(scene, 'compositing_node_tree', None)
            config.debug_print(f"[Atomic Debug] get_scene_compositor_node_tree: compositing_node_tree={node_tree}")
            if node_tree:
                config.debug_print(f"[Atomic Debug] get_scene_compositor_node_tree: Found via compositing_node_tree: {node_tree.name}")
                return node_tree
        except (AttributeError, TypeError) as e:
            config.debug_print(f"[Atomic Debug] get_scene_compositor_node_tree: compositing_node_tree access failed: {e}")
    else:
        # Blender 4.2/4.5 uses node_tree
        try:
            node_tree = getattr(scene, 'node_tree', None)
            if node_tree:
                return node_tree
        except (AttributeError, TypeError):
            pass
    return None


def is_library_or_override(datablock):
    """
    Check if a datablock is library-linked or an override.
    Atomic should completely ignore all datablocks within libraries.
    
    Args:
        datablock: The datablock to check
    
    Returns:
        bool: True if the datablock is library-linked or an override, False otherwise
    """
    # Check if datablock is linked from a library
    if hasattr(datablock, 'library') and datablock.library:
        return True
    
    # Check if datablock is an override (Blender 3.0+)
    if hasattr(datablock, 'override_library') and datablock.override_library:
        return True
    
    return False


def is_object_linked_without_override(obj):
    """
    True if obj comes from another .blend file but is not a library override.

    Override objects live in the current file and may have local modifiers
    (e.g. Geometry Nodes) that reference local materials or images; those
    must be scanned. Purely linked objects have no such local stack here.
    """
    lib = getattr(obj, "library", None)
    ovl = getattr(obj, "override_library", None)
    return lib is not None and ovl is None


# --- Blend-file storage (lives here so bl_ext dev sync cannot miss a separate module) ---

_cache_report = None
_cache_light = None
_cache_vert_sum = None


def invalidate_cache():
    global _cache_report, _cache_light, _cache_vert_sum
    _cache_report = None
    _cache_light = None
    _cache_vert_sum = None


def _mesh_vertex_sum_sample():
    """Cheap geometry signature so cache invalidates on edit without save."""
    s = 0
    for m in bpy.data.meshes:
        try:
            s += len(m.vertices)
        except (AttributeError, RuntimeError, ReferenceError):
            pass
        if s > 50_000_000:
            break
    return s


def _light_fingerprint():
    fp = bpy.data.filepath
    try:
        mt = os.stat(fp).st_mtime if fp else 0
    except OSError:
        mt = 0
    return (
        fp,
        mt,
        len(bpy.data.meshes),
        len(bpy.data.images),
        len(bpy.data.materials),
        len(bpy.data.node_groups),
        len(bpy.data.objects),
        len(bpy.data.armatures),
        len(bpy.data.collections),
    )


def _skip_linked(id_block):
    return getattr(id_block, "library", None) is not None


# Set at start of build_report(); object.data IDs whose users are overridden objects
_storage_override_data_ids = frozenset()


def _object_data_override_ids():
    """IDs used as ob.data for at least one object with a library override."""
    s = set()
    for ob in bpy.data.objects:
        if getattr(ob, "override_library", None) and ob.data is not None:
            s.add(ob.data)
    return frozenset(s)


def is_library_override_storage(id_block):
    """
    True if this ID is a library override, including object-data reached only
    via an overridden Object (obdata may lack override_library).
    """
    if id_block is None:
        return False
    if getattr(id_block, "override_library", None):
        return True
    return id_block in _storage_override_data_ids


def _override_weight_factor(id_block):
    return 0.08 if is_library_override_storage(id_block) else 1.0


def _mesh_size_bytes(m):
    """Rough serialized footprint estimate (verts/loops/faces), scaled for overrides."""
    if _skip_linked(m):
        return None
    try:
        v, l, p = len(m.vertices), len(m.loops), len(m.polygons)
    except (AttributeError, RuntimeError, ReferenceError):
        return None
    ow = _override_weight_factor(m)
    base = (v * 28 + l * 6 + p * 10 + 512) * ow
    return max(64, int(base))


def _image_entry(img):
    if _skip_linked(img):
        return None
    embedded = 0
    pf = getattr(img, "packed_file", None)
    if pf:
        try:
            data = pf.data
            if data:
                embedded = len(data)
        except (AttributeError, TypeError, RuntimeError):
            pass
    if embedded == 0:
        pfs = getattr(img, "packed_files", None)
        if pfs:
            for p in pfs:
                try:
                    if hasattr(p, "data") and p.data:
                        embedded += len(p.data)
                except (AttributeError, RuntimeError):
                    pass
    ow = _override_weight_factor(img)
    if embedded > 0:
        size_b = max(1, int(embedded * ow))
        return ("images", img.name, embedded, size_b, "packed")
    size_b = max(1, int(256 * ow))
    return ("images", img.name, 0, size_b, "external")


def _armature_size_bytes(a):
    if _skip_linked(a):
        return None
    try:
        n = len(a.bones)
    except (AttributeError, RuntimeError, ReferenceError):
        n = 0
    ow = _override_weight_factor(a)
    return int((2048 + n * 320) * ow)


def _curve_size_bytes(c):
    if _skip_linked(c):
        return None
    try:
        n = sum(len(s.points) for s in c.splines)
    except (AttributeError, RuntimeError, ReferenceError):
        n = 0
    ow = _override_weight_factor(c)
    return int((1024 + n * 24) * ow)


def _node_tree_size_bytes(nt):
    if not nt or _skip_linked(nt):
        return None
    try:
        n = len(nt.nodes) + len(nt.links)
    except (AttributeError, RuntimeError, ReferenceError):
        n = 0
    ow = _override_weight_factor(nt)
    return int((2048 + n * 96) * ow)


def _action_size_bytes(act):
    if _skip_linked(act):
        return None
    try:
        kp = sum(len(fc.keyframe_points) for fc in act.fcurves)
        fc = len(act.fcurves)
    except (AttributeError, RuntimeError, ReferenceError):
        kp, fc = 0, 0
    ow = _override_weight_factor(act)
    return int((256 + kp * 20 + fc * 80) * ow)


def _object_size_bytes(ob):
    if _skip_linked(ob):
        return None
    ow = _override_weight_factor(ob)
    return int(192 * ow)


def _texture_size_bytes(tex):
    if _skip_linked(tex):
        return None
    ow = _override_weight_factor(tex)
    return int(512 * ow)


def _volume_size_bytes(vol):
    if _skip_linked(vol):
        return None
    ow = _override_weight_factor(vol)
    return int(4096 * ow)


def _pointcloud_size_bytes(pc):
    if _skip_linked(pc):
        return None
    try:
        n = len(pc.points)
    except (AttributeError, RuntimeError, ReferenceError):
        n = 0
    ow = _override_weight_factor(pc)
    return int((512 + n * 16) * ow)


def _sound_entry(snd):
    if _skip_linked(snd):
        return None
    embedded = 0
    pf = getattr(snd, "packed_file", None)
    if pf:
        try:
            if hasattr(pf, "data") and pf.data:
                embedded = len(pf.data)
        except (AttributeError, TypeError, RuntimeError):
            pass
    ow = _override_weight_factor(snd)
    if embedded > 0:
        return ("sounds", snd.name, embedded, max(1, int(embedded * ow)), "packed")
    return ("sounds", snd.name, 0, max(1, int(256 * ow)), "external")


def _font_entry(font):
    if _skip_linked(font):
        return None
    embedded = 0
    pf = getattr(font, "packed_file", None)
    if pf and hasattr(pf, "data") and pf.data:
        try:
            embedded = len(pf.data)
        except (TypeError, RuntimeError):
            embedded = 0
    ow = _override_weight_factor(font)
    if embedded > 0:
        return ("fonts", font.name, embedded, max(1, int(embedded * ow)), "packed")
    return None


def _collection_size_bytes(coll):
    if _skip_linked(coll):
        return None
    try:
        no = len(coll.objects)
        nc = len(coll.children)
    except (AttributeError, RuntimeError, ReferenceError):
        return None
    ow = _override_weight_factor(coll)
    return max(64, int((512 + no * 96 + nc * 256) * ow))


def _fmt_bytes(n):
    if n >= 1048576:
        return f"{n / 1048576:.2f} MiB"
    if n >= 1024:
        return f"{n / 1024:.2f} KiB"
    return f"{int(n)} B"


def format_bytes(n):
    """Human-readable size for storage estimates."""
    return _fmt_bytes(n)


_STORAGE_TYPE_ICONS = {
    "Mesh": "MESH_DATA",
    "Image": "IMAGE_DATA",
    "Armature": "ARMATURE_DATA",
    "Material": "MATERIAL",
    "Object": "OBJECT_DATA",
    "Curve": "CURVE_DATA",
    "NodeTree": "NODETREE",
    "Action": "ACTION",
    "Texture": "TEXTURE",
    "Volume": "VOLUME_DATA",
    "PointCloud": "POINTCLOUD_DATA",
    "Sound": "SOUND",
    "Font": "FONT_DATA",
    "Collection": "OUTLINER_COLLECTION",
}


def storage_type_icon(type_name):
    """Blender UI icon for a storage row type label."""
    return _STORAGE_TYPE_ICONS.get(type_name, "BLANK1")


def storage_override_icon(is_lib_override):
    """Second column: library override emblem vs empty spacer."""
    return "LIBRARY_DATA_OVERRIDE" if is_lib_override else "BLANK1"


def build_report():
    """Build storage report dict. Call through get_report() for caching."""
    global _storage_override_data_ids
    _storage_override_data_ids = _object_data_override_ids()
    rows = []

    def _ov(id_block):
        return is_library_override_storage(id_block)

    for m in bpy.data.meshes:
        sz = _mesh_size_bytes(m)
        if sz is not None:
            io = _ov(m)
            rows.append(
                {
                    "type": "Mesh",
                    "name": m.name,
                    "embedded": 0,
                    "size_bytes": sz,
                    "is_lib_override": io,
                    "kind": "override" if io else "local",
                }
            )

    for img in bpy.data.images:
        e = _image_entry(img)
        if e is None:
            continue
        _typ, name, emb, sz, kind = e
        io = _ov(img)
        rows.append(
            {
                "type": "Image",
                "name": name,
                "embedded": emb,
                "size_bytes": sz,
                "is_lib_override": io,
                "kind": kind,
            }
        )

    for a in bpy.data.armatures:
        sz = _armature_size_bytes(a)
        if sz is not None:
            io = _ov(a)
            rows.append(
                {
                    "type": "Armature",
                    "name": a.name,
                    "embedded": 0,
                    "size_bytes": sz,
                    "is_lib_override": io,
                    "kind": "override" if io else "local",
                }
            )

    for c in getattr(bpy.data, "curves", []):
        sz = _curve_size_bytes(c)
        if sz is not None:
            io = _ov(c)
            rows.append(
                {
                    "type": "Curve",
                    "name": c.name,
                    "embedded": 0,
                    "size_bytes": sz,
                    "is_lib_override": io,
                    "kind": "override" if io else "local",
                }
            )

    for ng in bpy.data.node_groups:
        sz = _node_tree_size_bytes(ng)
        if sz is not None:
            io = _ov(ng)
            rows.append(
                {
                    "type": "NodeTree",
                    "name": ng.name,
                    "embedded": 0,
                    "size_bytes": sz,
                    "is_lib_override": io,
                    "kind": "override" if io else "local",
                }
            )

    for mat in bpy.data.materials:
        if _skip_linked(mat):
            continue
        sz = _node_tree_size_bytes(mat.node_tree) if mat.use_nodes and mat.node_tree else 256
        if sz is None:
            sz = 256
        ow = _override_weight_factor(mat)
        sz = int(sz * ow)
        io = _ov(mat)
        rows.append(
            {
                "type": "Material",
                "name": mat.name,
                "embedded": 0,
                "size_bytes": sz,
                "is_lib_override": io,
                "kind": "override" if io else "local",
            }
        )

    if hasattr(bpy.data, "actions"):
        for act in bpy.data.actions:
            sz = _action_size_bytes(act)
            if sz is not None:
                io = _ov(act)
                rows.append(
                    {
                        "type": "Action",
                        "name": act.name,
                        "embedded": 0,
                        "size_bytes": sz,
                        "is_lib_override": io,
                        "kind": "override" if io else "local",
                    }
                )

    for tex in getattr(bpy.data, "textures", []):
        sz = _texture_size_bytes(tex)
        if sz is not None:
            io = _ov(tex)
            rows.append(
                {
                    "type": "Texture",
                    "name": tex.name,
                    "embedded": 0,
                    "size_bytes": sz,
                    "is_lib_override": io,
                    "kind": "override" if io else "local",
                }
            )

    for ob in bpy.data.objects:
        sz = _object_size_bytes(ob)
        if sz is not None:
            io = _ov(ob)
            rows.append(
                {
                    "type": "Object",
                    "name": ob.name,
                    "embedded": 0,
                    "size_bytes": sz,
                    "is_lib_override": io,
                    "kind": "override" if io else "local",
                }
            )

    for vol in getattr(bpy.data, "volumes", []):
        sz = _volume_size_bytes(vol)
        if sz is not None:
            io = _ov(vol)
            rows.append(
                {
                    "type": "Volume",
                    "name": vol.name,
                    "embedded": 0,
                    "size_bytes": sz,
                    "is_lib_override": io,
                    "kind": "override" if io else "local",
                }
            )

    for pc in getattr(bpy.data, "pointclouds", []):
        sz = _pointcloud_size_bytes(pc)
        if sz is not None:
            io = _ov(pc)
            rows.append(
                {
                    "type": "PointCloud",
                    "name": pc.name,
                    "embedded": 0,
                    "size_bytes": sz,
                    "is_lib_override": io,
                    "kind": "override" if io else "local",
                }
            )

    for snd in getattr(bpy.data, "sounds", []):
        e = _sound_entry(snd)
        if e is None:
            continue
        _typ, name, emb, sz, kind = e
        io = _ov(snd)
        rows.append(
            {
                "type": "Sound",
                "name": name,
                "embedded": emb,
                "size_bytes": sz,
                "is_lib_override": io,
                "kind": kind,
            }
        )

    for font in getattr(bpy.data, "fonts", []):
        e = _font_entry(font)
        if e is None:
            continue
        _typ, name, emb, sz, kind = e
        io = _ov(font)
        rows.append(
            {
                "type": "Font",
                "name": name,
                "embedded": emb,
                "size_bytes": sz,
                "is_lib_override": io,
                "kind": kind,
            }
        )

    for coll in bpy.data.collections:
        sz = _collection_size_bytes(coll)
        if sz is not None:
            io = _ov(coll)
            rows.append(
                {
                    "type": "Collection",
                    "name": coll.name,
                    "embedded": 0,
                    "size_bytes": sz,
                    "is_lib_override": io,
                    "kind": "override" if io else "local",
                }
            )

    rows.sort(key=lambda r: r["size_bytes"], reverse=True)

    by_type = {}
    total_estimated = 0
    total_emb = 0
    for r in rows:
        t = r["type"]
        by_type[t] = by_type.get(t, 0) + r["size_bytes"]
        total_estimated += r["size_bytes"]
        total_emb += r.get("embedded", 0)

    type_order = sorted(by_type.keys(), key=lambda t: -by_type[t])
    by_type_sizes = [(t, by_type[t]) for t in type_order]

    return {
        "rows": rows,
        "by_type": by_type_sizes,
        "total_estimated_bytes": total_estimated,
        "total_embedded_packed": total_emb,
    }


def get_report():
    global _cache_report, _cache_light, _cache_vert_sum
    light = _light_fingerprint()
    vs = _mesh_vertex_sum_sample()
    if (
        _cache_report is not None
        and _cache_light == light
        and _cache_vert_sum == vs
    ):
        return _cache_report
    _cache_report = build_report()
    _cache_light = light
    _cache_vert_sum = vs
    return _cache_report


def format_embedded_total(n):
    """Human-readable total packed bytes embedded in the .blend."""
    return _fmt_bytes(n)

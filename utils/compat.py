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
    )


def _skip_linked(id_block):
    return getattr(id_block, "library", None) is not None


def _override_weight_factor(id_block):
    return 0.08 if getattr(id_block, "override_library", None) else 1.0


def _mesh_weight(m):
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
        weight = int(embedded * ow) + 1
        return ("images", img.name, embedded, weight, "packed")
    weight = int(256 * ow)
    return ("images", img.name, 0, weight, "external")


def _armature_weight(a):
    if _skip_linked(a):
        return None
    try:
        n = len(a.bones)
    except (AttributeError, RuntimeError, ReferenceError):
        n = 0
    ow = _override_weight_factor(a)
    return int((2048 + n * 320) * ow)


def _curve_weight(c):
    if _skip_linked(c):
        return None
    try:
        n = sum(len(s.points) for s in c.splines)
    except (AttributeError, RuntimeError, ReferenceError):
        n = 0
    ow = _override_weight_factor(c)
    return int((1024 + n * 24) * ow)


def _node_tree_weight(nt):
    if not nt or _skip_linked(nt):
        return None
    try:
        n = len(nt.nodes) + len(nt.links)
    except (AttributeError, RuntimeError, ReferenceError):
        n = 0
    ow = _override_weight_factor(nt)
    return int((2048 + n * 96) * ow)


def _action_weight(act):
    if _skip_linked(act):
        return None
    try:
        kp = sum(len(fc.keyframe_points) for fc in act.fcurves)
        fc = len(act.fcurves)
    except (AttributeError, RuntimeError, ReferenceError):
        kp, fc = 0, 0
    ow = _override_weight_factor(act)
    return int((256 + kp * 20 + fc * 80) * ow)


def _object_weight(ob):
    if _skip_linked(ob):
        return None
    ow = _override_weight_factor(ob)
    return int(192 * ow)


def _texture_weight(tex):
    if _skip_linked(tex):
        return None
    ow = _override_weight_factor(tex)
    return int(512 * ow)


def _volume_weight(vol):
    if _skip_linked(vol):
        return None
    ow = _override_weight_factor(vol)
    return int(4096 * ow)


def _pointcloud_weight(pc):
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
        return ("sounds", snd.name, embedded, int(embedded * ow) + 1, "packed")
    return ("sounds", snd.name, 0, int(256 * ow), "external")


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
        return ("fonts", font.name, embedded, int(embedded * ow) + 1, "packed")
    return None


def _fmt_bytes(n):
    if n >= 1048576:
        return f"{n / 1048576:.2f} MiB"
    if n >= 1024:
        return f"{n / 1024:.2f} KiB"
    return f"{int(n)} B"


def _fmt_weight(w):
    if w >= 1048576:
        return f"{w / 1048576:.2f}M"
    if w >= 1024:
        return f"{w / 1024:.2f}K"
    return str(int(w))


def build_report():
    """Build storage report dict. Call through get_report() for caching."""
    rows = []

    for m in bpy.data.meshes:
        w = _mesh_weight(m)
        if w is not None:
            rows.append(
                {
                    "type": "Mesh",
                    "name": m.name,
                    "embedded": 0,
                    "weight": w,
                    "kind": "override" if getattr(m, "override_library", None) else "local",
                }
            )

    for img in bpy.data.images:
        e = _image_entry(img)
        if e is None:
            continue
        typ, name, emb, w, kind = e
        rows.append(
            {
                "type": "Image",
                "name": name,
                "embedded": emb,
                "weight": w,
                "kind": kind,
            }
        )

    for a in bpy.data.armatures:
        w = _armature_weight(a)
        if w is not None:
            rows.append(
                {
                    "type": "Armature",
                    "name": a.name,
                    "embedded": 0,
                    "weight": w,
                    "kind": "override" if getattr(a, "override_library", None) else "local",
                }
            )

    for c in getattr(bpy.data, "curves", []):
        w = _curve_weight(c)
        if w is not None:
            rows.append(
                {
                    "type": "Curve",
                    "name": c.name,
                    "embedded": 0,
                    "weight": w,
                    "kind": "override" if getattr(c, "override_library", None) else "local",
                }
            )

    for ng in bpy.data.node_groups:
        w = _node_tree_weight(ng)
        if w is not None:
            rows.append(
                {
                    "type": "NodeTree",
                    "name": ng.name,
                    "embedded": 0,
                    "weight": w,
                    "kind": "override" if getattr(ng, "override_library", None) else "local",
                }
            )

    for mat in bpy.data.materials:
        if _skip_linked(mat):
            continue
        w = _node_tree_weight(mat.node_tree) if mat.use_nodes and mat.node_tree else 256
        if w is None:
            w = 256
        ow = _override_weight_factor(mat)
        w = int(w * ow)
        rows.append(
            {
                "type": "Material",
                "name": mat.name,
                "embedded": 0,
                "weight": w,
                "kind": "override" if getattr(mat, "override_library", None) else "local",
            }
        )

    if hasattr(bpy.data, "actions"):
        for act in bpy.data.actions:
            w = _action_weight(act)
            if w is not None:
                rows.append(
                    {
                        "type": "Action",
                        "name": act.name,
                        "embedded": 0,
                        "weight": w,
                        "kind": "override" if getattr(act, "override_library", None) else "local",
                    }
                )

    for tex in getattr(bpy.data, "textures", []):
        w = _texture_weight(tex)
        if w is not None:
            rows.append(
                {
                    "type": "Texture",
                    "name": tex.name,
                    "embedded": 0,
                    "weight": w,
                    "kind": "override" if getattr(tex, "override_library", None) else "local",
                }
            )

    for ob in bpy.data.objects:
        w = _object_weight(ob)
        if w is not None:
            rows.append(
                {
                    "type": "Object",
                    "name": ob.name,
                    "embedded": 0,
                    "weight": w,
                    "kind": "override" if getattr(ob, "override_library", None) else "local",
                }
            )

    for vol in getattr(bpy.data, "volumes", []):
        w = _volume_weight(vol)
        if w is not None:
            rows.append(
                {
                    "type": "Volume",
                    "name": vol.name,
                    "embedded": 0,
                    "weight": w,
                    "kind": "override" if getattr(vol, "override_library", None) else "local",
                }
            )

    for pc in getattr(bpy.data, "pointclouds", []):
        w = _pointcloud_weight(pc)
        if w is not None:
            rows.append(
                {
                    "type": "PointCloud",
                    "name": pc.name,
                    "embedded": 0,
                    "weight": w,
                    "kind": "override" if getattr(pc, "override_library", None) else "local",
                }
            )

    for snd in getattr(bpy.data, "sounds", []):
        e = _sound_entry(snd)
        if e is None:
            continue
        typ, name, emb, w, kind = e
        rows.append(
            {
                "type": "Sound",
                "name": name,
                "embedded": emb,
                "weight": w,
                "kind": kind,
            }
        )

    for font in getattr(bpy.data, "fonts", []):
        e = _font_entry(font)
        if e is None:
            continue
        typ, name, emb, w, kind = e
        rows.append(
            {
                "type": "Font",
                "name": name,
                "embedded": emb,
                "weight": w,
                "kind": kind,
            }
        )

    def sort_key(r):
        return (r["embedded"] * 10 ** 9 + r["weight"])

    rows.sort(key=sort_key, reverse=True)

    by_type = {}
    total_w = 0
    total_emb = 0
    for r in rows:
        t = r["type"]
        by_type[t] = by_type.get(t, 0) + r["weight"]
        total_w += r["weight"]
        total_emb += r["embedded"]

    type_order = sorted(by_type.keys(), key=lambda t: -by_type[t])
    by_type_pct = []
    for t in type_order:
        w = by_type[t]
        pct = (100.0 * w / total_w) if total_w else 0.0
        by_type_pct.append((t, w, pct))

    return {
        "rows": rows,
        "by_type": by_type_pct,
        "total_weight": total_w,
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


def format_row_label(r):
    """Single-line summary for UI list."""
    emb = r["embedded"]
    w = r["weight"]
    kind = r["kind"]
    if emb > 0:
        return f"{r['type']} | {_fmt_bytes(emb)} embedded | w={_fmt_weight(w)} | {kind}"
    return f"{r['type']} | w={_fmt_weight(w)} | {kind}"


def format_embedded_total(n):
    """Human-readable total packed bytes embedded in the .blend."""
    return _fmt_bytes(n)

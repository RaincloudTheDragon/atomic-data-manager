"""
This module provides API compatibility functions for handling differences
between Blender 4.2 LTS, 4.5 LTS, and 5.0.

"""

import ctypes
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


def _cache_modifier_counts():
    """Cheap counts so storage cache invalidates when physics/GeoNodes mods change."""
    nodes = cloth = soft = dp = particles = 0
    for ob in bpy.data.objects:
        try:
            for mod in ob.modifiers:
                t = getattr(mod, "type", None)
                if t == "NODES":
                    nodes += 1
                elif t == "CLOTH":
                    cloth += 1
                elif t == "SOFT_BODY":
                    soft += 1
                elif t == "DYNAMIC_PAINT":
                    dp += 1
            particles += len(getattr(ob, "particle_systems", []))
        except (AttributeError, RuntimeError, ReferenceError):
            pass
    rb = 0
    for sc in bpy.data.scenes:
        if getattr(sc, "rigidbody_world", None):
            rb += 1
    return (nodes, cloth, soft, dp, particles, rb)


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
        _cache_modifier_counts(),
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


def _action_keyframe_counts(act):
    """
    Keyframe point count and F-curve count for storage estimate.
    Blender 4.4+ layered actions store curves in ActionChannelBag under
    strips; act.fcurves is often empty, so we must walk layers/slots.
    """
    kp, fc = 0, 0
    layers = getattr(act, "layers", None)
    if layers and len(layers) > 0:
        for layer in layers:
            strips = getattr(layer, "strips", None)
            if not strips:
                continue
            for strip in strips:
                if not (hasattr(strip, "channelbags") or hasattr(strip, "channelbag")):
                    continue
                bags = getattr(strip, "channelbags", None)
                if bags and len(bags) > 0:
                    for bag in bags:
                        for fcurve in bag.fcurves:
                            fc += 1
                            try:
                                kp += len(fcurve.keyframe_points)
                            except (TypeError, AttributeError, RuntimeError, ReferenceError):
                                pass
                else:
                    slots = getattr(act, "slots", None)
                    if not slots:
                        continue
                    for slot in slots:
                        try:
                            bag = strip.channelbag(slot, ensure=False)
                        except (TypeError, AttributeError, RuntimeError, ReferenceError):
                            bag = None
                        if bag is None:
                            continue
                        for fcurve in bag.fcurves:
                            fc += 1
                            try:
                                kp += len(fcurve.keyframe_points)
                            except (TypeError, AttributeError, RuntimeError, ReferenceError):
                                pass
        return kp, fc
    fcurves = getattr(act, "fcurves", None)
    if fcurves:
        for fcurve in fcurves:
            fc += 1
            try:
                kp += len(fcurve.keyframe_points)
            except (TypeError, AttributeError, RuntimeError, ReferenceError):
                pass
    return kp, fc


def _action_size_bytes(act):
    if _skip_linked(act):
        return None
    kp, fc = _action_keyframe_counts(act)
    ow = _override_weight_factor(act)
    return max(64, int((256 + kp * 20 + fc * 80) * ow))


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


# DNA layout of blender::NodesModifierBake (64-bit). bake_size is not in RNA.
# See source/blender/makesdna/DNA_modifier_types.h
class _NodesModifierBakeDNA(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_int32),
        ("flag", ctypes.c_uint32),
        ("bake_mode", ctypes.c_uint8),
        ("bake_target", ctypes.c_int8),
        ("_pad", ctypes.c_char * 6),
        ("directory", ctypes.c_void_p),
        ("frame_start", ctypes.c_int32),
        ("frame_end", ctypes.c_int32),
        ("data_blocks_num", ctypes.c_int32),
        ("active_data_block", ctypes.c_int32),
        ("data_blocks", ctypes.c_void_p),
        ("packed", ctypes.c_void_p),
        ("_pad2", ctypes.c_void_p),
        ("bake_size", ctypes.c_int64),
    ]


_PHYS_BYTES_PER_POINT = 32


def _geonodes_bake_dna_sizes(bake):
    """
    Read packed pointer and bake_size from NodesModifierBake DNA.
    Returns (bake_size, is_packed) or None if unavailable.
    """
    if bake is None or ctypes.sizeof(ctypes.c_void_p) != 8:
        return None
    try:
        ptr = bake.as_pointer()
        if not ptr:
            return None
        dna = _NodesModifierBakeDNA.from_address(ptr)
        size = int(dna.bake_size)
        packed = bool(dna.packed)
        if size < 0:
            return None
        return (size, packed)
    except (AttributeError, TypeError, ValueError, OSError, RuntimeError):
        return None


def _append_geonodes_bake_rows(rows, is_override):
    """Append rows for GeoNodes bakes packed into the .blend."""
    # packed + bake_size DNA fields exist since Blender 4.3
    if not version.is_version_at_least(4, 3):
        return
    for ob in bpy.data.objects:
        if _skip_linked(ob):
            continue
        try:
            mods = ob.modifiers
        except (AttributeError, RuntimeError, ReferenceError):
            continue
        io = is_override(ob)
        ow = 0.08 if io else 1.0
        for mod in mods:
            if not is_geometry_nodes_modifier(mod):
                continue
            bakes = getattr(mod, "bakes", None)
            if not bakes:
                continue
            for bake in bakes:
                info = _geonodes_bake_dna_sizes(bake)
                if info is None:
                    continue
                size, packed = info
                if not packed or size <= 0:
                    continue
                node = getattr(bake, "node", None)
                node_name = getattr(node, "name", None) if node else None
                label = node_name or ("Bake %s" % getattr(bake, "bake_id", "?"))
                name = "%s / %s / %s" % (ob.name, mod.name, label)
                emb = max(1, int(size * ow))
                rows.append(
                    {
                        "type": "GeoNodesBake",
                        "name": name,
                        "embedded": emb,
                        "size_bytes": emb,
                        "is_lib_override": io,
                        "kind": "packed",
                        "owner_object": ob.name,
                        "modifier_name": mod.name,
                    }
                )


def _point_cache_in_blend(cache):
    """True when a point cache is baked into the .blend (not disk/external)."""
    if cache is None:
        return False
    try:
        if not cache.is_baked:
            return False
        if getattr(cache, "use_disk_cache", False):
            return False
        if getattr(cache, "use_external", False):
            return False
        return True
    except (AttributeError, RuntimeError, ReferenceError):
        return False


def _iter_point_cache_items(pc):
    if pc is None:
        return
    items = getattr(pc, "point_caches", None)
    try:
        if items is not None and len(items) > 0:
            for item in items:
                yield item
            return
    except (AttributeError, RuntimeError, TypeError):
        pass
    yield pc


def _physics_cache_size_bytes(cache, point_count):
    try:
        fs = int(cache.frame_start)
        fe = int(cache.frame_end)
        step = max(1, int(getattr(cache, "frame_step", 1) or 1))
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return None
    frames = max(1, (fe - fs) // step + 1)
    pts = max(1, int(point_count) if point_count else 1)
    return max(64, frames * pts * _PHYS_BYTES_PER_POINT)


def _object_mesh_vert_count(ob):
    data = getattr(ob, "data", None)
    if data is None:
        return 1
    try:
        return max(1, len(data.vertices))
    except (AttributeError, RuntimeError, ReferenceError, TypeError):
        return 1


def _append_physics_cache_row(
    rows, name, cache, point_count, is_lib_override, owner_object="", owner_scene=""
):
    if not _point_cache_in_blend(cache):
        return
    sz = _physics_cache_size_bytes(cache, point_count)
    if sz is None:
        return
    ow = 0.08 if is_lib_override else 1.0
    sz = max(64, int(sz * ow))
    cname = getattr(cache, "name", "") or ""
    display = "%s (%s)" % (name, cname) if cname else name
    row = {
        "type": "PhysicsCache",
        "name": display,
        "embedded": 0,
        "size_bytes": sz,
        "is_lib_override": is_lib_override,
        "kind": "override" if is_lib_override else "local",
    }
    if owner_object:
        row["owner_object"] = owner_object
    if owner_scene:
        row["owner_scene"] = owner_scene
    rows.append(row)


def _append_physics_cache_rows(rows, is_override):
    """Append rows for physics point caches stored inside the .blend."""
    for ob in bpy.data.objects:
        if _skip_linked(ob):
            continue
        io = is_override(ob)
        verts = _object_mesh_vert_count(ob)
        try:
            mods = ob.modifiers
        except (AttributeError, RuntimeError, ReferenceError):
            mods = []
        for mod in mods:
            mtype = getattr(mod, "type", None)
            if mtype == "CLOTH":
                pc = getattr(mod, "point_cache", None)
                for item in _iter_point_cache_items(pc):
                    _append_physics_cache_row(
                        rows, "%s / Cloth" % ob.name, item, verts, io, owner_object=ob.name
                    )
            elif mtype == "SOFT_BODY":
                pc = getattr(mod, "point_cache", None)
                for item in _iter_point_cache_items(pc):
                    _append_physics_cache_row(
                        rows, "%s / Soft Body" % ob.name, item, verts, io, owner_object=ob.name
                    )
            elif mtype == "DYNAMIC_PAINT":
                canvas = getattr(mod, "canvas_settings", None)
                surfaces = getattr(canvas, "canvas_surfaces", None) if canvas else None
                if not surfaces:
                    continue
                for surf in surfaces:
                    pc = getattr(surf, "point_cache", None)
                    fmt = getattr(surf, "surface_format", None)
                    if fmt == "IMAGE":
                        res = int(getattr(surf, "image_resolution", 256) or 256)
                        pts = max(1, res * res)
                    else:
                        pts = verts
                    sname = getattr(surf, "name", "Surface") or "Surface"
                    for item in _iter_point_cache_items(pc):
                        _append_physics_cache_row(
                            rows,
                            "%s / Dynamic Paint / %s" % (ob.name, sname),
                            item,
                            pts,
                            io,
                            owner_object=ob.name,
                        )
        try:
            psystems = ob.particle_systems
        except (AttributeError, RuntimeError, ReferenceError):
            psystems = []
        for ps in psystems:
            pc = getattr(ps, "point_cache", None)
            settings = getattr(ps, "settings", None)
            count = 1
            if settings is not None:
                try:
                    count = max(1, int(getattr(settings, "count", 1) or 1))
                except (TypeError, ValueError):
                    count = 1
            ps_name = getattr(ps, "name", "Particles") or "Particles"
            for item in _iter_point_cache_items(pc):
                _append_physics_cache_row(
                    rows, "%s / %s" % (ob.name, ps_name), item, count, io, owner_object=ob.name
                )

    for sc in bpy.data.scenes:
        if _skip_linked(sc):
            continue
        rbw = getattr(sc, "rigidbody_world", None)
        if not rbw:
            continue
        pc = getattr(rbw, "point_cache", None)
        coll = getattr(rbw, "collection", None)
        nobj = 1
        if coll is not None:
            try:
                nobj = max(1, len(coll.objects))
            except (AttributeError, RuntimeError, ReferenceError):
                nobj = 1
        io = is_override(sc)
        for item in _iter_point_cache_items(pc):
            _append_physics_cache_row(
                rows,
                "%s / Rigid Body" % sc.name,
                item,
                nobj,
                io,
                owner_scene=sc.name,
            )


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
    "GeoNodesBake": "GEOMETRY_NODES",
    "PhysicsCache": "PHYSICS",
}


def storage_type_icon(type_name):
    """Blender UI icon for a storage row type label."""
    return _STORAGE_TYPE_ICONS.get(type_name, "BLANK1")


def storage_packed_icon(type_name):
    """Packed-in-blend indicator (GeoNodes bake rows only)."""
    return "PACKAGE" if type_name == "GeoNodesBake" else "BLANK1"


def storage_override_icon(is_lib_override):
    """Library override emblem vs empty spacer."""
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

    _append_geonodes_bake_rows(rows, _ov)
    _append_physics_cache_rows(rows, _ov)

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

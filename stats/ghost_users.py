"""
Detect 'ghost' ID references (e.g. Reallusion CC / iClone import caches on Scene)
that keep bpy.users > 0 but are not real scene usage. Used by materials_deep and
any future cleanup paths.
"""

import bpy
from .. import config


def _idprop_count_material(p, mat, seen):
    """Recursively count pointers equal to `mat` under an ID property value."""
    if p is None or id(p) in seen:
        return 0
    if isinstance(p, (str, int, float, bool)):
        return 0
    seen = seen | {id(p)}
    if isinstance(p, bpy.types.Material) and p == mat:
        return 1
    n = 0
    t = type(p).__name__
    if isinstance(p, (list, tuple)):
        for x in p:
            n += _idprop_count_material(x, mat, seen)
    elif isinstance(p, dict):
        for v in p.values():
            n += _idprop_count_material(v, mat, seen)
    elif t == "IDPropertyGroup" or (hasattr(p, "keys") and hasattr(p, "values") and t != "dict"):
        try:
            for k in p.keys():
                try:
                    v = p[k]
                except Exception:
                    continue
                n += _idprop_count_material(v, mat, seen)
        except Exception:
            pass
    return n


def count_cc3_import_cache_references(material):
    """
    Count Material pointers stored in Scene ID property group CC3ImportProps
    (Character Creator 3 / iClone pipeline). These are import-cache ghosts and
    are not object/world/brush use.
    """
    m = 0
    for scene in bpy.data.scenes:
        try:
            if "CC3ImportProps" not in scene:
                continue
        except Exception:
            continue
        try:
            cc3 = scene["CC3ImportProps"]
        except Exception:
            continue
        m += _idprop_count_material(cc3, material, set())
    return m


def material_blender_users_fully_cc3_ghosts(material):
    """
    True if every material.user can be explained by CC3 import_cache pointers.

    Blender often reports *two* users per pbr cache slot: the Scene and the
    pbr_material_cache cell both contribute to bpy.types.Material.users, while
    our walk only counts Material pointers in the idprop tree (one per cell).
    So u == 2 * cc3 is normal; u == cc3 can occur when the count already matches
    1:1 in some file versions.
    """
    try:
        u = material.users
    except Exception:
        return False
    if u == 0:
        return True
    cc3 = count_cc3_import_cache_references(material)
    if cc3 == 0:
        return False
    if cc3 == u or u == 2 * cc3:
        return True
    if config.enable_debug_prints:
        config.debug_print(
            f"[Atomic Debug] ghost_users: material '{material.name}' users={u} "
            f"cc3_count={cc3} (not fully explained by CC3; keep conservative block)"
        )
    return False

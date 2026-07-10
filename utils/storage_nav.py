"""
Navigate from Atomic Storage rows to datablock users in the Blender UI.
"""

import bpy
from .. import config
from ..stats import users


def _find_area(context, area_type):
    screen = context.screen
    if context.window:
        screen = context.window.screen
    for area in screen.areas:
        if area.type == area_type:
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            return context.window, area, region
    return context.window, None, None


def resolve_targets(storage_type, id_name, owner_object="", owner_scene="", modifier_name=""):
    """
    Return navigation targets as dicts:
    object, material, action, nav (viewport|shader|dopesheet).
    """
    targets = []

    def add_objects(names, nav="viewport", material="", action=""):
        for name in names:
            if name in bpy.data.objects:
                targets.append(
                    {
                        "object": name,
                        "material": material,
                        "action": action,
                        "nav": nav,
                        "modifier": modifier_name if owner_object == name else "",
                    }
                )

    if storage_type == "Object":
        if id_name in bpy.data.objects:
            add_objects([id_name])
        return targets

    if storage_type == "GeoNodesBake":
        if owner_object:
            add_objects([owner_object])
        return targets

    if storage_type == "PhysicsCache":
        if owner_object:
            add_objects([owner_object])
            return targets
        if owner_scene:
            scene = bpy.data.scenes.get(owner_scene)
            rbw = getattr(scene, "rigidbody_world", None) if scene else None
            coll = getattr(rbw, "collection", None) if rbw else None
            if coll:
                add_objects([ob.name for ob in coll.objects])
            return targets

    if storage_type == "Mesh":
        add_objects(users.mesh_objects(id_name))
    elif storage_type == "Curve":
        add_objects(users.curve_objects(id_name))
    elif storage_type == "Armature":
        add_objects(users.armature_all(id_name))
    elif storage_type == "Volume":
        add_objects(users.volume_objects(id_name))
    elif storage_type == "PointCloud":
        add_objects(users.pointcloud_objects(id_name))
    elif storage_type == "Image":
        add_objects(users.image_viewport_objects(id_name))
    elif storage_type == "Material":
        seen = set()
        for name in users.material_objects(id_name):
            if name not in seen:
                seen.add(name)
                add_objects([name], nav="shader", material=id_name)
        for name in users.material_geometry_nodes(id_name):
            if name not in seen:
                seen.add(name)
                add_objects([name], nav="shader", material=id_name)
    elif storage_type == "Action":
        for name in users.action_objects(id_name):
            add_objects([name], nav="dopesheet", action=id_name)
    elif storage_type == "NodeTree":
        add_objects(users.node_group_viewport_objects(id_name))
    elif storage_type == "Texture":
        add_objects(users.texture_objects(id_name))
    elif storage_type == "Collection":
        add_objects(users.collection_viewport_objects(id_name))
    elif storage_type == "Sound":
        add_objects(users.sound_viewport_objects(id_name))
    elif storage_type == "Font":
        add_objects(users.font_viewport_objects(id_name))

    return targets


def _select_object(context, object_name):
    vl = context.view_layer
    if object_name not in vl.objects:
        return False
    for ob in vl.objects:
        ob.select_set(False)
    ob = vl.objects[object_name]
    ob.select_set(True)
    vl.objects.active = ob
    return True


def _frame_view3d(context):
    window, area, region = _find_area(context, "VIEW_3D")
    if not area or not region:
        return
    with context.temp_override(window=window, area=area, region=region):
        try:
            bpy.ops.view3d.view_selected()
        except RuntimeError:
            pass


def _outliner_show_active(context):
    window, area, region = _find_area(context, "OUTLINER")
    if not area or not region:
        return
    with context.temp_override(window=window, area=area, region=region):
        try:
            bpy.ops.outliner.show_active()
        except RuntimeError:
            pass


def _focus_shader_editor(context, material_name):
    material = bpy.data.materials.get(material_name)
    if not material:
        return
    ob = context.view_layer.objects.active
    if ob and hasattr(ob, "material_slots"):
        for i, slot in enumerate(ob.material_slots):
            if slot.material == material:
                ob.active_material_index = i
                break
    window, area, _region = _find_area(context, "NODE_EDITOR")
    if not area:
        return
    space = area.spaces.active
    if space.type != "NODE_EDITOR":
        return
    space.tree_type = "ShaderNodeTree"
    if hasattr(space, "shader_type"):
        space.shader_type = "OBJECT"
    if hasattr(space, "pin"):
        space.pin = False


def _focus_dopesheet(context, action_name):
    action = bpy.data.actions.get(action_name)
    window, area, _region = _find_area(context, "DOPESHEET_EDITOR")
    if not area:
        return
    space = area.spaces.active
    if space.type != "DOPESHEET_EDITOR":
        return
    if action and hasattr(space, "action"):
        space.action = action


def apply_target(context, target):
    """Select object and open the appropriate editor for one target."""
    if not _select_object(context, target["object"]):
        return False

    nav = target.get("nav", "viewport")
    if nav == "shader" and target.get("material"):
        _focus_shader_editor(context, target["material"])
    elif nav == "dopesheet" and target.get("action"):
        _focus_dopesheet(context, target["action"])

    if config.storage_navigate_frame_view:
        _frame_view3d(context)
    _outliner_show_active(context)
    return True
